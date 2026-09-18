import inspect
import json
import textwrap

import tmp_hybrid_zec_score7plus_60d as h
import tmp_strategy_912_40coin_30d as base
from tmp_hybrid_archive_market import archive_market_filters

UNIVERSE_30 = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "TRXUSDT",
    "DOTUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT",
    "AAVEUSDT", "UNIUSDT", "ETCUSDT", "ATOMUSDT", "FILUSDT",
    "ICPUSDT", "ARBUSDT", "OPUSDT", "ENAUSDT", "WIFUSDT",
    "1000PEPEUSDT", "FETUSDT", "INJUSDT", "RENDERUSDT", "SEIUSDT",
)

UNIVERSE_10 = (
    "DOGEUSDT", "BTCUSDT", "XRPUSDT", "TIAUSDT", "1000PEPEUSDT",
    "DOTUSDT", "UNIUSDT", "SUIUSDT", "WIFUSDT", "ETCUSDT",
)

FOLLOWTHROUGH_MINUTE = 15
MIN_CLOSE_MOVE = 0.0030
MIN_MFE = 0.0077

ORIGINAL_RUN_HYBRID = h._run_hybrid


def install_followthrough_patch():
    source = textwrap.dedent(inspect.getsource(ORIGINAL_RUN_HYBRID))

    old_stats = '        "early_failure_exits": 0,\n'
    new_stats = old_stats + '        "followthrough_15m_exits": 0,\n'
    if old_stats not in source:
        raise RuntimeError("stats patch anchor missing")
    source = source.replace(old_stats, new_stats, 1)

    old_exit = (
        '            if bar is not None:\n'
        '                close_position(symbol, bar[1], "EARLY_FAILURE", ts)\n'
        '                stats["early_failure_exits"] += 1\n'
    )
    new_exit = (
        '            if bar is not None:\n'
        '                reason = positions[symbol].pop("pending_early_reason", "EARLY_FAILURE")\n'
        '                close_position(symbol, bar[1], reason, ts)\n'
        '                if reason == "EARLY_FAILURE":\n'
        '                    stats["early_failure_exits"] += 1\n'
        '                else:\n'
        '                    stats["followthrough_15m_exits"] += 1\n'
    )
    if old_exit not in source:
        raise RuntimeError("pending exit patch anchor missing")
    source = source.replace(old_exit, new_exit, 1)

    old_init = '                "pending_stop": None,\n'
    new_init = old_init + '                "followthrough_mfe": 0.0,\n'
    if old_init not in source:
        raise RuntimeError("position init patch anchor missing")
    source = source.replace(old_init, new_init, 1)

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
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    if old_manage not in source:
        raise RuntimeError("management patch anchor missing")
    source = source.replace(old_manage, new_manage, 1)

    namespace = dict(h.__dict__)
    namespace.update({
        "FOLLOWTHROUGH_MINUTE": FOLLOWTHROUGH_MINUTE,
        "MIN_CLOSE_MOVE": MIN_CLOSE_MOVE,
        "MIN_MFE": MIN_MFE,
    })
    exec(compile(source, "<followthrough15_patch>", "exec"), namespace)
    return namespace["_run_hybrid"]


MODIFIED_RUN_HYBRID = install_followthrough_patch()


def prepare(universe):
    h.SYMBOLS = tuple(universe)
    h.DATA_START = base.DATA_START
    h.DATA_END = base.DATA_END
    h.TEST_START_MS = base.TEST_START_MS
    h.TEST_END_MS = base.TEST_END_MS
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    base._download_errors.clear()
    h._download_symbol = base._download_symbol_30d
    h._market_filters = archive_market_filters

    # The patched function is compiled in an isolated namespace, so keep its
    # run-specific globals synchronized with the current case as well.
    patched_globals = MODIFIED_RUN_HYBRID.__globals__
    patched_globals["SYMBOLS"] = tuple(universe)
    patched_globals["TEST_START_MS"] = base.TEST_START_MS
    patched_globals["TEST_END_MS"] = base.TEST_END_MS
    patched_globals["_market_filters"] = archive_market_filters

    raw = {}
    for index, symbol in enumerate(universe, 1):
        raw[symbol] = base._download_symbol_30d(symbol)
        print(f"DATA {index:02d}/{len(universe)} {symbol} bars={len(raw[symbol])}", flush=True)
    return raw


def summarize(result):
    return {
        "start_equity": result["start_equity"],
        "final_equity": result["final_equity"],
        "return_pct": result["return_pct"],
        "trades": result["trades"],
        "wins": result["wins"],
        "losses": result["losses"],
        "win_rate_pct": result["win_rate_pct"],
        "profit_factor": result["profit_factor"],
        "fees": result["fees"],
        "peak_marked_equity": result["peak_marked_equity"],
        "trough_marked_equity": result["trough_marked_equity"],
        "max_drawdown_pct": result["max_drawdown_pct"],
        "liquidations": result["liquidations"],
        "exit_reasons": result["exit_reasons"],
        "pipeline": result["pipeline"],
        "by_symbol": result["by_symbol"],
        "by_score": result["by_score"],
    }


def run_case(label, universe):
    print(f"START_CASE {label} universe={len(universe)}", flush=True)
    raw = prepare(universe)

    baseline = ORIGINAL_RUN_HYBRID(raw)
    print(
        f"BASELINE {label} final={baseline['final_equity']} trades={baseline['trades']}",
        flush=True,
    )

    # Keep downloaded prices and historical market filters cached; only the account path changes.
    modified = MODIFIED_RUN_HYBRID(raw)
    print(
        f"MODIFIED {label} final={modified['final_equity']} trades={modified['trades']} "
        f"ft15={modified['pipeline'].get('followthrough_15m_exits', 0)}",
        flush=True,
    )

    return {
        "label": label,
        "universe_size": len(universe),
        "symbols": list(universe),
        "baseline": summarize(baseline),
        "modified": summarize(modified),
        "modified_trades_detail": modified["trades_detail"],
        "data": {
            "bars_by_symbol": {symbol: len(raw[symbol]) for symbol in universe},
            "missing_symbols": [symbol for symbol in universe if not raw[symbol]],
            "download_errors": dict(base._download_errors),
        },
    }


def main():
    result_30 = run_case("30coin", UNIVERSE_30)
    result_10 = run_case("10coin", UNIVERSE_10)

    result = {
        "window": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
        "equity_protection": False,
        "followthrough_rule": {
            "evaluation": "completed minute 15 close, causal",
            "exit": "next 1m open with configured slippage",
            "keep_if_close_move_gte_pct": MIN_CLOSE_MOVE * 100.0,
            "or_keep_if_prior_mfe_gte_pct": MIN_MFE * 100.0,
            "existing_first_10m_early_failure": True,
        },
        "universe_30_definition": "First 30 symbols of the established fixed 40-symbol universe; no retrospective re-ranking for this run.",
        "runs_in_requested_order": [result_30, result_10],
        "caveats": [
            "The +0.30% / +0.77% thresholds were selected from the same 10-coin 30-day sample, so the 10-coin retest is in-sample.",
            "The 30-coin run shares the same calendar window and partially overlaps the symbols used to derive the thresholds.",
            "Funding cashflows are omitted; funding is only an entry filter, as in the prior backtests.",
            "Margin/liquidation and order-book execution remain simplified; minute OHLC uses adverse stop precedence.",
        ],
    }
    with open("followthrough15_30then10_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print("FOLLOWTHROUGH15_30THEN10=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
