from __future__ import annotations

import argparse
import json
import math
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from run_additional_canonical_windows import _prepare_window
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
)

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"
MINUTE_MS = 60_000
FIVE_MINUTES_MS = 300_000
NEXT_OPEN_EXITS = {"EARLY_FAILURE", "FOLLOWTHROUGH_15M"}

GENERIC_WINDOWS = {
    "apr_2026": {
        "name": "apr_2026",
        "window": "2026-04-01T00:00:00Z/2026-05-01T00:00:00Z",
        "data_start": date(2026, 3, 30),
        "data_end": date(2026, 5, 1),
        "test_start": datetime(2026, 4, 1, tzinfo=UTC),
        "test_end": datetime(2026, 5, 1, tzinfo=UTC),
        "months": ((2026, 3), (2026, 4)),
    },
    "jun_jul_2026": {
        "name": "jun_jul_2026",
        "window": "2026-06-17T00:00:00Z/2026-07-17T00:00:00Z",
        "data_start": date(2026, 6, 15),
        "data_end": date(2026, 7, 17),
        "test_start": datetime(2026, 6, 17, tzinfo=UTC),
        "test_end": datetime(2026, 7, 17, tzinfo=UTC),
        "months": ((2026, 6), (2026, 7)),
    },
    "jul_aug_2026": {
        "name": "jul_aug_2026",
        "window": "2026-07-17T00:00:00Z/2026-08-17T00:00:00Z",
        "data_start": date(2026, 7, 15),
        "data_end": date(2026, 8, 17),
        "test_start": datetime(2026, 7, 17, tzinfo=UTC),
        "test_end": datetime(2026, 8, 17, tzinfo=UTC),
        "months": ((2026, 7), (2026, 8)),
    },
}


def _logged_causal_filter(
    base_filter: Any,
    market_ref: Any,
    sink: dict[tuple[str, int, int], dict[str, Any]],
):
    def wrapped(
        symbol: str,
        timestamp: int,
        direction: int,
        btc_bars: Any,
        btc_index: Any,
    ) -> dict[str, Any]:
        result = base_filter(symbol, timestamp, direction, btc_bars, btc_index)
        if result.get("pass"):
            funding = market_ref._funding_before(symbol, timestamp)
            sink[(symbol, timestamp, direction)] = {
                "symbol": symbol,
                "filter_timestamp": timestamp,
                "direction": direction,
                "decision_ms": result.get("decision_ms"),
                "taker_timestamp": result.get("taker_timestamp"),
                "premium_timestamp": result.get("premium_timestamp"),
                "legacy_metric_timestamp": result.get("legacy_metric_timestamp"),
                "funding_timestamp": funding.get("timestamp") if funding else None,
                "checks": result.get("checks"),
            }
        return result

    return wrapped


def _prepare_named_window(
    name: str,
    modules: dict[str, Any],
) -> tuple[Any, dict[str, list[tuple]], str]:
    h = modules["tmp_hybrid_zec_score7plus_60d"]
    market_ref = modules["tmp_hybrid_archive_market"]

    if name == "may_2026":
        strategy = modules["tmp_followthrough15_may2026_top10"]
        raw = strategy._prepare()
        return strategy.MODIFIED_RUN_HYBRID, raw, "2026-05-01T00:00:00Z/2026-06-01T00:00:00Z"

    strategy = modules["tmp_followthrough15_30then10"]
    run_fn = strategy.MODIFIED_RUN_HYBRID
    universe = tuple(strategy.UNIVERSE_10)

    if name == "aug_sep_2026":
        raw = strategy.prepare(strategy.UNIVERSE_10)
        return run_fn, raw, "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z"

    config = GENERIC_WINDOWS[name]
    raw = _prepare_window(h, market_ref, run_fn, universe, config)
    return run_fn, raw, str(config["window"])


def _close_enough(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(left - right) <= tolerance


def _audit_trade(
    trade: dict[str, Any],
    raw: dict[str, list[tuple]],
    approvals: dict[tuple[str, int, int], dict[str, Any]],
    fee_rate: float,
    slippage: float,
) -> dict[str, Any]:
    held = int(trade["held_minutes"])
    exit_ts = int(trade["exit_ts"])
    next_open_exit = trade["reason"] in NEXT_OPEN_EXITS
    elapsed_minutes = held if next_open_exit else held - 1
    entry_ts = exit_ts - elapsed_minutes * MINUTE_MS
    filter_ts = entry_ts - MINUTE_MS
    direction = 1 if trade["direction"] == "LONG" else -1
    approval = approvals.get((trade["symbol"], filter_ts, direction))

    bars = {int(bar[0]): bar for bar in raw[trade["symbol"]]}
    entry_bar = bars.get(entry_ts)
    exit_bar = bars.get(exit_ts)

    expected_entry = None
    if entry_bar is not None:
        expected_entry = float(entry_bar[1]) * (1.0 + direction * slippage)

    notional = float(trade["margin"]) * float(trade["leverage"])
    expected_fee_each = notional * fee_rate
    gross = notional * direction * (float(trade["exit"]) / float(trade["entry"]) - 1.0)
    expected_net = gross - 2.0 * expected_fee_each

    temporal_checks = {
        "approval_exists": approval is not None,
        "decision_equals_entry_boundary": bool(approval and approval["decision_ms"] == entry_ts),
        "taker_interval_completed": bool(
            approval
            and approval["taker_timestamp"] is not None
            and approval["taker_timestamp"] + FIVE_MINUTES_MS <= approval["decision_ms"]
        ),
        "premium_interval_completed": bool(
            approval
            and approval["premium_timestamp"] is not None
            and approval["premium_timestamp"] + FIVE_MINUTES_MS <= approval["decision_ms"]
        ),
        "legacy_metric_not_future": bool(
            approval
            and approval["legacy_metric_timestamp"] is not None
            and approval["legacy_metric_timestamp"] <= filter_ts
        ),
        "funding_not_future": bool(
            approval
            and approval["funding_timestamp"] is not None
            and approval["funding_timestamp"] <= filter_ts
        ),
        "btc_decision_bar_completed": bool(approval and filter_ts + MINUTE_MS <= entry_ts),
    }
    execution_checks = {
        "entry_bar_exists": entry_bar is not None,
        "exit_bar_exists": exit_bar is not None,
        "entry_after_filter_by_one_minute": entry_ts == filter_ts + MINUTE_MS,
        "held_minutes_consistent": held >= 1
        and exit_ts - entry_ts == elapsed_minutes * MINUTE_MS,
        "entry_price_matches_open_plus_slippage": bool(
            expected_entry is not None
            and _close_enough(float(trade["entry"]), expected_entry, tolerance=1e-10)
        ),
        "net_matches_gross_minus_round_trip_fees": _close_enough(float(trade["net"]), expected_net),
    }
    checks = {**temporal_checks, **execution_checks}

    return {
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "score": trade["score"],
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "held_minutes": held,
        "reason": trade["reason"],
        "next_open_exit": next_open_exit,
        "entry": trade["entry"],
        "exit": trade["exit"],
        "margin": trade["margin"],
        "leverage": trade["leverage"],
        "notional": notional,
        "expected_fee_each": expected_fee_each,
        "reported_net": trade["net"],
        "expected_net": expected_net,
        "approval": approval,
        "checks": checks,
        "pass": all(checks.values()),
    }


def _audit_result(
    result: dict[str, Any],
    raw: dict[str, list[tuple]],
    approvals: dict[tuple[str, int, int], dict[str, Any]],
    h: Any,
) -> dict[str, Any]:
    audited_trades = [
        _audit_trade(trade, raw, approvals, float(h.FEE), float(h.SLIPPAGE))
        for trade in result["trades_detail"]
    ]

    expected_fees = sum(2.0 * item["expected_fee_each"] for item in audited_trades)
    expected_final = float(result["start_equity"]) + sum(
        float(item["reported_net"]) for item in audited_trades
    )
    wins = sum(1 for item in audited_trades if float(item["reported_net"]) > 0)
    losses = len(audited_trades) - wins
    gross_profit = sum(
        float(item["reported_net"]) for item in audited_trades if float(item["reported_net"]) > 0
    )
    gross_loss = -sum(
        float(item["reported_net"]) for item in audited_trades if float(item["reported_net"]) < 0
    )
    expected_pf = gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)

    portfolio_checks = {
        "all_trade_checks_pass": all(item["pass"] for item in audited_trades),
        "trade_count_matches": len(audited_trades) == int(result["trades"]),
        "wins_match": wins == int(result["wins"]),
        "losses_match": losses == int(result["losses"]),
        "fees_match": _close_enough(float(result["fees"]), round(expected_fees, 4), 1e-4),
        "final_equity_matches_trade_ledger": _close_enough(
            float(result["final_equity"]), round(expected_final, 4), 1e-4
        ),
        "return_matches_final_equity": _close_enough(
            float(result["return_pct"]),
            round((float(result["final_equity"]) / float(result["start_equity"]) - 1.0) * 100.0, 2),
            1e-8,
        ),
        "profit_factor_matches": _close_enough(
            float(result["profit_factor"]), round(expected_pf, 3), 1e-8
        ),
        "no_liquidations": int(result["liquidations"]) == 0,
    }

    failures = [
        {
            "symbol": item["symbol"],
            "entry_ts": item["entry_ts"],
            "failed_checks": [name for name, passed in item["checks"].items() if not passed],
        }
        for item in audited_trades
        if not item["pass"]
    ]

    return {
        "summary": _summary(result),
        "audited_trade_count": len(audited_trades),
        "approved_filter_records": len(approvals),
        "expected_total_fees": expected_fees,
        "expected_final_equity_from_trade_ledger": expected_final,
        "portfolio_checks": portfolio_checks,
        "failures": failures,
        "trades": audited_trades,
        "pass": all(portfolio_checks.values()) and not failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "window",
        choices=("apr_2026", "may_2026", "jun_jul_2026", "jul_aug_2026", "aug_sep_2026"),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix=f"novabot913-trade-audit-{args.window}-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]

        run_fn, raw, window = _prepare_named_window(args.window, modules)
        approvals: dict[tuple[str, int, int], dict[str, Any]] = {}
        base_filter = _causal_filter_factory(h, market_ref)
        audit_filter = _logged_causal_filter(base_filter, market_ref, approvals)
        result = _run_engine(h, run_fn, raw, audit_filter)
        audit = _audit_result(result, raw, approvals, h)

        report = {
            "phase": "canonical_strategy_trade_audit",
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "strategy_parameters_modified": False,
            "window_name": args.window,
            "window": window,
            "audit": audit,
        }
        output = Path(f"canonical_trade_audit_{args.window}.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("CANONICAL_TRADE_AUDIT=" + json.dumps(report, sort_keys=True), flush=True)
        if not audit["pass"]:
            raise SystemExit("canonical trade audit failed")


if __name__ == "__main__":
    main()
