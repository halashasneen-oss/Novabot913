from __future__ import annotations

import argparse
import inspect
import json
import tempfile
import textwrap
from datetime import UTC, date, datetime
from itertools import product
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

PROTECT_TRIGGER_R_VALUES = (0.40, 0.50, 0.60, 0.75)
PROTECT_STOP_R_VALUES = (0.10, 0.20, 0.25, 0.35)
BREAK_EVEN_TRIGGER_R_VALUES = (0.80, 1.00, 1.20)

FOLLOWTHROUGH_MINUTE = 15
MIN_CLOSE_MOVE = 0.0030
MIN_MFE = 0.0077

UNIVERSE_10 = (
    "DOGEUSDT",
    "BTCUSDT",
    "XRPUSDT",
    "TIAUSDT",
    "1000PEPEUSDT",
    "DOTUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "WIFUSDT",
    "ETCUSDT",
)

WINDOW_CONFIGS: dict[str, dict[str, Any]] = {
    "apr_2026": {
        "window": "2026-04-01T00:00:00Z/2026-05-01T00:00:00Z",
        "data_start": date(2026, 3, 30),
        "data_end": date(2026, 5, 1),
        "test_start": datetime(2026, 4, 1, tzinfo=UTC),
        "test_end": datetime(2026, 5, 1, tzinfo=UTC),
        "months": ((2026, 3), (2026, 4)),
    },
    "jun_jul_2026": {
        "window": "2026-06-17T00:00:00Z/2026-07-17T00:00:00Z",
        "data_start": date(2026, 6, 15),
        "data_end": date(2026, 7, 17),
        "test_start": datetime(2026, 6, 17, tzinfo=UTC),
        "test_end": datetime(2026, 7, 17, tzinfo=UTC),
        "months": ((2026, 6), (2026, 7)),
    },
    "jul_aug_2026": {
        "window": "2026-07-17T00:00:00Z/2026-08-17T00:00:00Z",
        "data_start": date(2026, 7, 15),
        "data_end": date(2026, 8, 17),
        "test_start": datetime(2026, 7, 17, tzinfo=UTC),
        "test_end": datetime(2026, 8, 17, tzinfo=UTC),
        "months": ((2026, 7), (2026, 8)),
    },
}


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise RuntimeError(f"patch anchor missing: {label}")
    return source.replace(old, new, 1)


def _variant_name(
    protect_trigger_r: float,
    protect_stop_r: float,
    break_even_trigger_r: float,
) -> str:
    return (f"p{protect_trigger_r:.2f}_s{protect_stop_r:.2f}_be{break_even_trigger_r:.2f}").replace(
        ".", "p"
    )


def install_protection_patch(
    h: Any,
    protect_trigger_r: float,
    protect_stop_r: float,
    break_even_trigger_r: float,
):
    source = textwrap.dedent(inspect.getsource(h._run_hybrid))

    source = _replace_once(
        source,
        '        "early_failure_exits": 0,\n',
        '        "early_failure_exits": 0,\n        "followthrough_15m_exits": 0,\n',
        "stats",
    )

    old_pending_exit = (
        "            if bar is not None:\n"
        '                close_position(symbol, bar[1], "EARLY_FAILURE", ts)\n'
        '                stats["early_failure_exits"] += 1\n'
    )
    new_pending_exit = (
        "            if bar is not None:\n"
        '                reason = positions[symbol].pop("pending_early_reason", "EARLY_FAILURE")\n'
        "                close_position(symbol, bar[1], reason, ts)\n"
        '                if reason == "EARLY_FAILURE":\n'
        '                    stats["early_failure_exits"] += 1\n'
        "                else:\n"
        '                    stats["followthrough_15m_exits"] += 1\n'
    )
    source = _replace_once(
        source,
        old_pending_exit,
        new_pending_exit,
        "followthrough next-open exit",
    )

    source = _replace_once(
        source,
        '                "pending_stop": None,\n',
        '                "pending_stop": None,\n                "followthrough_mfe": 0.0,\n',
        "position followthrough state",
    )

    old_manage = (
        "            if direction == 1:\n"
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    new_manage = (
        "            if direction == 1:\n"
        '                current_follow_mfe = high / pos["entry"] - 1.0\n'
        "            else:\n"
        '                current_follow_mfe = pos["entry"] / low - 1.0\n'
        '            pos["followthrough_mfe"] = max(\n'
        '                pos["followthrough_mfe"], current_follow_mfe\n'
        "            )\n"
        '            if pos["held_minutes"] == FOLLOWTHROUGH_MINUTE:\n'
        '                close_move = direction * (close / pos["entry"] - 1.0)\n'
        '                if close_move < MIN_CLOSE_MOVE and pos["followthrough_mfe"] < MIN_MFE:\n'
        '                    pos["pending_early_reason"] = "FOLLOWTHROUGH_15M"\n'
        "                    pending_early[symbol] = ts + 60_000\n"
        "            if direction == 1:\n"
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    source = _replace_once(
        source,
        old_manage,
        new_manage,
        "followthrough management",
    )

    old_trail = """            trail_distance = _progressive_trail(pos["risk"], pos["mfe"])
            if trail_distance is not None:
                be = pos["entry"] * (1.0 + direction * BE_LOCK)
                trail = (
                    pos["best_close"] * (1.0 - trail_distance)
                    if direction == 1
                    else pos["best_close"] * (1.0 + trail_distance)
                )
                proposed = max(be, trail) if direction == 1 else min(be, trail)
                pos["pending_stop"] = proposed
"""
    new_trail = """            proposed = None
            if pos["mfe"] >= PROTECT_TRIGGER_R * pos["risk"]:
                protect = pos["entry"] * (
                    1.0 - direction * PROTECT_STOP_R * pos["risk"]
                )
                proposed = protect
            if pos["mfe"] >= BREAK_EVEN_TRIGGER_R * pos["risk"]:
                be = pos["entry"] * (1.0 + direction * BE_LOCK)
                proposed = (
                    max(proposed, be)
                    if direction == 1 and proposed is not None
                    else min(proposed, be)
                    if direction == -1 and proposed is not None
                    else be
                )
            trail_distance = _progressive_trail(pos["risk"], pos["mfe"])
            if trail_distance is not None:
                be = pos["entry"] * (1.0 + direction * BE_LOCK)
                trail = (
                    pos["best_close"] * (1.0 - trail_distance)
                    if direction == 1
                    else pos["best_close"] * (1.0 + trail_distance)
                )
                trail_proposed = max(be, trail) if direction == 1 else min(be, trail)
                proposed = (
                    max(proposed, trail_proposed)
                    if direction == 1 and proposed is not None
                    else min(proposed, trail_proposed)
                    if direction == -1 and proposed is not None
                    else trail_proposed
                )
            if proposed is not None:
                existing = pos.get("pending_stop")
                if existing is None:
                    pos["pending_stop"] = proposed
                elif direction == 1:
                    pos["pending_stop"] = max(existing, proposed)
                else:
                    pos["pending_stop"] = min(existing, proposed)
"""
    source = _replace_once(
        source,
        old_trail,
        new_trail,
        "early profit protection",
    )

    namespace = dict(h.__dict__)
    namespace.update(
        {
            "FOLLOWTHROUGH_MINUTE": FOLLOWTHROUGH_MINUTE,
            "MIN_CLOSE_MOVE": MIN_CLOSE_MOVE,
            "MIN_MFE": MIN_MFE,
            "PROTECT_TRIGGER_R": protect_trigger_r,
            "PROTECT_STOP_R": protect_stop_r,
            "BREAK_EVEN_TRIGGER_R": break_even_trigger_r,
        }
    )
    exec(compile(source, "<strategy913_protection_sweep>", "exec"), namespace)
    return namespace["_run_hybrid"]


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


def _prepare_generic(
    h: Any,
    market_ref: Any,
    run_fns: tuple[Any, ...],
    config: dict[str, Any],
) -> dict[str, list[tuple]]:
    h.SYMBOLS = UNIVERSE_10
    h.DATA_START = config["data_start"]
    h.DATA_END = config["data_end"]
    h.TEST_START_MS = int(config["test_start"].timestamp() * 1000)
    h.TEST_END_MS = int(config["test_end"].timestamp() * 1000)
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    market_ref._premium_cache.clear()
    market_ref._funding_cache.clear()

    for run_fn in run_fns:
        run_fn.__globals__["SYMBOLS"] = UNIVERSE_10
        run_fn.__globals__["TEST_START_MS"] = h.TEST_START_MS
        run_fn.__globals__["TEST_END_MS"] = h.TEST_END_MS

    raw: dict[str, list[tuple]] = {}
    for index, symbol in enumerate(UNIVERSE_10, 1):
        rows: list[tuple] = []
        for year, month in config["months"]:
            _append_month(
                h,
                rows,
                symbol,
                year,
                month,
                config["data_start"],
                config["data_end"],
            )
        rows.sort(key=lambda item: item[0])
        raw[symbol] = rows
        print(
            f"DATA {index:02d}/{len(UNIVERSE_10)} {symbol} bars={len(rows)}",
            flush=True,
        )

    missing = [symbol for symbol, rows in raw.items() if not rows]
    if missing:
        raise RuntimeError(f"missing price data: {missing}")
    return raw


def _sync_run_globals(run_fn: Any, h: Any) -> None:
    run_fn.__globals__["SYMBOLS"] = UNIVERSE_10
    run_fn.__globals__["TEST_START_MS"] = h.TEST_START_MS
    run_fn.__globals__["TEST_END_MS"] = h.TEST_END_MS


def _prepare(
    window: str,
    modules: dict[str, Any],
    run_fns: tuple[Any, ...],
) -> tuple[dict[str, list[tuple]], Any]:
    h = modules["tmp_hybrid_zec_score7plus_60d"]
    market_ref = modules["tmp_hybrid_archive_market"]
    strategy = modules["tmp_followthrough15_30then10"]

    if window == "may_2026":
        may = modules["tmp_followthrough15_may2026_top10"]
        raw = may._prepare()
        baseline_run = may.MODIFIED_RUN_HYBRID
        for run_fn in run_fns:
            _sync_run_globals(run_fn, h)
        return raw, baseline_run

    if window == "aug_sep_2026":
        raw = strategy.prepare(strategy.UNIVERSE_10)
        baseline_run = strategy.MODIFIED_RUN_HYBRID
        for run_fn in run_fns:
            _sync_run_globals(run_fn, h)
        return raw, baseline_run

    config = WINDOW_CONFIGS[window]
    baseline_run = strategy.MODIFIED_RUN_HYBRID
    raw = _prepare_generic(
        h,
        market_ref,
        (baseline_run, *run_fns),
        config,
    )
    return raw, baseline_run


def _memoized_filter(base_filter: Any):
    cache: dict[tuple[str, int, int], dict[str, Any]] = {}

    def wrapped(
        symbol: str,
        timestamp: int,
        direction: int,
        btc_bars: Any,
        btc_index: Any,
    ) -> dict[str, Any]:
        key = (symbol, timestamp, direction)
        if key not in cache:
            cache[key] = base_filter(
                symbol,
                timestamp,
                direction,
                btc_bars,
                btc_index,
            )
        return cache[key]

    return wrapped


def _score(summary: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(summary["return_pct"]),
        -float(summary["max_drawdown_pct"]),
        float(summary["profit_factor"]),
    )


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

    combinations = list(
        product(
            PROTECT_TRIGGER_R_VALUES,
            PROTECT_STOP_R_VALUES,
            BREAK_EVEN_TRIGGER_R_VALUES,
        )
    )

    with tempfile.TemporaryDirectory(prefix="novabot913-protection-sweep-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]

        variants: dict[str, dict[str, Any]] = {}
        run_fns: list[Any] = []
        for protect_trigger_r, protect_stop_r, break_even_trigger_r in combinations:
            name = _variant_name(
                protect_trigger_r,
                protect_stop_r,
                break_even_trigger_r,
            )
            run_fn = install_protection_patch(
                h,
                protect_trigger_r,
                protect_stop_r,
                break_even_trigger_r,
            )
            variants[name] = {
                "protect_trigger_r": protect_trigger_r,
                "protect_stop_r": protect_stop_r,
                "break_even_trigger_r": break_even_trigger_r,
                "run_fn": run_fn,
            }
            run_fns.append(run_fn)

        raw, baseline_run = _prepare(args.window, modules, tuple(run_fns))

        base_filter = _causal_filter_factory(h, market_ref)
        shared_filter = _memoized_filter(base_filter)

        baseline = _run_engine(h, baseline_run, raw, shared_filter)
        baseline_summary = _summary(baseline)
        print(
            "PROTECTION_SWEEP_BASELINE=" + json.dumps(baseline_summary, sort_keys=True),
            flush=True,
        )

        results: dict[str, Any] = {}
        for index, (name, item) in enumerate(variants.items(), 1):
            run_fn = item["run_fn"]
            variant = _run_engine(h, run_fn, raw, shared_filter)
            summary = _summary(variant)
            results[name] = {
                "protect_trigger_r": item["protect_trigger_r"],
                "protect_stop_r": item["protect_stop_r"],
                "break_even_trigger_r": item["break_even_trigger_r"],
                "summary": summary,
                "trades": [_trade_view(trade) for trade in variant["trades_detail"]],
            }
            print(
                f"VARIANT {index:02d}/{len(variants)} {name} "
                f"return={summary['return_pct']} "
                f"dd={summary['max_drawdown_pct']} "
                f"pf={summary['profit_factor']}",
                flush=True,
            )

        ranked = sorted(
            (
                {
                    "name": name,
                    "protect_trigger_r": item["protect_trigger_r"],
                    "protect_stop_r": item["protect_stop_r"],
                    "break_even_trigger_r": item["break_even_trigger_r"],
                    "return_pct": item["summary"]["return_pct"],
                    "max_drawdown_pct": item["summary"]["max_drawdown_pct"],
                    "profit_factor": item["summary"]["profit_factor"],
                    "trades": item["summary"]["trades"],
                    "fees": item["summary"]["fees"],
                }
                for name, item in results.items()
            ),
            key=lambda row: _score(
                {
                    "return_pct": row["return_pct"],
                    "max_drawdown_pct": row["max_drawdown_pct"],
                    "profit_factor": row["profit_factor"],
                }
            ),
            reverse=True,
        )

        window_label = (
            WINDOW_CONFIGS[args.window]["window"]
            if args.window in WINDOW_CONFIGS
            else {
                "may_2026": "2026-05-01T00:00:00Z/2026-06-01T00:00:00Z",
                "aug_sep_2026": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
            }[args.window]
        )

        report = {
            "phase": "strategy_913_early_profit_protection_sweep",
            "research_only": True,
            "adopted": False,
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "canonical_strategy_modified": False,
            "window_name": args.window,
            "window": window_label,
            "universe": list(UNIVERSE_10),
            "grid": {
                "protect_trigger_r": list(PROTECT_TRIGGER_R_VALUES),
                "protect_stop_r": list(PROTECT_STOP_R_VALUES),
                "break_even_trigger_r": list(BREAK_EVEN_TRIGGER_R_VALUES),
                "variants": len(combinations),
            },
            "baseline": baseline_summary,
            "baseline_trades": [_trade_view(trade) for trade in baseline["trades_detail"]],
            "ranked": ranked,
            "results": results,
        }

        output = Path(f"strategy913_protection_sweep_{args.window}.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "STRATEGY913_PROTECTION_SWEEP=" + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
