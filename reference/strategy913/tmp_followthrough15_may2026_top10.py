import inspect
import json
import textwrap
from datetime import date, datetime, timezone

import tmp_hybrid_zec_score7plus_60d as h
from tmp_hybrid_archive_market import archive_market_filters

UNIVERSE_10 = (
    "DOGEUSDT", "BTCUSDT", "XRPUSDT", "TIAUSDT", "1000PEPEUSDT",
    "DOTUSDT", "UNIUSDT", "SUIUSDT", "WIFUSDT", "ETCUSDT",
)

DATA_START = date(2026, 4, 29)
DATA_END = date(2026, 6, 1)
TEST_START_MS = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)

FOLLOWTHROUGH_MINUTE = 15
MIN_CLOSE_MOVE = 0.0030
MIN_MFE = 0.0077

ORIGINAL_RUN_HYBRID = h._run_hybrid


def _download_symbol_may(symbol):
    if symbol in h._price_cache:
        return h._price_cache[symbol]
    rows = []
    start_ms = int(datetime(2026, 4, 29, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for year, month in ((2026, 4), (2026, 5)):
        stamp = f"{year:04d}-{month:02d}"
        name = f"{symbol}-1m-{stamp}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{name}"
        )
        try:
            source = h._read_zip_rows(url)
        except Exception as exc:
            print(f"DOWNLOAD_ERROR {symbol} {stamp} {type(exc).__name__}", flush=True)
            continue
        for row in source:
            if row and row[0].isdigit():
                ts = int(row[0])
                if start_ms <= ts < end_ms:
                    rows.append((ts, *(float(row[i]) for i in range(1, 6))))
    rows.sort(key=lambda x: x[0])
    h._price_cache[symbol] = rows
    return rows


def install_followthrough_patch():
    source = textwrap.dedent(inspect.getsource(ORIGINAL_RUN_HYBRID))

    old_stats = '        "early_failure_exits": 0,\n'
    source = source.replace(
        old_stats,
        old_stats + '        "followthrough_15m_exits": 0,\n',
        1,
    )

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
    source = source.replace(old_exit, new_exit, 1)

    old_init = '                "pending_stop": None,\n'
    source = source.replace(
        old_init,
        old_init + '                "followthrough_mfe": 0.0,\n',
        1,
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
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
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


def _prepare():
    h.SYMBOLS = UNIVERSE_10
    h.DATA_START = DATA_START
    h.DATA_END = DATA_END
    h.TEST_START_MS = TEST_START_MS
    h.TEST_END_MS = TEST_END_MS
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    h._download_symbol = _download_symbol_may
    h._market_filters = archive_market_filters

    patched_globals = MODIFIED_RUN_HYBRID.__globals__
    patched_globals["SYMBOLS"] = UNIVERSE_10
    patched_globals["TEST_START_MS"] = TEST_START_MS
    patched_globals["TEST_END_MS"] = TEST_END_MS
    patched_globals["_market_filters"] = archive_market_filters

    raw = {}
    for index, symbol in enumerate(UNIVERSE_10, 1):
        raw[symbol] = _download_symbol_may(symbol)
        print(f"DATA {index:02d}/10 {symbol} bars={len(raw[symbol])}", flush=True)
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


def main():
    raw = _prepare()
    missing = [symbol for symbol, bars in raw.items() if not bars]
    if missing:
        raise RuntimeError(f"missing price data: {missing}")

    baseline = ORIGINAL_RUN_HYBRID(raw)
    print(
        f"BASELINE final={baseline['final_equity']} trades={baseline['trades']}",
        flush=True,
    )

    modified = MODIFIED_RUN_HYBRID(raw)
    print(
        f"MODIFIED final={modified['final_equity']} trades={modified['trades']} "
        f"ft15={modified['pipeline'].get('followthrough_15m_exits', 0)}",
        flush=True,
    )

    result = {
        "window": "2026-05-01T00:00:00Z/2026-06-01T00:00:00Z",
        "selection": "Randomly selected calendar month from Jan-Jun 2026: May 2026",
        "symbols": list(UNIVERSE_10),
        "equity_protection": False,
        "followthrough_rule": {
            "evaluation": "completed minute 15 close, causal",
            "exit": "next 1m open with configured slippage",
            "keep_if_close_move_gte_pct": 0.30,
            "or_keep_if_prior_mfe_gte_pct": 0.77,
            "existing_first_10m_early_failure": True,
        },
        "baseline": summarize(baseline),
        "modified": summarize(modified),
        "modified_trades_detail": modified["trades_detail"],
        "data": {
            "bars_by_symbol": {symbol: len(raw[symbol]) for symbol in UNIVERSE_10},
            "missing_symbols": missing,
        },
        "caveats": [
            "This calendar month is temporally non-overlapping with the Aug-Sep sample used to choose the 15-minute thresholds.",
            "Funding cashflows are omitted; funding is only an entry filter.",
            "Margin/liquidation and order-book execution remain simplified; minute OHLC uses adverse stop precedence.",
        ],
    }
    with open("followthrough15_may2026_top10_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print("FOLLOWTHROUGH15_MAY2026_TOP10=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
