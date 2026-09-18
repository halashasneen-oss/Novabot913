import json
import math
from datetime import date, datetime, timedelta, timezone

import tmp_hybrid_zec_score7plus_60d as h
from tmp_hybrid_archive_market import archive_market_filters

UNIVERSE = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "TRXUSDT",
    "DOTUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT",
    "AAVEUSDT", "UNIUSDT", "ETCUSDT", "ATOMUSDT", "FILUSDT",
    "ICPUSDT", "ARBUSDT", "OPUSDT", "ENAUSDT", "WIFUSDT",
    "1000PEPEUSDT", "FETUSDT", "INJUSDT", "RENDERUSDT", "SEIUSDT",
    "TAOUSDT", "TIAUSDT", "JUPUSDT", "1000BONKUSDT", "HYPEUSDT",
    "ZECUSDT", "CRVUSDT", "PENDLEUSDT", "LDOUSDT", "TONUSDT",
)

DATA_START = date(2026, 8, 15)
DATA_END = date(2026, 9, 16)
TEST_START_MS = int(datetime(2026, 8, 17, tzinfo=timezone.utc).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)
START_EQUITY = 100.0
LADDER = ((2.0, 0.80), (2.5, 0.85), (3.0, 0.90), (3.5, 0.925))

_download_errors = {}


def _append_rows(rows, url, start_ms, end_ms, symbol):
    try:
        source = h._read_zip_rows(url)
    except Exception as exc:
        key = f"{symbol}:{type(exc).__name__}"
        _download_errors[key] = _download_errors.get(key, 0) + 1
        return
    for row in source:
        if row and row[0].isdigit():
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                rows.append((ts, *(float(row[i]) for i in range(1, 6))))


def _download_symbol_30d(symbol):
    if symbol in h._price_cache:
        return h._price_cache[symbol]
    rows = []
    start_ms = int(datetime.combine(DATA_START, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(DATA_END, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)

    stamp = "2026-08"
    name = f"{symbol}-1m-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        f"{symbol}/1m/{name}"
    )
    _append_rows(rows, url, start_ms, end_ms, symbol)

    current = date(2026, 9, 1)
    while current < DATA_END:
        stamp = current.isoformat()
        name = f"{symbol}-1m-{stamp}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/1m/{name}"
        )
        _append_rows(rows, url, start_ms, end_ms, symbol)
        current += timedelta(days=1)

    rows.sort(key=lambda x: x[0])
    h._price_cache[symbol] = rows
    return rows


def _retention(peak):
    multiple = peak / START_EQUITY
    keep = None
    for threshold, retention in LADDER:
        if multiple >= threshold:
            keep = retention
    return keep


def _baseline_replay(trades):
    wallet = START_EQUITY
    events = {}
    for idx, trade in enumerate(trades):
        entry_ts = trade["exit_ts"] - (trade["held_minutes"] - 1) * 60_000
        events.setdefault(entry_ts, []).append(("entry", idx, trade))
        events.setdefault(trade["exit_ts"], []).append(("exit", idx, trade))
    active = {}
    for ts in sorted(events):
        for kind, idx, trade in sorted(events[ts], key=lambda x: 0 if x[0] == "entry" else 1):
            notional = trade["margin"] * trade["leverage"]
            if kind == "entry":
                wallet -= notional * h.FEE
                active[idx] = trade
            else:
                if idx not in active:
                    continue
                direction = 1 if trade["direction"] == "LONG" else -1
                move = direction * (trade["exit"] / trade["entry"] - 1.0)
                if trade["reason"] == "LIQ_GAP":
                    wallet -= trade["margin"]
                else:
                    wallet += notional * move - notional * h.FEE
                active.pop(idx, None)
    return wallet


def _apply_912(trades, raw):
    entries = {}
    exits = {}
    first_ts = None
    last_ts = None
    for idx, trade in enumerate(trades):
        entry_ts = trade["exit_ts"] - (trade["held_minutes"] - 1) * 60_000
        entries.setdefault(entry_ts, []).append((idx, trade))
        exits.setdefault(trade["exit_ts"], []).append((idx, trade))
        first_ts = entry_ts if first_ts is None else min(first_ts, entry_ts)
        last_ts = trade["exit_ts"] if last_ts is None else max(last_ts, trade["exit_ts"])

    if first_ts is None:
        return {
            "final_equity": START_EQUITY,
            "return_pct": 0.0,
            "peak_marked_equity": START_EQUITY,
            "max_marked_drawdown_pct": 0.0,
            "trades_started": 0,
            "trades_closed_original": 0,
            "trades_closed_by_lock": 0,
            "halted": False,
            "lock_event": None,
            "profit_factor": 0.0,
            "wins": 0,
            "losses": 0,
        }

    maps = {symbol: {bar[0]: bar for bar in bars} for symbol, bars in raw.items()}
    wallet = START_EQUITY
    active = {}
    completed = []
    peak = START_EQUITY
    trough = START_EQUITY
    max_dd = 0.0
    pending_lock_ts = None
    pending_lock = None
    halted = False
    started = 0
    original_closed = 0
    lock_closed = 0

    ts = first_ts
    while ts <= last_ts + 60_000 and not halted:
        if pending_lock_ts is not None and ts >= pending_lock_ts:
            for idx, pos in list(active.items()):
                trade = pos["trade"]
                bar = maps.get(trade["symbol"], {}).get(ts)
                if bar is None:
                    continue
                direction = pos["direction"]
                raw_open = bar[1]
                exit_price = raw_open * (1.0 - direction * h.SLIPPAGE)
                move = direction * (exit_price / trade["entry"] - 1.0)
                gross = pos["notional"] * move
                exit_fee = pos["notional"] * h.FEE
                wallet += gross - exit_fee
                net = gross - pos["entry_fee"] - exit_fee
                completed.append(net)
                active.pop(idx, None)
                lock_closed += 1
            halted = True
            if pending_lock is not None:
                pending_lock["actual_exit_ts"] = ts
                pending_lock["final_equity"] = wallet
            break

        # Match the original engine ordering: entries first, then position management/exits.
        for idx, trade in entries.get(ts, []):
            notional = trade["margin"] * trade["leverage"]
            entry_fee = notional * h.FEE
            wallet -= entry_fee
            active[idx] = {
                "trade": trade,
                "direction": 1 if trade["direction"] == "LONG" else -1,
                "notional": notional,
                "entry_fee": entry_fee,
            }
            started += 1

        for idx, trade in exits.get(ts, []):
            pos = active.get(idx)
            if pos is None:
                continue
            if trade["reason"] == "LIQ_GAP":
                wallet -= trade["margin"]
                net = -trade["margin"] - pos["entry_fee"]
            else:
                move = pos["direction"] * (trade["exit"] / trade["entry"] - 1.0)
                gross = pos["notional"] * move
                exit_fee = pos["notional"] * h.FEE
                wallet += gross - exit_fee
                net = gross - pos["entry_fee"] - exit_fee
            completed.append(net)
            active.pop(idx, None)
            original_closed += 1

        marked = wallet
        for pos in active.values():
            trade = pos["trade"]
            bar = maps.get(trade["symbol"], {}).get(ts)
            if bar is None:
                continue
            close = bar[4]
            move = pos["direction"] * (close / trade["entry"] - 1.0)
            marked += pos["notional"] * move

        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        keep = _retention(peak)
        if keep is not None and marked <= peak * keep:
            pending_lock_ts = ts + 60_000
            pending_lock = {
                "trigger_ts": ts,
                "scheduled_exit_ts": pending_lock_ts,
                "peak_marked": peak,
                "retention": keep,
                "lock_floor": peak * keep,
                "marked_at_trigger": marked,
                "open_positions": len(active),
            }
            if not active:
                halted = True
                pending_lock["actual_exit_ts"] = ts
                pending_lock["final_equity"] = wallet
                break

        ts += 60_000

    if not halted:
        # All baseline exits should already have been applied.
        for idx, pos in list(active.items()):
            trade = pos["trade"]
            move = pos["direction"] * (trade["exit"] / trade["entry"] - 1.0)
            gross = pos["notional"] * move
            exit_fee = pos["notional"] * h.FEE
            wallet += gross - exit_fee
            completed.append(gross - pos["entry_fee"] - exit_fee)
            active.pop(idx, None)
            original_closed += 1

    gross_profit = sum(max(0.0, x) for x in completed)
    gross_loss = -sum(min(0.0, x) for x in completed)
    wins = sum(1 for x in completed if x > 0)
    return {
        "final_equity": round(wallet, 6),
        "return_pct": round((wallet / START_EQUITY - 1.0) * 100.0, 3),
        "peak_marked_equity": round(peak, 6),
        "trough_marked_equity": round(trough, 6),
        "max_marked_drawdown_pct": round(max_dd * 100.0, 3),
        "trades_started": started,
        "trades_closed_original": original_closed,
        "trades_closed_by_lock": lock_closed,
        "trades_skipped_after_halt": max(0, len(trades) - started),
        "wins": wins,
        "losses": len(completed) - wins,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (math.inf if gross_profit else 0.0),
        "halted": halted,
        "lock_event": pending_lock,
    }


def main():
    h.SYMBOLS = UNIVERSE
    h.DATA_START = DATA_START
    h.DATA_END = DATA_END
    h.TEST_START_MS = TEST_START_MS
    h.TEST_END_MS = TEST_END_MS
    h._price_cache.clear()
    h._metrics_cache.clear()
    h._market_cache.clear()
    h._download_symbol = _download_symbol_30d
    h._market_filters = archive_market_filters

    raw = {}
    for i, symbol in enumerate(UNIVERSE, start=1):
        raw[symbol] = _download_symbol_30d(symbol)
        print(f"DATA {i:02d}/{len(UNIVERSE)} {symbol} bars={len(raw[symbol])}", flush=True)

    baseline = h._run_hybrid(raw)
    replay_final = _baseline_replay(baseline["trades_detail"])
    replay_error = abs(replay_final - baseline["final_equity"])
    strategy_912 = _apply_912(baseline["trades_detail"], raw)

    bars_by_symbol = {symbol: len(raw[symbol]) for symbol in UNIVERSE}
    missing_symbols = [symbol for symbol, count in bars_by_symbol.items() if count == 0]
    result = {
        "strategy": "912",
        "window": "2026-08-17T00:00:00Z/2026-09-16T00:00:00Z",
        "universe_size": len(UNIVERSE),
        "symbols": list(UNIVERSE),
        "data": {
            "bars_by_symbol": bars_by_symbol,
            "missing_symbols": missing_symbols,
            "download_errors": _download_errors,
        },
        "baseline_hybrid_without_equity_lock": baseline,
        "baseline_replay_final": replay_final,
        "baseline_replay_error": replay_error,
        "baseline_replay_valid": replay_error < 0.02,
        "strategy_912_result": strategy_912,
        "strategy_912_ladder": [list(x) for x in LADDER],
        "method": "Same Hybrid entry/exit rules as 912; global progressive equity lock uses completed 1m marked equity and exits all open positions on next 1m open with slippage, then halts.",
        "caveats": [
            "This 30-day/40-coin run is a new universe test but overlaps the historical period used during development, so it is not fully out-of-sample.",
            "Funding rates are entry filters; funding cashflows are not charged to PnL.",
            "Margin/liquidation and order-book execution remain simplified as in the original 912 backtest.",
        ],
    }
    if not result["baseline_replay_valid"]:
        raise RuntimeError(f"baseline replay mismatch: {replay_error}")
    with open("strategy_912_40coin_30d_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print("STRATEGY_912_40COIN_30D=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
