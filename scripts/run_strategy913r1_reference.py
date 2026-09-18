from __future__ import annotations

import argparse
import json
import tempfile
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
from run_strategy913_ablation import UNIVERSE_10, WINDOW_CONFIGS, _prepare

from novabot913.strategy913r1_engine import install_r1_patch

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"


def _delta(baseline: dict[str, Any], r1: dict[str, Any]) -> dict[str, float]:
    keys = (
        "final_equity",
        "return_pct",
        "trades",
        "win_rate_pct",
        "profit_factor",
        "fees",
        "max_drawdown_pct",
    )
    return {
        key: round(float(r1[key]) - float(baseline[key]), 8)
        for key in keys
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window",
        required=True,
        choices=(
            "apr_2026",
            "may_2026",
            "jun_jul_2026",
            "jul_aug_2026",
            "aug_sep_2026",
        ),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="novabot913-r1-reference-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        r1_run = install_r1_patch(h)

        raw, baseline_run = _prepare(
            args.window,
            modules,
            {"r1": r1_run},
        )

        baseline_filter = _causal_filter_factory(h, market_ref)
        baseline = _run_engine(h, baseline_run, raw, baseline_filter)

        r1_filter = _causal_filter_factory(h, market_ref)
        r1 = _run_engine(h, r1_run, raw, r1_filter)

        baseline_summary = _summary(baseline)
        r1_summary = _summary(r1)

        window_label = (
            WINDOW_CONFIGS[args.window]["window"]
            if args.window in WINDOW_CONFIGS
            else {
                "may_2026": "2026-05-01T00:00:00Z/2026-06-01T00:00:00Z",
                "aug_sep_2026": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
            }[args.window]
        )

        report = {
            "phase": "strategy_913_r1_reference",
            "research_only": True,
            "adopted": False,
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "canonical_strategy_modified": False,
            "window_name": args.window,
            "window": window_label,
            "universe": list(UNIVERSE_10),
            "baseline": baseline_summary,
            "r1": r1_summary,
            "delta_r1_minus_baseline": _delta(baseline_summary, r1_summary),
            "baseline_trades": [_trade_view(item) for item in baseline["trades_detail"]],
            "r1_trades": [_trade_view(item) for item in r1["trades_detail"]],
            "r1_risk_audit": r1.get("risk_audit", []),
            "r1_regime_audit": r1.get("regime_audit", []),
            "r1_circuit_audit": r1.get("circuit_audit", []),
        }

        output = Path(f"strategy913r1_reference_{args.window}.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "STRATEGY913R1_REFERENCE=" + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
