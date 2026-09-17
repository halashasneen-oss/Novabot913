from __future__ import annotations

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

from novabot913.post_causal_analysis import BASELINE_CAUSAL_SHA
from novabot913.post_causal_experiments import long_only_market_filter

WINDOW = "2026-06-17T00:00:00Z/2026-07-17T00:00:00Z"
DATA_START = date(2026, 6, 15)
DATA_END = date(2026, 7, 17)
TEST_START_MS = int(datetime(2026, 6, 17, tzinfo=UTC).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 7, 17, tzinfo=UTC).timestamp() * 1000)
MONTHS = ((2026, 6), (2026, 7))


def _append_month(h: Any, rows: list[tuple], symbol: str, year: int, month: int) -> None:
    start_ms = int(
        datetime.combine(DATA_START, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    end_ms = int(
        datetime.combine(DATA_END, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    stamp = f"{year:04d}-{month:02d}"
    name = f"{symbol}-1m-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        f"{symbol}/1m/{name}"
    )
    for row in h._read_zip_rows(url):
        if row and row[0].isdigit():
            timestamp = int(row[0])
            if start_ms <= timestamp < end_ms:
                rows.append((timestamp, *(float(row[index]) for index in range(1, 6))))


def _download_symbol_oos(h: Any, symbol: str) -> list[tuple]:
    if symbol in h._price_cache:
        return h._price_cache[symbol]

    rows: list[tuple] = []
    for year, month in MONTHS:
        _append_month(h, rows, symbol, year, month)
    rows.sort(key=lambda item: item[0])
    h._price_cache[symbol] = rows
    return rows


def _prepare_oos(h: Any, market_ref: Any, run_fn: Any, universe: tuple[str, ...]):
    h.SYMBOLS = universe
    h.DATA_START = DATA_START
    h.DATA_END = DATA_END
    h.TEST_START_MS = TEST_START_MS
    h.TEST_END_MS = TEST_END_MS
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    market_ref._premium_cache.clear()
    market_ref._funding_cache.clear()

    patched_globals = run_fn.__globals__
    patched_globals["SYMBOLS"] = universe
    patched_globals["TEST_START_MS"] = TEST_START_MS
    patched_globals["TEST_END_MS"] = TEST_END_MS

    raw: dict[str, list[tuple]] = {}
    for index, symbol in enumerate(universe, 1):
        raw[symbol] = _download_symbol_oos(h, symbol)
        print(f"DATA {index:02d}/{len(universe)} {symbol} bars={len(raw[symbol])}", flush=True)

    missing = [symbol for symbol, bars in raw.items() if not bars]
    if missing:
        raise RuntimeError(f"missing OOS price data: {missing}")
    return raw


def _delta(baseline: dict[str, Any], variant: dict[str, Any]) -> dict[str, float]:
    keys = (
        "final_equity",
        "return_pct",
        "trades",
        "win_rate_pct",
        "profit_factor",
        "fees",
        "max_drawdown_pct",
    )
    return {key: round(float(variant[key]) - float(baseline[key]), 8) for key in keys}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="novabot913-long-only-oos-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]
        universe = tuple(strategy.UNIVERSE_10)
        run_fn = strategy.MODIFIED_RUN_HYBRID
        raw = _prepare_oos(h, market_ref, run_fn, universe)

        baseline_filter = _causal_filter_factory(h, market_ref)
        baseline = _run_engine(h, run_fn, raw, baseline_filter)
        baseline_summary = _summary(baseline)
        print("OOS_BASELINE=" + json.dumps(baseline_summary, sort_keys=True), flush=True)

        variant_filter = long_only_market_filter(_causal_filter_factory(h, market_ref))
        variant = _run_engine(h, run_fn, raw, variant_filter)
        variant_summary = _summary(variant)
        print("OOS_LONG_ONLY=" + json.dumps(variant_summary, sort_keys=True), flush=True)

        report = {
            "phase": "post_causal_research_phase_3_oos_direction_validation",
            "window": WINDOW,
            "baseline_causal_sha": BASELINE_CAUSAL_SHA,
            "research_only": True,
            "adopted": False,
            "hypothesis_frozen_before_oos": (
                "Reject otherwise-approved SHORT decisions; retain LONG decisions."
            ),
            "variant_change": "LONG_ONLY market-filter gate",
            "strategy_parameters_modified": False,
            "universe": list(universe),
            "baseline": baseline_summary,
            "variant": variant_summary,
            "delta_variant_minus_baseline": _delta(baseline_summary, variant_summary),
            "baseline_trades": [_trade_view(item) for item in baseline["trades_detail"]],
            "variant_trades": [_trade_view(item) for item in variant["trades_detail"]],
            "oos_note": (
                "This June 17-July 17 window is non-overlapping with May and Aug-Sep "
                "diagnostic windows and precedes the July 17-Sep 16 60-day reference window."
            ),
        }
        output = Path("post_causal_long_only_oos.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("POST_CAUSAL_LONG_ONLY_OOS=" + json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
