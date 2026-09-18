from __future__ import annotations

import json
import tempfile
from pathlib import Path

from run_beast_three_mod_experiment import (
    UNIVERSE_10,
    _sync_run_globals,
    install_beast_patch,
)
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
)

BASELINE = {
    "start_equity": 100.0,
    "final_equity": 126.6958,
    "return_pct": 26.70,
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
}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="novabot913-beast-augsep-fast-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]
        beast_run = install_beast_patch(h)

        raw = strategy.prepare(strategy.UNIVERSE_10)
        _sync_run_globals(beast_run, h)

        causal_filter = _causal_filter_factory(h, market_ref)
        variant = _run_engine(h, beast_run, raw, causal_filter)
        variant_summary = _summary(variant)

        report = {
            "window": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
            "research_only": True,
            "canonical_strategy_modified": False,
            "universe": list(UNIVERSE_10),
            "baseline": BASELINE,
            "variant": variant_summary,
            "pipeline": variant_summary.get("pipeline", {}),
        }
        Path("beast_augsep_fast.json").write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print("BEAST_AUGSEP_FAST=" + json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
