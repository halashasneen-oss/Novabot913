from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

from run_canonical_trade_audit import NEXT_OPEN_EXITS, _prepare_named_window
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
)

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"
MINUTE_MS = 60_000
ALLOWED_EXIT_REASONS = {
    "EARLY_FAILURE",
    "FOLLOWTHROUGH_15M",
    "STOP",
    "TRAIL_STOP",
    "TIME_12H",
    "END",
    "LIQ_GAP",
}


def _close(left: float, right: float, *, abs_tol: float = 1e-8) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=abs_tol)


def _direction(trade: dict[str, Any]) -> int:
    return 1 if trade["direction"] == "LONG" else -1


def _entry_ts(trade: dict[str, Any]) -> int:
    held = int(trade["held_minutes"])
    exit_ts = int(trade["exit_ts"])
    elapsed = held if trade["reason"] in NEXT_OPEN_EXITS else held - 1
    return exit_ts - elapsed * MINUTE_MS


def _margin_fraction(score: int) -> float:
    return 0.50 if score >= 6 else 0.35


def _expected_leverage(score: int) -> int:
    return 75 if score >= 6 else 50


def _stop_risk(leverage: float) -> float:
    liqdist = max(0.002, 1.0 / leverage - 0.005)
    return min(0.028, max(0.006, liqdist * 0.55))


def _liq_distance(leverage: float) -> float:
    return max(0.003, 1.0 / leverage - 0.005)


def _trail_distance(risk: float, mfe: float) -> float | None:
    if mfe >= 6.0 * risk:
        return 0.35 * risk
    if mfe >= 4.0 * risk:
        return 0.50 * risk
    if mfe >= 2.5 * risk:
        return 0.75 * risk
    if mfe >= 1.5 * risk:
        return risk
    return None


def _exit_with_slippage(raw_exit: float, direction: int, slippage: float) -> float:
    return raw_exit * (1.0 - direction * slippage)


def _trade_record(trade: dict[str, Any], fee_rate: float) -> dict[str, Any]:
    leverage = float(trade["leverage"])
    margin = float(trade["margin"])
    notional = margin * leverage
    fee = notional * fee_rate
    direction = _direction(trade)
    gross = notional * direction * (float(trade["exit"]) / float(trade["entry"]) - 1.0)
    return {
        "trade": trade,
        "entry_ts": _entry_ts(trade),
        "exit_ts": int(trade["exit_ts"]),
        "direction": direction,
        "margin": margin,
        "leverage": leverage,
        "notional": notional,
        "entry_fee": fee,
        "exit_fee": fee,
        "gross": gross,
    }


def _resolve_entry_order(
    records: list[dict[str, Any]],
    wallet: float,
    fee_rate: float,
) -> list[dict[str, Any]] | None:
    if not records:
        return []
    for index, record in enumerate(records):
        trade = record["trade"]
        expected_margin = wallet * _margin_fraction(int(trade["score"]))
        if not _close(record["margin"], expected_margin, abs_tol=1e-7):
            continue
        next_wallet = wallet - record["notional"] * fee_rate
        remainder = records[:index] + records[index + 1 :]
        resolved = _resolve_entry_order(remainder, next_wallet, fee_rate)
        if resolved is not None:
            return [record, *resolved]
    return None


def _account_audit(result: dict[str, Any], h: Any) -> dict[str, Any]:
    fee_rate = float(h.FEE)
    records = [_trade_record(trade, fee_rate) for trade in result["trades_detail"]]
    timestamps = sorted({item["entry_ts"] for item in records} | {item["exit_ts"] for item in records})
    wallet = float(result["start_equity"])
    active: dict[int, dict[str, Any]] = {}
    checks: list[dict[str, Any]] = []
    max_concurrent = 0

    for ts in timestamps:
        next_open_exits = [
            item
            for item in records
            if item["exit_ts"] == ts and item["trade"]["reason"] in NEXT_OPEN_EXITS
        ]
        for item in next_open_exits:
            key = id(item)
            checks.append(
                {
                    "kind": "next_open_exit_is_active",
                    "timestamp": ts,
                    "symbol": item["trade"]["symbol"],
                    "pass": key in active,
                }
            )
            wallet += item["gross"] - item["exit_fee"]
            active.pop(key, None)

        entries = [item for item in records if item["entry_ts"] == ts]
        order = _resolve_entry_order(entries, wallet, fee_rate)
        checks.append(
            {
                "kind": "entry_order_resolves_from_wallet_sizing",
                "timestamp": ts,
                "symbols": [item["trade"]["symbol"] for item in entries],
                "pass": order is not None,
            }
        )
        if order is None:
            order = entries

        for item in order:
            trade = item["trade"]
            score = int(trade["score"])
            expected_margin = wallet * _margin_fraction(score)
            expected_leverage = _expected_leverage(score)
            used_margin = sum(position["margin"] for position in active.values())
            available = max(0.0, wallet - used_margin)
            entry_checks = {
                "margin_matches_wallet_fraction": _close(
                    item["margin"], expected_margin, abs_tol=1e-7
                ),
                "leverage_matches_score": _close(item["leverage"], expected_leverage),
                "margin_fits_available_wallet": item["margin"] <= available + 1e-9,
                "notional_is_margin_times_leverage": _close(
                    item["notional"], item["margin"] * item["leverage"]
                ),
            }
            checks.append(
                {
                    "kind": "entry_risk",
                    "timestamp": ts,
                    "symbol": trade["symbol"],
                    "score": score,
                    "wallet_before": wallet,
                    "used_margin_before": used_margin,
                    "available_before": available,
                    "reported_margin": item["margin"],
                    "expected_margin": expected_margin,
                    "reported_leverage": item["leverage"],
                    "expected_leverage": expected_leverage,
                    "checks": entry_checks,
                    "pass": all(entry_checks.values()),
                }
            )
            wallet -= item["entry_fee"]
            active[id(item)] = item
            max_concurrent = max(max_concurrent, len(active))

        regular_exits = [
            item
            for item in records
            if item["exit_ts"] == ts and item["trade"]["reason"] not in NEXT_OPEN_EXITS
        ]
        for item in regular_exits:
            key = id(item)
            checks.append(
                {
                    "kind": "regular_exit_is_active",
                    "timestamp": ts,
                    "symbol": item["trade"]["symbol"],
                    "pass": key in active,
                }
            )
            if item["trade"]["reason"] == "LIQ_GAP":
                wallet -= item["margin"]
            else:
                wallet += item["gross"] - item["exit_fee"]
            active.pop(key, None)

    portfolio_checks = {
        "all_account_events_pass": all(item.get("pass", True) for item in checks),
        "no_positions_left_open": not active,
        "final_wallet_matches": _close(
            wallet,
            float(result["final_equity"]),
            abs_tol=1e-4,
        ),
        "max_concurrent_matches": max_concurrent == int(result.get("max_concurrent", max_concurrent)),
    }
    return {
        "checks": checks,
        "portfolio_checks": portfolio_checks,
        "reconstructed_final_wallet": wallet,
        "reconstructed_max_concurrent": max_concurrent,
        "pass": all(portfolio_checks.values()),
    }


def _path_audit(
    trade: dict[str, Any],
    raw: dict[str, list[tuple]],
    h: Any,
    followthrough: Any,
) -> dict[str, Any]:
    symbol = str(trade["symbol"])
    direction = _direction(trade)
    leverage = float(trade["leverage"])
    entry = float(trade["entry"])
    entry_ts = _entry_ts(trade)
    exit_ts = int(trade["exit_ts"])
    slippage = float(h.SLIPPAGE)
    risk = _stop_risk(leverage)
    liqdist = _liq_distance(leverage)
    stop = entry * (1.0 - direction * risk)
    liq = entry * (1.0 - direction * liqdist)
    best_close = entry
    mfe = 0.0
    follow_mfe = 0.0
    wrong_side_count = 0
    pending_stop: float | None = None
    pending_exit_reason: str | None = None
    pending_exit_ts: int | None = None
    held = 0
    failures: list[str] = []
    trail_updates = 0

    bars = raw[symbol]
    bar_map = {int(bar[0]): bar for bar in bars}
    index_map = {int(bar[0]): index for index, bar in enumerate(bars)}
    breakout_level = float(trade["breakout_level"])
    expected_terminal_reason: str | None = None
    expected_terminal_exit: float | None = None
    expected_terminal_ts: int | None = None

    ts = entry_ts
    while ts <= exit_ts:
        if pending_stop is not None:
            previous_stop = stop
            if direction == 1:
                stop = max(stop, pending_stop)
                if stop < previous_stop - 1e-12:
                    failures.append("long_trailing_stop_loosened")
            else:
                stop = min(stop, pending_stop)
                if stop > previous_stop + 1e-12:
                    failures.append("short_trailing_stop_loosened")
            pending_stop = None

        if pending_exit_ts == ts:
            bar = bar_map.get(ts)
            if bar is None:
                failures.append("missing_next_open_exit_bar")
                break
            expected_terminal_reason = pending_exit_reason
            expected_terminal_ts = ts
            expected_terminal_exit = _exit_with_slippage(float(bar[1]), direction, slippage)
            break

        bar = bar_map.get(ts)
        if bar is None:
            failures.append("missing_managed_bar")
            break

        opn, high, low, close = (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))
        held += 1

        liq_gap = opn <= liq if direction == 1 else opn >= liq
        if liq_gap:
            expected_terminal_reason = "LIQ_GAP"
            expected_terminal_ts = ts
            expected_terminal_exit = opn
            break

        stop_hit = low <= stop if direction == 1 else high >= stop
        if stop_hit:
            raw_exit = min(stop, opn) if direction == 1 else max(stop, opn)
            expected_terminal_reason = "TRAIL_STOP" if mfe >= 1.5 * risk else "STOP"
            expected_terminal_ts = ts
            expected_terminal_exit = _exit_with_slippage(raw_exit, direction, slippage)
            break

        if direction == 1:
            current_follow_mfe = high / entry - 1.0
        else:
            current_follow_mfe = entry / low - 1.0
        follow_mfe = max(follow_mfe, current_follow_mfe)

        if held == int(followthrough.FOLLOWTHROUGH_MINUTE):
            close_move = direction * (close / entry - 1.0)
            if close_move < float(followthrough.MIN_CLOSE_MOVE) and follow_mfe < float(
                followthrough.MIN_MFE
            ):
                pending_exit_reason = "FOLLOWTHROUGH_15M"
                pending_exit_ts = ts + MINUTE_MS

        if direction == 1:
            best_close = max(best_close, close)
            current_mfe = best_close / entry - 1.0
        else:
            best_close = min(best_close, close)
            current_mfe = entry / best_close - 1.0
        mfe = max(mfe, current_mfe)
        distance = _trail_distance(risk, mfe)
        if distance is not None:
            be = entry * (1.0 + direction * float(h.BE_LOCK))
            if direction == 1:
                trail = best_close * (1.0 - distance)
                pending_stop = max(be, trail)
            else:
                trail = best_close * (1.0 + distance)
                pending_stop = min(be, trail)
            trail_updates += 1

        if held <= int(h.EARLY_FAILURE_MINUTES):
            wrong_side = close < breakout_level if direction == 1 else close > breakout_level
            bar_index = index_map.get(ts)
            vote_ok = bar_index is not None and h.s7._tf_vote(bars, bar_index, direction, 1)
            if wrong_side and not vote_ok:
                wrong_side_count += 1
            else:
                wrong_side_count = 0
            if wrong_side_count >= 2:
                pending_exit_reason = "EARLY_FAILURE"
                pending_exit_ts = ts + MINUTE_MS

        if held >= int(h.MAX_HOLD_MINUTES):
            expected_terminal_reason = "TIME_12H"
            expected_terminal_ts = ts
            expected_terminal_exit = _exit_with_slippage(close, direction, slippage)
            break

        if ts == exit_ts:
            if trade["reason"] == "END":
                expected_terminal_reason = "END"
                expected_terminal_ts = ts
                expected_terminal_exit = _exit_with_slippage(close, direction, slippage)
            else:
                failures.append("reported_exit_without_replayed_trigger")
            break

        ts += MINUTE_MS

    if expected_terminal_reason != trade["reason"]:
        failures.append("exit_reason_mismatch")
    if expected_terminal_ts != exit_ts:
        failures.append("exit_timestamp_mismatch")
    if expected_terminal_exit is None or not _close(
        expected_terminal_exit,
        float(trade["exit"]),
        abs_tol=1e-10,
    ):
        failures.append("exit_price_mismatch")
    if held != int(trade["held_minutes"]):
        failures.append("held_minutes_mismatch")

    initial_geometry_checks = {
        "leverage_matches_score": _close(leverage, _expected_leverage(int(trade["score"]))),
        "stop_risk_matches_frozen_formula": _close(risk, float(h._stop_risk(leverage))),
        "initial_stop_inside_liquidation": stop > liq if direction == 1 else stop < liq,
    }
    if not all(initial_geometry_checks.values()):
        failures.append("initial_risk_geometry_mismatch")

    return {
        "symbol": symbol,
        "direction": trade["direction"],
        "score": trade["score"],
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "reason": trade["reason"],
        "held_minutes": trade["held_minutes"],
        "risk_pct": risk * 100.0,
        "liquidation_distance_pct": liqdist * 100.0,
        "trail_updates": trail_updates,
        "replayed_reason": expected_terminal_reason,
        "replayed_exit_ts": expected_terminal_ts,
        "replayed_exit": expected_terminal_exit,
        "initial_geometry_checks": initial_geometry_checks,
        "failures": failures,
        "pass": not failures,
    }


def _static_audit(h: Any, followthrough: Any) -> dict[str, Any]:
    checks = {
        "fee_each_side_5bps": _close(float(h.FEE), 0.0005),
        "slippage_each_side_2bps": _close(float(h.SLIPPAGE), 0.0002),
        "score5_params": h._params(5) == (0.35, 50),
        "score6_params": h._params(6) == (0.50, 75),
        "score7_params": h._params(7) == (0.50, 75),
        "stop_risk_50x": _close(float(h._stop_risk(50)), 0.00825),
        "stop_risk_75x": _close(float(h._stop_risk(75)), 0.006),
        "early_failure_first_10m": int(h.EARLY_FAILURE_MINUTES) == 10,
        "max_hold_12h": int(h.MAX_HOLD_MINUTES) == 720,
        "break_even_lock_15bps": _close(float(h.BE_LOCK), 0.0015),
        "followthrough_minute_15": int(followthrough.FOLLOWTHROUGH_MINUTE) == 15,
        "followthrough_close_move_30bps": _close(float(followthrough.MIN_CLOSE_MOVE), 0.0030),
        "followthrough_mfe_77bps": _close(float(followthrough.MIN_MFE), 0.0077),
        "trail_at_1_5r": _close(float(h._progressive_trail(0.01, 0.015)), 0.01),
        "trail_at_2_5r": _close(float(h._progressive_trail(0.01, 0.025)), 0.0075),
        "trail_at_4r": _close(float(h._progressive_trail(0.01, 0.04)), 0.005),
        "trail_at_6r": _close(float(h._progressive_trail(0.01, 0.06)), 0.0035),
    }
    return {"checks": checks, "pass": all(checks.values())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "window",
        choices=("apr_2026", "may_2026", "jun_jul_2026", "jul_aug_2026", "aug_sep_2026"),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix=f"novabot913-risk-audit-{args.window}-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        followthrough = (
            modules["tmp_followthrough15_may2026_top10"]
            if args.window == "may_2026"
            else modules["tmp_followthrough15_30then10"]
        )

        run_fn, raw, window = _prepare_named_window(args.window, modules)
        causal_filter = _causal_filter_factory(h, market_ref)
        result = _run_engine(h, run_fn, raw, causal_filter)

        static = _static_audit(h, followthrough)
        account = _account_audit(result, h)
        paths = [
            _path_audit(trade, raw, h, followthrough) for trade in result["trades_detail"]
        ]
        exit_reason_checks = {
            reason: reason in ALLOWED_EXIT_REASONS for reason in result["exit_reasons"]
        }
        overall = (
            static["pass"]
            and account["pass"]
            and all(item["pass"] for item in paths)
            and all(exit_reason_checks.values())
            and int(result["liquidations"]) == 0
        )

        report = {
            "phase": "canonical_execution_risk_constraint_audit",
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "strategy_parameters_modified": False,
            "window_name": args.window,
            "window": window,
            "trade_count": len(paths),
            "static_rules": static,
            "account_path": account,
            "trade_paths": paths,
            "exit_reason_checks": exit_reason_checks,
            "liquidations": result["liquidations"],
            "summary": {
                "start_equity": result["start_equity"],
                "final_equity": result["final_equity"],
                "return_pct": result["return_pct"],
                "trades": result["trades"],
                "max_concurrent": result["max_concurrent"],
                "exit_reasons": result["exit_reasons"],
            },
            "pass": overall,
        }
        output = Path(f"execution_risk_audit_{args.window}.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("EXECUTION_RISK_AUDIT=" + json.dumps(report, sort_keys=True), flush=True)
        if not overall:
            raise SystemExit("execution/risk constraint audit failed")


if __name__ == "__main__":
    main()
