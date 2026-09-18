from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novabot913.strategy913r1_engine import install_r1_patch
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
    _trade_view,
)
from run_strategy913_ytd_protect import (
    TEST_START,
    UNIVERSE_10,
    _download_symbol_ytd,
    _install_snapshot_fallbacks,
    _load_snapshot,
    _monthly_realized,
    _sync_engine,
    _worst_trade,
)


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
    snapshot = _load_snapshot()
    test_end_ms = int(snapshot["snapshot_end_ms_exclusive"])
    test_start_ms = int(TEST_START.timestamp() * 1000)
    end_dt = datetime.fromtimestamp(test_end_ms / 1000, tz=UTC)

    with tempfile.TemporaryDirectory(prefix="novabot913-r1-ytd-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]

        baseline_run = strategy.MODIFIED_RUN_HYBRID
        r1_run = install_r1_patch(h)

        h._price_cache.clear()
        h._metrics_cache.clear()
        h._market_cache.clear()
        market_ref._premium_cache.clear()
        market_ref._funding_cache.clear()

        _install_snapshot_fallbacks(h, market_ref, snapshot, end_dt, test_end_ms)
        _sync_engine(h, baseline_run, test_start_ms, test_end_ms)
        _sync_engine(h, r1_run, test_start_ms, test_end_ms)

        raw: dict[str, list[tuple]] = {}
        for index, symbol in enumerate(UNIVERSE_10, 1):
            raw[symbol] = _download_symbol_ytd(
                h,
                snapshot,
                symbol,
                end_dt,
                test_end_ms,
            )
            print(
                f"YTD_DATA {index:02d}/{len(UNIVERSE_10)} "
                f"{symbol} bars={len(raw[symbol])}",
                flush=True,
            )

        missing = [symbol for symbol, bars in raw.items() if not bars]
        if missing:
            raise RuntimeError(f"missing YTD price data: {missing}")

        baseline_filter = _causal_filter_factory(h, market_ref)
        baseline = _run_engine(h, baseline_run, raw, baseline_filter)

        r1_filter = _causal_filter_factory(h, market_ref)
        r1 = _run_engine(h, r1_run, raw, r1_filter)

        baseline_summary = _summary(baseline)
        r1_summary = _summary(r1)

        report = {
            "phase": "strategy_913_r1_ytd_continuous",
            "research_only": True,
            "adopted": False,
            "continuous_account": True,
            "start_equity_usdt": 100.0,
            "test_start_utc": TEST_START.isoformat(),
            "test_end_utc_exclusive": end_dt.isoformat(),
            "last_completed_minute_only": True,
            "universe": list(UNIVERSE_10),
            "baseline": baseline_summary,
            "r1": r1_summary,
            "delta_r1_minus_baseline": _delta(baseline_summary, r1_summary),
            "baseline_monthly_realized": _monthly_realized(
                baseline["trades_detail"]
            ),
            "r1_monthly_realized": _monthly_realized(r1["trades_detail"]),
            "baseline_worst_trade": _worst_trade(baseline["trades_detail"]),
            "r1_worst_trade": _worst_trade(r1["trades_detail"]),
            "baseline_trades": [
                _trade_view(item) for item in baseline["trades_detail"]
            ],
            "r1_trades": [_trade_view(item) for item in r1["trades_detail"]],
            "r1_risk_audit": r1.get("risk_audit", []),
            "r1_regime_audit": r1.get("regime_audit", []),
            "r1_circuit_audit": r1.get("circuit_audit", []),
        }

        output = Path("strategy913r1_ytd.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "STRATEGY913R1_YTD=" + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
