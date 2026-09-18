from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
    _trade_view,
)
from run_strategy913_protection_sweep import (
    UNIVERSE_10,
    _memoized_filter,
    _prepare_generic,
    install_protection_patch,
)

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"

CANDIDATES: dict[str, tuple[float, float, float]] = {
    "robust_be_0p80": (0.50, 0.10, 0.80),
    "high_return_be_1p20": (0.50, 0.20, 1.20),
    "original_protection": (0.50, 0.25, 1.00),
}

OOS_WINDOWS: dict[str, dict[str, Any]] = {
    "jan_2026": {
        "window": "2026-01-01T00:00:00Z/2026-02-01T00:00:00Z",
        "data_start": date(2025, 12, 30),
        "data_end": date(2026, 2, 1),
        "test_start": datetime(2026, 1, 1, tzinfo=UTC),
        "test_end": datetime(2026, 2, 1, tzinfo=UTC),
        "months": ((2025, 12), (2026, 1)),
    },
    "feb_2026": {
        "window": "2026-02-01T00:00:00Z/2026-03-01T00:00:00Z",
        "data_start": date(2026, 1, 30),
        "data_end": date(2026, 3, 1),
        "test_start": datetime(2026, 2, 1, tzinfo=UTC),
        "test_end": datetime(2026, 3, 1, tzinfo=UTC),
        "months": ((2026, 1), (2026, 2)),
    },
    "mar_2026": {
        "window": "2026-03-01T00:00:00Z/2026-04-01T00:00:00Z",
        "data_start": date(2026, 2, 27),
        "data_end": date(2026, 4, 1),
        "test_start": datetime(2026, 3, 1, tzinfo=UTC),
        "test_end": datetime(2026, 4, 1, tzinfo=UTC),
        "months": ((2026, 2), (2026, 3)),
    },
}


def _concentration(trades: list[dict[str, Any]]) -> dict[str, float]:
    positive = [float(trade["net"]) for trade in trades if float(trade["net"]) > 0.0]
    all_net = [float(trade["net"]) for trade in trades]
    total_positive = sum(positive)
    largest_win = max(positive, default=0.0)
    return {
        "largest_win_net": round(largest_win, 6),
        "largest_win_share_pct": round(
            (largest_win / total_positive * 100.0) if total_positive else 0.0,
            2,
        ),
        "worst_trade_net": round(min(all_net, default=0.0), 6),
        "total_positive_net": round(total_positive, 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", required=True, choices=tuple(OOS_WINDOWS))
    args = parser.parse_args()
    config = OOS_WINDOWS[args.window]

    with tempfile.TemporaryDirectory(prefix="novabot913-protection-oos-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]

        candidate_runs: dict[str, Any] = {}
        run_fns: list[Any] = []
        for name, values in CANDIDATES.items():
            protect_trigger_r, protect_stop_r, break_even_trigger_r = values
            run_fn = install_protection_patch(
                h,
                protect_trigger_r,
                protect_stop_r,
                break_even_trigger_r,
            )
            candidate_runs[name] = run_fn
            run_fns.append(run_fn)

        baseline_run = strategy.MODIFIED_RUN_HYBRID
        raw = _prepare_generic(
            h,
            market_ref,
            (baseline_run, *run_fns),
            config,
        )

        base_filter = _causal_filter_factory(h, market_ref)
        shared_filter = _memoized_filter(base_filter)

        baseline = _run_engine(h, baseline_run, raw, shared_filter)
        baseline_summary = _summary(baseline)
        baseline_trades = [_trade_view(trade) for trade in baseline["trades_detail"]]

        results: dict[str, Any] = {}
        for name, run_fn in candidate_runs.items():
            variant = _run_engine(h, run_fn, raw, shared_filter)
            summary = _summary(variant)
            trades = [_trade_view(trade) for trade in variant["trades_detail"]]
            values = CANDIDATES[name]
            results[name] = {
                "protect_trigger_r": values[0],
                "protect_stop_r": values[1],
                "break_even_trigger_r": values[2],
                "summary": summary,
                "concentration": _concentration(trades),
                "trades": trades,
            }
            print(
                f"OOS {args.window} {name} "
                f"return={summary['return_pct']} "
                f"dd={summary['max_drawdown_pct']} "
                f"pf={summary['profit_factor']}",
                flush=True,
            )

        report = {
            "phase": "strategy_913_early_profit_protection_oos",
            "research_only": True,
            "adopted": False,
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "canonical_strategy_modified": False,
            "selection_windows": [
                "apr_2026",
                "may_2026",
                "jun_jul_2026",
                "jul_aug_2026",
                "aug_sep_2026",
            ],
            "oos_window_name": args.window,
            "oos_window": config["window"],
            "universe": list(UNIVERSE_10),
            "candidates": {
                name: {
                    "protect_trigger_r": values[0],
                    "protect_stop_r": values[1],
                    "break_even_trigger_r": values[2],
                }
                for name, values in CANDIDATES.items()
            },
            "baseline": {
                "summary": baseline_summary,
                "concentration": _concentration(baseline_trades),
                "trades": baseline_trades,
            },
            "results": results,
        }

        output = Path(f"strategy913_protection_oos_{args.window}.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "STRATEGY913_PROTECTION_OOS=" + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
