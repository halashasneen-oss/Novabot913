from __future__ import annotations

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

from novabot913.post_causal_analysis import BASELINE_CAUSAL_SHA
from novabot913.post_causal_experiments import long_only_market_filter

EXPECTED_BASELINES = {
    "may_2026": {
        "final_equity": 125.569,
        "return_pct": 25.57,
        "trades": 9,
        "profit_factor": 1.27,
        "max_drawdown_pct": 47.1,
    },
    "aug_sep_2026": {
        "final_equity": 126.6958,
        "return_pct": 26.7,
        "trades": 22,
        "profit_factor": 1.057,
        "max_drawdown_pct": 67.35,
    },
}


def _assert_frozen_baseline(window: str, summary: dict[str, Any]) -> None:
    expected = EXPECTED_BASELINES[window]
    observed = {key: summary.get(key) for key in expected}
    if observed != expected:
        raise RuntimeError(
            f"frozen corrected baseline drifted for {window}: "
            f"expected={expected}, observed={observed}"
        )


def _window_result(
    h: Any,
    market_ref: Any,
    run_fn: Any,
    raw: dict[str, list[tuple]],
    window: str,
) -> dict[str, Any]:
    baseline_filter = _causal_filter_factory(h, market_ref)
    baseline = _run_engine(h, run_fn, raw, baseline_filter)
    baseline_summary = _summary(baseline)
    _assert_frozen_baseline(window, baseline_summary)

    variant_filter = long_only_market_filter(_causal_filter_factory(h, market_ref))
    variant = _run_engine(h, run_fn, raw, variant_filter)
    variant_summary = _summary(variant)

    delta_keys = (
        "final_equity",
        "return_pct",
        "trades",
        "win_rate_pct",
        "profit_factor",
        "fees",
        "max_drawdown_pct",
    )
    delta = {
        key: round(float(variant_summary[key]) - float(baseline_summary[key]), 8)
        for key in delta_keys
    }

    return {
        "baseline": baseline_summary,
        "variant": variant_summary,
        "delta_variant_minus_baseline": delta,
        "baseline_trades": [_trade_view(item) for item in baseline["trades_detail"]],
        "variant_trades": [_trade_view(item) for item in variant["trades_detail"]],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="novabot913-long-only-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]

        may = modules["tmp_followthrough15_may2026_top10"]
        may_result = _window_result(
            h,
            market_ref,
            may.MODIFIED_RUN_HYBRID,
            may._prepare(),
            "may_2026",
        )
        print(
            "LONG_ONLY_MAY=" + json.dumps(may_result["variant"], sort_keys=True),
            flush=True,
        )

        aug = modules["tmp_followthrough15_30then10"]
        aug_result = _window_result(
            h,
            market_ref,
            aug.MODIFIED_RUN_HYBRID,
            aug.prepare(aug.UNIVERSE_10),
            "aug_sep_2026",
        )
        print(
            "LONG_ONLY_AUGSEP=" + json.dumps(aug_result["variant"], sort_keys=True),
            flush=True,
        )

        report = {
            "phase": "post_causal_research_phase_2_direction_experiment",
            "baseline_causal_sha": BASELINE_CAUSAL_SHA,
            "research_only": True,
            "adopted": False,
            "hypothesis": "Reject otherwise-approved SHORT decisions; retain LONG decisions.",
            "baseline_strategy_modified": False,
            "variant_change": "LONG_ONLY market-filter gate",
            "may_2026": may_result,
            "aug_sep_2026": aug_result,
        }
        output = Path("post_causal_long_only_experiment.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("POST_CAUSAL_LONG_ONLY=" + json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
