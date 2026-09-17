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

BASELINE_ARTIFACT_RUN_ID = 35240653406
BASELINE_ARTIFACT_DIGEST = "sha256:77ace4c097aecbcf3a6a550d34427ad521b0553692f4e4266eaf80474e57c814"

FROZEN_BASELINE_SUMMARIES = {
    "may_2026": {
        "start_equity": 100.0,
        "final_equity": 125.569,
        "return_pct": 25.57,
        "trades": 9,
        "wins": 3,
        "losses": 6,
        "win_rate_pct": 33.33,
        "profit_factor": 1.27,
        "fees": 28.1534,
        "peak_marked_equity": 163.085,
        "trough_marked_equity": 67.0091,
        "max_drawdown_pct": 47.1,
        "liquidations": 0,
        "exit_reasons": {
            "EARLY_FAILURE": 4,
            "FOLLOWTHROUGH_15M": 1,
            "STOP": 1,
            "TRAIL_STOP": 3,
        },
        "pipeline": {
            "4h_setups": 283,
            "early_failure_exits": 4,
            "external_approvals": 10,
            "external_rejections": 161,
            "followthrough_15m_exits": 1,
            "margin_rejections": 1,
            "micro_confirmations": 81,
            "retest_expired": 40,
            "retest_failures": 31,
            "retests_seen": 71,
        },
    },
    "aug_sep_2026": {
        "start_equity": 100.0,
        "final_equity": 126.6958,
        "return_pct": 26.7,
        "trades": 22,
        "wins": 11,
        "losses": 11,
        "win_rate_pct": 50.0,
        "profit_factor": 1.057,
        "fees": 134.7059,
        "peak_marked_equity": 388.0973,
        "trough_marked_equity": 66.9401,
        "max_drawdown_pct": 67.35,
        "liquidations": 0,
        "exit_reasons": {
            "EARLY_FAILURE": 3,
            "FOLLOWTHROUGH_15M": 4,
            "STOP": 6,
            "TRAIL_STOP": 9,
        },
        "pipeline": {
            "4h_setups": 278,
            "early_failure_exits": 3,
            "external_approvals": 23,
            "external_rejections": 289,
            "followthrough_15m_exits": 4,
            "margin_rejections": 1,
            "micro_confirmations": 119,
            "retest_expired": 60,
            "retest_failures": 36,
            "retests_seen": 98,
        },
    },
}


def _window_result(
    h: Any,
    market_ref: Any,
    run_fn: Any,
    raw: dict[str, list[tuple]],
    window: str,
) -> dict[str, Any]:
    baseline_summary = FROZEN_BASELINE_SUMMARIES[window]

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
        "baseline_source": {
            "causal_sha": BASELINE_CAUSAL_SHA,
            "artifact_run_id": BASELINE_ARTIFACT_RUN_ID,
            "artifact_digest": BASELINE_ARTIFACT_DIGEST,
        },
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
            "baseline_artifact_run_id": BASELINE_ARTIFACT_RUN_ID,
            "baseline_artifact_digest": BASELINE_ARTIFACT_DIGEST,
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
