from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from novabot913.temporal import latest_completed_premium, latest_completed_taker

REFERENCE_DIR = Path(__file__).resolve().parents[1] / "reference" / "strategy913"
REFERENCE_FILES = (
    "tmp_hybrid_zec_score7plus_60d.py",
    "tmp_ten_score7plus_flow.py",
    "tmp_zec_extreme_reference.py",
    "tmp_hybrid_archive_market.py",
    "tmp_followthrough15_may2026_top10.py",
    "tmp_strategy_912_40coin_30d.py",
    "tmp_followthrough15_30then10.py",
)


def _stage_reference_files(directory: Path) -> None:
    for filename in REFERENCE_FILES:
        source = REFERENCE_DIR / filename
        if not source.is_file():
            raise FileNotFoundError(f"missing vendored Strategy 913 reference: {source}")
        shutil.copyfile(source, directory / filename)


def _load_modules(directory: Path) -> dict[str, Any]:
    sys.path.insert(0, str(directory))
    modules: dict[str, Any] = {}
    for name in (
        "tmp_hybrid_zec_score7plus_60d",
        "tmp_hybrid_archive_market",
        "tmp_followthrough15_may2026_top10",
        "tmp_strategy_912_40coin_30d",
        "tmp_followthrough15_30then10",
    ):
        modules[name] = importlib.import_module(name)
    return modules


def _causal_filter_factory(h: Any, market_ref: Any):
    def causal_archive_market_filters(
        symbol: str,
        timestamp: int,
        direction: int,
        btc_bars: list[tuple],
        btc_index: dict[int, int],
    ) -> dict[str, Any]:
        key = ("archive_causal", symbol, timestamp, direction)
        if key in h._market_cache:
            return h._market_cache[key]

        decision_ms = timestamp + 60_000
        day = h.datetime.fromtimestamp(timestamp / 1000, tz=h.timezone.utc).date()
        metrics = h._metrics_day(symbol, day - timedelta(days=1)) + h._metrics_day(symbol, day)
        metrics.sort(key=lambda item: item["timestamp"])

        # Keep OI and Crowding on the frozen legacy point-row timing.
        now = h._latest_before(metrics, timestamp)
        old = h._latest_before(metrics, timestamp - 30 * 60_000)

        # Authorized correction #2: Taker only after its represented 5m interval closes.
        taker = latest_completed_taker(metrics, decision_ms)

        # Authorized correction #1: Premium final close only after its 5m candle closes.
        # Copy cached lists before concatenation so the reference cache is never mutated.
        premium_rows = list(market_ref._premium_day(symbol, day - timedelta(days=1))) + list(
            market_ref._premium_day(symbol, day)
        )
        premium_rows.sort(key=lambda item: item["timestamp"])
        premium = latest_completed_premium(premium_rows, decision_ms)

        # Funding timing remains frozen and unchanged.
        funding = market_ref._funding_before(symbol, timestamp)
        if now is None or old is None or taker is None or premium is None or funding is None:
            result = {"pass": False, "reason": "missing_archive_market_data"}
            h._market_cache[key] = result
            return result

        taker_ratio = taker["taker_ratio"]
        taker_ok = (
            taker_ratio >= h.TAKER_LONG_MIN if direction == 1 else taker_ratio <= h.TAKER_SHORT_MAX
        )
        oi_change = now["oi"] / old["oi"] - 1.0 if old["oi"] else 0.0
        oi_ok = oi_change >= h.OI_MIN_RISE
        premium_close = premium["close"]
        premium_ok = abs(premium_close) <= h.MAX_PREMIUM_ABS
        funding_rate = funding["rate"]
        funding_ok = abs(funding_rate) <= h.MAX_FUNDING_ABS
        ratio = now["top_ratio"]
        long_share = ratio / (1.0 + ratio) if ratio > 0 else 0.5
        short_share = 1.0 - long_share
        crowd_ok = long_share >= 0.55 or short_share >= 0.55

        bi = btc_index.get(timestamp)
        btc_move = 0.0
        btc_ok = False
        if bi is not None and bi >= 10:
            btc_move = btc_bars[bi][4] / btc_bars[bi - 10][4] - 1.0
            btc_ok = direction * btc_move >= h.BTC_CONFIRM_MOVE

        checks = {
            "taker": taker_ok,
            "oi": oi_ok,
            "premium": premium_ok,
            "funding": funding_ok,
            "crowding": crowd_ok,
            "btc": btc_ok,
        }
        result = {
            "pass": all(checks.values()),
            "source": "Binance Vision archive, causal Premium/Taker",
            "decision_ms": decision_ms,
            "taker_timestamp": taker["timestamp"],
            "legacy_metric_timestamp": now["timestamp"],
            "premium_timestamp": premium["timestamp"],
            "taker_ratio": taker_ratio,
            "oi_change_pct": oi_change * 100.0,
            "premium_pct": premium_close * 100.0,
            "funding_pct": funding_rate * 100.0,
            "top_long_share": long_share,
            "btc_move_pct": btc_move * 100.0,
            "checks": checks,
        }
        h._market_cache[key] = result
        return result

    return causal_archive_market_filters


def _logged_filter(base_filter: Any, sink: list[dict[str, Any]]):
    def wrapped(symbol: str, timestamp: int, direction: int, btc_bars: Any, btc_index: Any):
        result = base_filter(symbol, timestamp, direction, btc_bars, btc_index)
        sink.append(
            {
                "symbol": symbol,
                "timestamp": timestamp,
                "direction": direction,
                "pass": bool(result.get("pass")),
                "reason": result.get("reason"),
                "checks": result.get("checks"),
                "taker_ratio": result.get("taker_ratio"),
                "premium_pct": result.get("premium_pct"),
                "taker_timestamp": result.get("taker_timestamp"),
                "premium_timestamp": result.get("premium_timestamp"),
            }
        )
        return result

    return wrapped


def _run_engine(h: Any, run_fn: Any, raw: dict[str, list[tuple]], market_filter: Any):
    h._market_cache.clear()
    h._market_filters = market_filter
    run_fn.__globals__["_market_filters"] = market_filter
    return run_fn(raw)


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "start_equity",
        "final_equity",
        "return_pct",
        "trades",
        "wins",
        "losses",
        "win_rate_pct",
        "profit_factor",
        "fees",
        "peak_marked_equity",
        "trough_marked_equity",
        "max_drawdown_pct",
        "liquidations",
        "exit_reasons",
        "pipeline",
    )
    return {key: result.get(key) for key in keys}


def _trade_view(trade: dict[str, Any]) -> dict[str, Any]:
    held = int(trade["held_minutes"])
    exit_ts = int(trade["exit_ts"])
    return {
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "score": trade["score"],
        "entry_ts": exit_ts - (held - 1) * 60_000,
        "exit_ts": exit_ts,
        "reason": trade["reason"],
        "held_minutes": held,
        "entry": trade["entry"],
        "exit": trade["exit"],
        "net": trade["net"],
    }


def _first_trade_divergence(
    legacy: list[dict[str, Any]], corrected: list[dict[str, Any]]
) -> dict[str, Any] | None:
    left = [_trade_view(item) for item in legacy]
    right = [_trade_view(item) for item in corrected]
    for index in range(max(len(left), len(right))):
        legacy_trade = left[index] if index < len(left) else None
        corrected_trade = right[index] if index < len(right) else None
        if legacy_trade != corrected_trade:
            return {"index": index + 1, "legacy": legacy_trade, "corrected": corrected_trade}
    return None


def _first_filter_divergence(
    legacy_log: list[dict[str, Any]], corrected_log: list[dict[str, Any]]
) -> dict[str, Any] | None:
    def keyed(items: list[dict[str, Any]]) -> dict[tuple[str, int, int], dict[str, Any]]:
        return {(x["symbol"], x["timestamp"], x["direction"]): x for x in items}

    left = keyed(legacy_log)
    right = keyed(corrected_log)
    common = sorted(set(left) & set(right), key=lambda key: (key[1], key[0], key[2]))
    for key in common:
        legacy_item = left[key]
        corrected_item = right[key]
        if legacy_item["pass"] != corrected_item["pass"] or legacy_item.get(
            "checks"
        ) != corrected_item.get("checks"):
            return {"key": list(key), "legacy": legacy_item, "corrected": corrected_item}
    return None


def _run_may(modules: dict[str, Any], causal_filter: Any) -> dict[str, Any]:
    h = modules["tmp_hybrid_zec_score7plus_60d"]
    market_ref = modules["tmp_hybrid_archive_market"]
    may = modules["tmp_followthrough15_may2026_top10"]
    raw = may._prepare()

    legacy_log: list[dict[str, Any]] = []
    legacy_filter = _logged_filter(market_ref.archive_market_filters, legacy_log)
    legacy = _run_engine(h, may.MODIFIED_RUN_HYBRID, raw, legacy_filter)

    corrected_log: list[dict[str, Any]] = []
    corrected_filter = _logged_filter(causal_filter, corrected_log)
    corrected = _run_engine(h, may.MODIFIED_RUN_HYBRID, raw, corrected_filter)

    return {
        "window": "2026-05-01T00:00:00Z/2026-06-01T00:00:00Z",
        "legacy": _summary(legacy),
        "corrected": _summary(corrected),
        "first_filter_divergence": _first_filter_divergence(legacy_log, corrected_log),
        "first_trade_divergence": _first_trade_divergence(
            legacy["trades_detail"], corrected["trades_detail"]
        ),
        "legacy_trades": [_trade_view(x) for x in legacy["trades_detail"]],
        "corrected_trades": [_trade_view(x) for x in corrected["trades_detail"]],
    }


def _run_augsep(modules: dict[str, Any], causal_filter: Any) -> dict[str, Any]:
    h = modules["tmp_hybrid_zec_score7plus_60d"]
    market_ref = modules["tmp_hybrid_archive_market"]
    aug = modules["tmp_followthrough15_30then10"]
    raw = aug.prepare(aug.UNIVERSE_10)

    legacy_log: list[dict[str, Any]] = []
    legacy_filter = _logged_filter(market_ref.archive_market_filters, legacy_log)
    legacy = _run_engine(h, aug.MODIFIED_RUN_HYBRID, raw, legacy_filter)

    corrected_log: list[dict[str, Any]] = []
    corrected_filter = _logged_filter(causal_filter, corrected_log)
    corrected = _run_engine(h, aug.MODIFIED_RUN_HYBRID, raw, corrected_filter)

    return {
        "window": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
        "legacy": _summary(legacy),
        "corrected": _summary(corrected),
        "first_filter_divergence": _first_filter_divergence(legacy_log, corrected_log),
        "first_trade_divergence": _first_trade_divergence(
            legacy["trades_detail"], corrected["trades_detail"]
        ),
        "legacy_trades": [_trade_view(x) for x in legacy["trades_detail"]],
        "corrected_trades": [_trade_view(x) for x in corrected["trades_detail"]],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="novabot913-reference-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        causal_filter = _causal_filter_factory(h, market_ref)

        may = _run_may(modules, causal_filter)
        print("CORRECTED_REFERENCE_MAY=" + json.dumps(may["corrected"], sort_keys=True), flush=True)

        augsep = _run_augsep(modules, causal_filter)
        print(
            "CORRECTED_REFERENCE_AUGSEP=" + json.dumps(augsep["corrected"], sort_keys=True),
            flush=True,
        )

        result = {
            "strategy": "913 fixed universe",
            "authorized_temporal_corrections": [
                "Premium 5m final close visible only after full interval completion",
                "Taker 5m ratio visible only after represented interval completion",
            ],
            "unchanged_timing": ["OI", "Crowding", "Funding"],
            "decision_time_convention": "actual decision = reference 1m open label + 60 seconds",
            "may_2026": may,
            "aug_sep_2026": augsep,
        }
        output = Path("corrected_causal_reference.json")
        output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print("CORRECTED_CAUSAL_REFERENCE=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
