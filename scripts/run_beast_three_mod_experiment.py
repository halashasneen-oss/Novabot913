from __future__ import annotations

import argparse
import inspect
import json
import tempfile
import textwrap
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

STAGE1_FRACTION = 0.50
STAGE2_TRIGGER_R = 0.50
EARLY_PROTECT_TRIGGER_R = 0.50
EARLY_PROTECT_STOP_R = 0.25
BREAK_EVEN_TRIGGER_R = 1.00
REENTRY_WINDOW_MINUTES = 30
MAX_REENTRIES = 1

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


def install_beast_patch(h: Any):
    source = textwrap.dedent(inspect.getsource(h._run_hybrid))

    source = _replace_once(
        source,
        '    pending_early = {}\n',
        '    pending_early = {}\n'
        '    pending_scale = {}\n'
        '    reentry_watch = {}\n',
        "state dictionaries",
    )

    source = _replace_once(
        source,
        '        "early_failure_exits": 0,\n',
        '        "early_failure_exits": 0,\n'
        '        "followthrough_15m_exits": 0,\n'
        '        "stage2_entries": 0,\n'
        '        "stage2_rejections": 0,\n'
        '        "reentry_approvals": 0,\n'
        '        "reentry_expired": 0,\n',
        "stats",
    )

    source = _replace_once(
        source,
        '        pos = positions.pop(symbol)\n',
        '        pos = positions.pop(symbol)\n'
        '        pending_scale.pop(symbol, None)\n',
        "close position state cleanup",
    )

    watch_code = (
        '        if reason in ("EARLY_FAILURE", "FOLLOWTHROUGH_15M") '
        'and pos.get("reentry_count", 0) < MAX_REENTRIES:\n'
        '            reentry_watch[symbol] = {\n'
        '                "direction": pos["direction"],\n'
        '                "score": pos["score"],\n'
        '                "breakout_level": pos["breakout_level"],\n'
        '                "expires": ts + REENTRY_WINDOW_MINUTES * 60_000,\n'
        '                "reentry_count": pos.get("reentry_count", 0) + 1,\n'
        '            }\n'
    )
    source = _replace_once(
        source,
        '\n    for ts in timestamps:\n',
        '\n' + watch_code + '\n    for ts in timestamps:\n',
        "reentry watch creation",
    )

    old_pending_exit = (
        '            if bar is not None:\n'
        '                close_position(symbol, bar[1], "EARLY_FAILURE", ts)\n'
        '                stats["early_failure_exits"] += 1\n'
    )
    new_pending_exit = (
        '            if bar is not None:\n'
        '                reason = positions[symbol].pop("pending_early_reason", "EARLY_FAILURE")\n'
        '                close_position(symbol, bar[1], reason, ts)\n'
        '                if reason == "EARLY_FAILURE":\n'
        '                    stats["early_failure_exits"] += 1\n'
        '                else:\n'
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
        '            required = wallet * margin_fraction\n',
        '            target_margin = wallet * margin_fraction\n'
        '            required = target_margin * STAGE1_FRACTION\n',
        "stage1 margin",
    )

    source = _replace_once(
        source,
        '                "pending_stop": None,\n',
        '                "pending_stop": None,\n'
        '                "followthrough_mfe": 0.0,\n'
        '                "target_margin": target_margin,\n'
        '                "stage2_added": False,\n'
        '                "reentry_count": int(item.get("reentry_count", 0)),\n'
        '                "is_reentry": bool(item.get("is_reentry", False)),\n',
        "position experiment fields",
    )

    scale_code = '''        # Research-only staged second entry executes at the next minute open.
        for symbol in list(pending_scale):
            if pending_scale[symbol] != ts or symbol not in positions:
                if pending_scale[symbol] < ts or symbol not in positions:
                    pending_scale.pop(symbol, None)
                continue
            pos = positions[symbol]
            bar = maps[symbol].get(ts)
            if bar is None:
                pending_scale.pop(symbol, None)
                continue
            used = sum(p["margin"] for p in positions.values())
            available = max(0.0, wallet - used)
            remaining = max(0.0, pos["target_margin"] - pos["margin"])
            if remaining <= 0:
                pos["stage2_added"] = True
                pending_scale.pop(symbol, None)
                continue
            if remaining > available + 1e-9:
                stats["stage2_rejections"] += 1
                pending_scale.pop(symbol, None)
                continue
            direction = pos["direction"]
            add_entry = bar[1] * (1.0 + direction * SLIPPAGE)
            add_notional = remaining * pos["leverage"]
            add_fee = add_notional * FEE
            wallet -= add_fee
            fees += add_fee
            old_notional = pos["notional"]
            new_notional = old_notional + add_notional
            pos["entry"] = (
                pos["entry"] * old_notional + add_entry * add_notional
            ) / new_notional
            pos["margin"] += remaining
            pos["notional"] = new_notional
            pos["entry_fee"] += add_fee
            pos["stage2_added"] = True
            liqdist = max(0.003, 1.0 / pos["leverage"] - 0.005)
            candidate_stop = pos["entry"] * (1.0 - direction * pos["risk"])
            if direction == 1:
                pos["stop"] = max(pos["stop"], candidate_stop)
                pos["best_close"] = max(pos["best_close"], pos["entry"])
                pos["mfe"] = max(0.0, pos["best_close"] / pos["entry"] - 1.0)
            else:
                pos["stop"] = min(pos["stop"], candidate_stop)
                pos["best_close"] = min(pos["best_close"], pos["entry"])
                pos["mfe"] = max(0.0, pos["entry"] / pos["best_close"] - 1.0)
            pos["liq"] = pos["entry"] * (1.0 - direction * liqdist)
            stats["stage2_entries"] += 1
            pending_scale.pop(symbol, None)

'''
    source = _replace_once(
        source,
        '        # Manage open positions before processing new signals.\n',
        scale_code + '        # Manage open positions before processing new signals.\n',
        "stage2 execution",
    )

    old_manage = (
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    new_manage = (
        '            if direction == 1:\n'
        '                current_follow_mfe = high / pos["entry"] - 1.0\n'
        '            else:\n'
        '                current_follow_mfe = pos["entry"] / low - 1.0\n'
        '            pos["followthrough_mfe"] = max(pos["followthrough_mfe"], current_follow_mfe)\n'
        '            if pos["held_minutes"] == FOLLOWTHROUGH_MINUTE:\n'
        '                close_move = direction * (close / pos["entry"] - 1.0)\n'
        '                if close_move < MIN_CLOSE_MOVE and pos["followthrough_mfe"] < MIN_MFE:\n'
        '                    pos["pending_early_reason"] = "FOLLOWTHROUGH_15M"\n'
        '                    pending_early[symbol] = ts + 60_000\n'
        '            stage_move = direction * (close / pos["entry"] - 1.0)\n'
        '            if (\n'
        '                not pos["stage2_added"]\n'
        '                and symbol not in pending_scale\n'
        '                and stage_move >= STAGE2_TRIGGER_R * pos["risk"]\n'
        '            ):\n'
        '                pending_scale[symbol] = ts + 60_000\n'
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    source = _replace_once(
        source,
        old_manage,
        new_manage,
        "followthrough and stage2 trigger",
    )

    old_trail = '''            trail_distance = _progressive_trail(pos["risk"], pos["mfe"])
            if trail_distance is not None:
                be = pos["entry"] * (1.0 + direction * BE_LOCK)
                trail = (
                    pos["best_close"] * (1.0 - trail_distance)
                    if direction == 1
                    else pos["best_close"] * (1.0 + trail_distance)
                )
                proposed = max(be, trail) if direction == 1 else min(be, trail)
                pos["pending_stop"] = proposed
'''
    new_trail = '''            proposed = None
            if pos["mfe"] >= EARLY_PROTECT_TRIGGER_R * pos["risk"]:
                protect = pos["entry"] * (
                    1.0 - direction * EARLY_PROTECT_STOP_R * pos["risk"]
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
'''
    source = _replace_once(
        source,
        old_trail,
        new_trail,
        "early profit protection",
    )

    reentry_code = '''        # Research-only one-shot re-entry after premature management exits.
        for symbol in list(reentry_watch):
            item = reentry_watch[symbol]
            if ts > item["expires"]:
                stats["reentry_expired"] += 1
                reentry_watch.pop(symbol, None)
                continue
            if symbol in positions or symbol in pending_entries:
                continue
            bar = maps[symbol].get(ts)
            if bar is None:
                continue
            direction = item["direction"]
            close = bar[4]
            i1 = datasets[symbol]["index_1m"].get(ts)
            vote_ok = i1 is not None and s7._tf_vote(
                datasets[symbol][1], i1, direction, 1
            )
            relaunched = (
                close > item["breakout_level"]
                if direction == 1
                else close < item["breakout_level"]
            )
            if not (vote_ok and relaunched):
                continue
            filters = _market_filters(symbol, ts, direction, btc_bars, btc_index)
            if not filters.get("pass"):
                continue
            pending_entries[symbol] = {
                "direction": direction,
                "score": item["score"],
                "breakout_level": item["breakout_level"],
                "filters": filters,
                "entry_ts": ts + 60_000,
                "reentry_count": item["reentry_count"],
                "is_reentry": True,
            }
            stats["reentry_approvals"] += 1
            reentry_watch.pop(symbol, None)

'''
    source = _replace_once(
        source,
        '        # Arm fresh 4H reconstructed extreme setups at completed 4H close.\n',
        reentry_code + '        # Arm fresh 4H reconstructed extreme setups at completed 4H close.\n',
        "reentry processing",
    )

    source = _replace_once(
        source,
        '            if symbol in positions or symbol in armed or symbol in retests or symbol in pending_entries:\n',
        '            if (\n'
        '                symbol in positions\n'
        '                or symbol in armed\n'
        '                or symbol in retests\n'
        '                or symbol in pending_entries\n'
        '                or symbol in reentry_watch\n'
        '            ):\n',
        "fresh setup exclusion during reentry watch",
    )

    namespace = dict(h.__dict__)
    namespace.update(
        {
            "FOLLOWTHROUGH_MINUTE": FOLLOWTHROUGH_MINUTE,
            "MIN_CLOSE_MOVE": MIN_CLOSE_MOVE,
            "MIN_MFE": MIN_MFE,
            "STAGE1_FRACTION": STAGE1_FRACTION,
            "STAGE2_TRIGGER_R": STAGE2_TRIGGER_R,
            "EARLY_PROTECT_TRIGGER_R": EARLY_PROTECT_TRIGGER_R,
            "EARLY_PROTECT_STOP_R": EARLY_PROTECT_STOP_R,
            "BREAK_EVEN_TRIGGER_R": BREAK_EVEN_TRIGGER_R,
            "REENTRY_WINDOW_MINUTES": REENTRY_WINDOW_MINUTES,
            "MAX_REENTRIES": MAX_REENTRIES,
        }
    )
    exec(compile(source, "<strategy913_beast_research>", "exec"), namespace)
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
    start_ms = int(
        datetime.combine(data_start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    end_ms = int(
        datetime.combine(data_end, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
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
    return {
        key: round(float(variant[key]) - float(baseline[key]), 8)
        for key in keys
    }


def _prepare(
    window: str,
    modules: dict[str, Any],
    beast_run: Any,
) -> tuple[dict[str, list[tuple]], Any]:
    h = modules["tmp_hybrid_zec_score7plus_60d"]
    market_ref = modules["tmp_hybrid_archive_market"]
    strategy = modules["tmp_followthrough15_30then10"]

    if window == "may_2026":
        may = modules["tmp_followthrough15_may2026_top10"]
        raw = may._prepare()
        baseline_run = may.MODIFIED_RUN_HYBRID
        _sync_run_globals(beast_run, h)
        return raw, baseline_run

    if window == "aug_sep_2026":
        raw = strategy.prepare(strategy.UNIVERSE_10)
        baseline_run = strategy.MODIFIED_RUN_HYBRID
        _sync_run_globals(beast_run, h)
        return raw, baseline_run

    config = WINDOW_CONFIGS[window]
    baseline_run = strategy.MODIFIED_RUN_HYBRID
    raw = _prepare_generic(h, market_ref, (baseline_run, beast_run), config)
    return raw, baseline_run


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

    with tempfile.TemporaryDirectory(prefix="novabot913-beast-research-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)

        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]
        beast_run = install_beast_patch(h)
        raw, baseline_run = _prepare(args.window, modules, beast_run)

        baseline_filter = _causal_filter_factory(h, market_ref)
        baseline = _run_engine(h, baseline_run, raw, baseline_filter)
        baseline_summary = _summary(baseline)
        print(
            "BEAST_BASELINE=" + json.dumps(baseline_summary, sort_keys=True),
            flush=True,
        )

        market_ref._premium_cache.clear()
        market_ref._funding_cache.clear()
        variant_filter = _causal_filter_factory(h, market_ref)
        variant = _run_engine(h, beast_run, raw, variant_filter)
        variant_summary = _summary(variant)
        print(
            "BEAST_VARIANT=" + json.dumps(variant_summary, sort_keys=True),
            flush=True,
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
            "phase": "strategy_913_beast_three_mod_research",
            "research_only": True,
            "adopted": False,
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "window_name": args.window,
            "window": window_label,
            "universe": list(UNIVERSE_10),
            "canonical_strategy_modified": False,
            "experiment": {
                "staged_entry": {
                    "stage1_share_of_original_margin": STAGE1_FRACTION,
                    "stage2_share_of_original_margin": 1.0 - STAGE1_FRACTION,
                    "stage2_trigger": f"{STAGE2_TRIGGER_R}R favorable completed 1m close",
                    "stage2_execution": "next 1m open with canonical slippage",
                },
                "early_profit_protection": {
                    "at_0_5R": "next-minute stop tightens to -0.25R",
                    "at_1R": "next-minute stop tightens to canonical +0.15% BE lock",
                    "existing_progressive_trailing_after_1_5R": True,
                },
                "reentry": {
                    "maximum": MAX_REENTRIES,
                    "eligible_exits": ["EARLY_FAILURE", "FOLLOWTHROUGH_15M"],
                    "window_minutes": REENTRY_WINDOW_MINUTES,
                    "requirements": [
                        "completed 1m directional vote passes",
                        "completed 1m close reclaims original breakout level",
                        "canonical causal market filter passes again",
                    ],
                    "execution": "next 1m open",
                },
            },
            "baseline": baseline_summary,
            "variant": variant_summary,
            "delta_variant_minus_baseline": _delta(baseline_summary, variant_summary),
            "baseline_trades": [_trade_view(item) for item in baseline["trades_detail"]],
            "variant_trades": [_trade_view(item) for item in variant["trades_detail"]],
        }
        output = Path(f"beast_three_mod_{args.window}.json")
        output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(
            "BEAST_THREE_MOD_RESULT=" + json.dumps(report, sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    main()
