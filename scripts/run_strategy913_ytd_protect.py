from __future__ import annotations

import json
import tempfile
from datetime import UTC, date, datetime, timedelta
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
from run_strategy913_ablation import UNIVERSE_10, install_beast_patch

TEST_START = datetime(2026, 1, 1, tzinfo=UTC)
DATA_START = date(2025, 12, 30)
SNAPSHOT_FILES = (
    "research_data/live_2026-09-18_part1.json",
    "research_data/live_2026-09-18_part2.json",
    "research_data/live_2026-09-18_part3.json",
    "research_data/live_2026-09-18_part4.json",
)


def _next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def _load_snapshot() -> dict[str, Any]:
    merged: dict[str, Any] = {
        "snapshot_start_ms": None,
        "snapshot_end_ms_exclusive": None,
        "symbols": {},
    }
    root = Path(__file__).resolve().parents[1]
    for relative in SNAPSHOT_FILES:
        payload = json.loads((root / relative).read_text(encoding="utf-8"))
        start_ms = int(payload["snapshot_start_ms"])
        end_ms = int(payload["snapshot_end_ms_exclusive"])
        if merged["snapshot_start_ms"] is None:
            merged["snapshot_start_ms"] = start_ms
            merged["snapshot_end_ms_exclusive"] = end_ms
        elif (
            start_ms != merged["snapshot_start_ms"]
            or end_ms != merged["snapshot_end_ms_exclusive"]
        ):
            raise RuntimeError("live snapshot files do not share the same time bounds")
        merged["symbols"].update(payload["symbols"])

    missing = [symbol for symbol in UNIVERSE_10 if symbol not in merged["symbols"]]
    if missing:
        raise RuntimeError(f"missing live snapshot symbols: {missing}")
    return merged


def _append_zip_rows(
    h: Any,
    rows: list[tuple],
    url: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    try:
        source = h._read_zip_rows(url)
    except Exception:
        return False

    for row in source:
        if not row or not row[0].isdigit():
            continue
        timestamp = int(row[0])
        if start_ms <= timestamp < end_ms:
            rows.append(
                (
                    timestamp,
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                )
            )
    return True


def _append_monthly_price(
    h: Any,
    rows: list[tuple],
    symbol: str,
    month: date,
    start_ms: int,
    end_ms: int,
) -> None:
    stamp = f"{month.year:04d}-{month.month:02d}"
    name = f"{symbol}-1m-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        f"{symbol}/1m/{name}"
    )
    if not _append_zip_rows(h, rows, url, start_ms, end_ms):
        raise RuntimeError(f"missing monthly price archive: {symbol} {stamp}")


def _append_daily_price(
    h: Any,
    rows: list[tuple],
    symbol: str,
    day: date,
    start_ms: int,
    end_ms: int,
) -> None:
    stamp = day.isoformat()
    name = f"{symbol}-1m-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/daily/klines/"
        f"{symbol}/1m/{name}"
    )
    if not _append_zip_rows(h, rows, url, start_ms, end_ms):
        raise RuntimeError(f"missing daily price archive: {symbol} {stamp}")


def _snapshot_price_rows(
    snapshot: dict[str, Any],
    symbol: str,
    end_ms: int,
) -> list[tuple]:
    rows: list[tuple] = []
    for row in snapshot["symbols"][symbol]["price_1m"]:
        timestamp = int(row[0])
        if timestamp + 60_000 > end_ms:
            continue
        rows.append(
            (
                timestamp,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
        )
    return rows


def _download_symbol_ytd(
    h: Any,
    snapshot: dict[str, Any],
    symbol: str,
    end_dt: datetime,
    end_ms: int,
) -> list[tuple]:
    start_ms = int(
        datetime.combine(DATA_START, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    rows: list[tuple] = []

    current_month = _month_start(DATA_START)
    snapshot_month = _month_start(end_dt.date())
    while current_month < snapshot_month:
        _append_monthly_price(h, rows, symbol, current_month, start_ms, end_ms)
        current_month = _next_month(current_month)

    current_day = snapshot_month
    while current_day < end_dt.date():
        _append_daily_price(h, rows, symbol, current_day, start_ms, end_ms)
        current_day += timedelta(days=1)

    rows.extend(_snapshot_price_rows(snapshot, symbol, end_ms))
    deduplicated = {bar[0]: bar for bar in rows}
    return [deduplicated[timestamp] for timestamp in sorted(deduplicated)]


def _snapshot_metric_rows(snapshot: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
    data = snapshot["symbols"][symbol]
    oi_by_ts = {int(item["timestamp"]): item for item in data["oi_5m"]}
    taker_by_ts = {int(item["timestamp"]): item for item in data["taker_5m"]}
    top_by_ts = {int(item["timestamp"]): item for item in data["top_position_5m"]}
    timestamps = sorted(set(oi_by_ts) & set(taker_by_ts) & set(top_by_ts))

    return [
        {
            "timestamp": timestamp,
            "oi": float(oi_by_ts[timestamp]["sumOpenInterest"]),
            "taker_ratio": float(taker_by_ts[timestamp]["buySellRatio"]),
            "top_ratio": float(top_by_ts[timestamp]["longShortRatio"]),
        }
        for timestamp in timestamps
    ]


def _snapshot_premium_rows(snapshot: dict[str, Any], symbol: str, end_ms: int):
    return [
        {"timestamp": int(row[0]), "close": float(row[4])}
        for row in snapshot["symbols"][symbol]["premium_5m"]
        if int(row[0]) + 5 * 60_000 <= end_ms
    ]


def _snapshot_funding_rows(snapshot: dict[str, Any], symbol: str):
    return [
        {
            "timestamp": int(item["fundingTime"]),
            "rate": float(item["fundingRate"]),
        }
        for item in snapshot["symbols"][symbol]["funding"]
    ]


def _install_snapshot_fallbacks(
    h: Any,
    market_ref: Any,
    snapshot: dict[str, Any],
    end_dt: datetime,
    end_ms: int,
) -> None:
    snapshot_day = end_dt.date()
    archive_metrics_day = h._metrics_day
    archive_premium_day = market_ref._premium_day
    archive_funding_day = market_ref._funding_day

    metric_cache: dict[str, list[dict[str, Any]]] = {}
    premium_cache: dict[str, list[dict[str, Any]]] = {}
    funding_cache: dict[str, list[dict[str, Any]]] = {}

    def metrics_day(symbol: str, day: date):
        rows = archive_metrics_day(symbol, day)
        if rows or day != snapshot_day:
            return rows
        if symbol not in metric_cache:
            metric_cache[symbol] = _snapshot_metric_rows(snapshot, symbol)
        return metric_cache[symbol]

    def premium_day(symbol: str, day: date):
        rows = archive_premium_day(symbol, day)
        if rows or day != snapshot_day:
            return rows
        if symbol not in premium_cache:
            premium_cache[symbol] = _snapshot_premium_rows(snapshot, symbol, end_ms)
        return premium_cache[symbol]

    def funding_day(symbol: str, day: date):
        rows = archive_funding_day(symbol, day)
        if rows or day != snapshot_day:
            return rows
        if symbol not in funding_cache:
            funding_cache[symbol] = _snapshot_funding_rows(snapshot, symbol)
        return funding_cache[symbol]

    h._metrics_day = metrics_day
    market_ref._premium_day = premium_day
    market_ref._funding_day = funding_day


def _sync_engine(
    h: Any,
    run_fn: Any,
    test_start_ms: int,
    test_end_ms: int,
) -> None:
    h.SYMBOLS = UNIVERSE_10
    h.DATA_START = DATA_START
    h.DATA_END = datetime.fromtimestamp(test_end_ms / 1000, tz=UTC).date() + timedelta(days=1)
    h.TEST_START_MS = test_start_ms
    h.TEST_END_MS = test_end_ms

    run_fn.__globals__["SYMBOLS"] = UNIVERSE_10
    run_fn.__globals__["TEST_START_MS"] = test_start_ms
    run_fn.__globals__["TEST_END_MS"] = test_end_ms


def _monthly_realized(trades: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    output: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        exit_dt = datetime.fromtimestamp(int(trade["exit_ts"]) / 1000, tz=UTC)
        key = f"{exit_dt.year:04d}-{exit_dt.month:02d}"
        bucket = output.setdefault(
            key,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "net_usdt": 0.0,
            },
        )
        bucket["trades"] = int(bucket["trades"]) + 1
        if float(trade["net"]) > 0:
            bucket["wins"] = int(bucket["wins"]) + 1
        else:
            bucket["losses"] = int(bucket["losses"]) + 1
        bucket["net_usdt"] = float(bucket["net_usdt"]) + float(trade["net"])

    for bucket in output.values():
        bucket["net_usdt"] = round(float(bucket["net_usdt"]), 6)
    return output


def _worst_trade(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not trades:
        return None
    return _trade_view(min(trades, key=lambda trade: float(trade["net"])))


def main() -> None:
    snapshot = _load_snapshot()
    test_end_ms = int(snapshot["snapshot_end_ms_exclusive"])
    test_start_ms = int(TEST_START.timestamp() * 1000)
    end_dt = datetime.fromtimestamp(test_end_ms / 1000, tz=UTC)

    if test_end_ms <= test_start_ms:
        raise RuntimeError("YTD end must be after 2026-01-01")

    with tempfile.TemporaryDirectory(prefix="novabot913-ytd-protect-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        strategy = modules["tmp_followthrough15_30then10"]

        protect_run = install_beast_patch(h, "protect_only")
        baseline_run = strategy.MODIFIED_RUN_HYBRID

        h._price_cache.clear()
        h._metrics_cache.clear()
        h._market_cache.clear()
        market_ref._premium_cache.clear()
        market_ref._funding_cache.clear()

        _install_snapshot_fallbacks(h, market_ref, snapshot, end_dt, test_end_ms)
        _sync_engine(h, baseline_run, test_start_ms, test_end_ms)
        _sync_engine(h, protect_run, test_start_ms, test_end_ms)

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

        protected_filter = _causal_filter_factory(h, market_ref)
        protected = _run_engine(h, protect_run, raw, protected_filter)

        baseline_summary = _summary(baseline)
        protected_summary = _summary(protected)
        delta = {
            key: round(float(protected_summary[key]) - float(baseline_summary[key]), 8)
            for key in (
                "final_equity",
                "return_pct",
                "trades",
                "win_rate_pct",
                "profit_factor",
                "fees",
                "max_drawdown_pct",
            )
        }

        report = {
            "phase": "strategy_913_ytd_continuous_protect_only",
            "research_only": True,
            "adopted": False,
            "continuous_account": True,
            "start_equity_usdt": 100.0,
            "test_start_utc": TEST_START.isoformat(),
            "test_end_utc_exclusive": end_dt.isoformat(),
            "last_completed_minute_only": True,
            "universe": list(UNIVERSE_10),
            "data_policy": {
                "historical_prices": "Binance Vision monthly archives",
                "current_month_completed_days": "Binance Vision daily archives",
                "current_utc_day": "Binance connector snapshot",
                "premium_taker_causality": "frozen corrected Strategy 913 timing",
                "oi_crowding_funding_timing": "unchanged from canonical Strategy 913",
            },
            "protection_rule": {
                "activate_at": "0.5R MFE",
                "first_protective_stop": "-0.25R on next minute",
                "break_even_at": "1.0R MFE",
                "break_even_lock": "+0.15%",
                "progressive_trailing": "canonical Strategy 913 from 1.5R onward",
                "staged_entry": False,
                "reentry": False,
            },
            "baseline": baseline_summary,
            "protected": protected_summary,
            "delta_protected_minus_baseline": delta,
            "baseline_worst_trade": _worst_trade(baseline["trades_detail"]),
            "protected_worst_trade": _worst_trade(protected["trades_detail"]),
            "baseline_monthly_realized": _monthly_realized(baseline["trades_detail"]),
            "protected_monthly_realized": _monthly_realized(protected["trades_detail"]),
            "baseline_trades": [
                _trade_view(item) for item in baseline["trades_detail"]
            ],
            "protected_trades": [
                _trade_view(item) for item in protected["trades_detail"]
            ],
        }

        output = Path("strategy913_ytd_protect_only.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "STRATEGY913_YTD_PROTECT="
            + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
