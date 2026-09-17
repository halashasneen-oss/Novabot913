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

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"

WINDOWS = (
    {
        "name": "apr_2026",
        "window": "2026-04-01T00:00:00Z/2026-05-01T00:00:00Z",
        "data_start": date(2026, 3, 30),
        "data_end": date(2026, 5, 1),
        "test_start": datetime(2026, 4, 1, tzinfo=UTC),
        "test_end": datetime(2026, 5, 1, tzinfo=UTC),
        "months": ((2026, 3), (2026, 4)),
    },
    {
        "name": "jul_aug_2026",
        "window": "2026-07-17T00:00:00Z/2026-08-17T00:00:00Z",
        "data_start": date(2026, 7, 15),
        "data_end": date(2026, 8, 17),
        "test_start": datetime(2026, 7, 17, tzinfo=UTC),
        "test_end": datetime(2026, 8, 17, tzinfo=UTC),
        "months": ((2026, 7), (2026, 8)),
    },
)


def _append_month(
    h: Any,
    rows: list[tuple],
    symbol: str,
    year: int,
    month: int,
    data_start: date,
    data_end: date,
) -> None:
    start_ms = int(datetime.combine(data_start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(data_end, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    stamp = f"{year:04d}-{month:02d}"
    name = f"{symbol}-1m-{stamp}.zip"
    url = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{name}"
    for row in h._read_zip_rows(url):
        if row and row[0].isdigit():
            timestamp = int(row[0])
            if start_ms <= timestamp < end_ms:
                rows.append((timestamp, *(float(row[index]) for index in range(1, 6))))


def _download_symbol(
    h: Any,
    symbol: str,
    data_start: date,
    data_end: date,
    months: tuple[tuple[int, int], ...],
) -> list[tuple]:
    rows: list[tuple] = []
    for year, month in months:
        _append_month(h, rows, symbol, year, month, data_start, data_end)
    rows.sort(key=lambda item: item[0])
    return rows


def _prepare_window(
    h: Any,
    market_ref: Any,
    run_fn: Any,
    universe: tuple[str, ...],
    config: dict[str, Any],
) -> dict[str, list[tuple]]:
    data_start = config["data_start"]
    data_end = config["data_end"]
    test_start_ms = int(config["test_start"].timestamp() * 1000)
    test_end_ms = int(config["test_end"].timestamp() * 1000)

    h.SYMBOLS = universe
    h.DATA_START = data_start
    h.DATA_END = data_end
    h.TEST_START_MS = test_start_ms
    h.TEST_END_MS = test_end_ms
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    market_ref._premium_cache.clear()
    market_ref._funding_cache.clear()

    patched_globals = run_fn.__globals__
    patched_globals["SYMBOLS"] = universe
    patched_globals["TEST_START_MS"] = test_start_ms
    patched_globals["TEST_END_MS"] = test_end_ms

    raw: dict[str, list[tuple]] = {}
    for index, symbol in enumerate(universe, 1):
        raw[symbol] = _download_symbol(
            h,
            symbol,
            data_start,
            data_end,
            config["months"],
        )
        print(
            f"{config['name']} DATA {index:02d}/{len(universe)} {symbol} bars={len(raw[symbol])}",
            flush=True,
        )

    missing = [symbol for symbol, bars in raw.items() if not bars]
    if missing:
        raise RuntimeError(f"missing price data for {config['name']}: {missing}")
    return raw


def _run_window(
    h: Any,
    market_ref: Any,
    run_fn: Any,
    universe: tuple[str, ...],
    config: dict[str, Any],
) -> dict[str, Any]:
    raw = _prepare_window(h, market_ref, run_fn, universe, config)
    causal_filter = _causal_filter_factory(h, market_ref)
    result = _run_engine(h, run_fn, raw, causal_filter)
    summary = _summary(result)
    print(
        f"CANONICAL_{config['name'].upper()}=" + json.dumps(summary, sort_keys=True),
        flush=True,
    )
    return {
        "window": config["window"],
        "summary": summary,
        "trades": [_trade_view(item) for item in result["trades_detail"]],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="novabot913-additional-canonical-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]
        universe = tuple(strategy.UNIVERSE_10)
        run_fn = strategy.MODIFIED_RUN_HYBRID

        results = {
            config["name"]: _run_window(h, market_ref, run_fn, universe, config)
            for config in WINDOWS
        }
        report = {
            "phase": "canonical_strategy_additional_historical_validation",
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "strategy_parameters_modified": False,
            "universe": list(universe),
            "windows": results,
        }
        output = Path("additional_canonical_windows.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("ADDITIONAL_CANONICAL_WINDOWS=" + json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
