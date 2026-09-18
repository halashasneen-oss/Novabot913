import csv
import io
import json
import math
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

SYMBOLS = ("ARBUSDT", "HYPEUSDT", "XRPUSDT", "SUIUSDT", "DOGEUSDT")
DATA_START = date(2026, 9, 6)
DATA_END = date(2026, 9, 16)
TEST_START_MS = int(datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)
FEE = 0.0005
SLIPPAGE = 0.0002
MAX_HOLD_BARS = 3


def _download(symbol):
    rows = []
    current = DATA_START
    while current < DATA_END:
        stamp = current.isoformat()
        name = f"{symbol}-4h-{stamp}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/4h/{name}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "NovaArb/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        for row in csv.reader(io.StringIO(text)):
            if row and row[0].isdigit():
                rows.append(
                    (
                        int(row[0]),
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                    )
                )
        current += timedelta(days=1)
    return rows


def _ema(values, period):
    alpha = 2.0 / (period + 1.0)
    out = []
    current = values[0]
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def _signals(bars):
    closes = [bar[4] for bar in bars]
    ema3 = _ema(closes, 3)
    ema8 = _ema(closes, 8)
    signals = {}
    for i in range(8, len(bars) - 1):
        ts, open_price, high, low, close, volume = bars[i]
        prev3 = bars[i - 3 : i]
        prev5 = bars[i - 5 : i]
        prev3_hi = max(bar[2] for bar in prev3)
        prev3_lo = min(bar[3] for bar in prev3)
        vol_avg5 = sum(bar[5] for bar in prev5) / len(prev5)
        vol_ratio = volume / vol_avg5 if vol_avg5 else 0.0
        ret1 = close / bars[i - 1][4] - 1.0
        body = (close - open_price) / open_price
        body_abs = abs(body)
        close_pos = (close - low) / (high - low) if high > low else 0.5

        long_score = 0
        short_score = 0
        if ema3[i] > ema8[i]:
            long_score += 1
        elif ema3[i] < ema8[i]:
            short_score += 1

        long_breakout = close > prev3_hi
        short_breakout = close < prev3_lo
        if long_breakout:
            long_score += 2
        if short_breakout:
            short_score += 2

        if body_abs >= 0.015:
            if body > 0:
                long_score += 1
            elif body < 0:
                short_score += 1

        if vol_ratio >= 1.5:
            if body > 0:
                long_score += 1
            elif body < 0:
                short_score += 1

        if close_pos >= 0.75:
            long_score += 1
        elif close_pos <= 0.25:
            short_score += 1

        if ret1 >= 0.015:
            long_score += 1
        elif ret1 <= -0.015:
            short_score += 1

        if long_score > short_score and long_score >= 5 and long_breakout:
            direction = 1
            score = long_score
        elif short_score > long_score and short_score >= 5 and short_breakout:
            direction = -1
            score = short_score
        else:
            continue

        signals[ts] = {
            "direction": direction,
            "score": score,
            "vol_ratio": vol_ratio,
            "ret1": ret1,
            "body_abs": body_abs,
            "close_pos": close_pos,
            "signal_close": close,
            "entry_ts": bars[i + 1][0],
        }
    return signals


def _params(score):
    if score >= 6:
        return 0.50, 75
    return 0.35, 50


def _stop_risk(leverage):
    liqdist = max(0.002, 1.0 / leverage - 0.005)
    return min(0.028, max(0.006, liqdist * 0.55))


def _trail_distance(risk):
    return max(0.006, min(0.012, 0.7 * risk))


def _run(raw):
    bar_maps = {symbol: {bar[0]: bar for bar in raw[symbol]} for symbol in SYMBOLS}
    index_maps = {
        symbol: {bar[0]: idx for idx, bar in enumerate(raw[symbol])}
        for symbol in SYMBOLS
    }
    signals = {symbol: _signals(raw[symbol]) for symbol in SYMBOLS}
    timestamps = sorted(
        ts for ts in bar_maps[SYMBOLS[0]] if TEST_START_MS <= ts < TEST_END_MS
    )

    wallet = 100.0
    positions = {}
    pending = {}
    trades = []
    fees = 0.0
    rejected_margin = 0
    max_concurrent = 0
    peak = 100.0
    trough = 100.0
    max_dd = 0.0
    liquidations = 0

    def close_position(symbol, exit_price, reason, ts):
        nonlocal wallet, fees
        pos = positions[symbol]
        move = pos["direction"] * (exit_price / pos["entry"] - 1.0)
        gross = pos["notional"] * move
        exit_fee = pos["notional"] * FEE
        wallet += gross - exit_fee
        fees += exit_fee
        net = gross - exit_fee - pos["entry_fee"]
        trades.append(
            {
                "symbol": symbol,
                "score": pos["score"],
                "direction": "LONG" if pos["direction"] == 1 else "SHORT",
                "leverage": pos["leverage"],
                "margin": pos["margin"],
                "entry": pos["entry"],
                "exit": exit_price,
                "net": net,
                "reason": reason,
                "held_bars": pos["held_bars"],
                "signal_vol_ratio": pos["vol_ratio"],
                "signal_ret_pct": pos["ret1"] * 100.0,
                "exit_ts": ts,
            }
        )
        positions.pop(symbol)

    for ts in timestamps:
        candidates = []
        for symbol, item in list(pending.items()):
            if item["entry_ts"] == ts and symbol not in positions:
                candidates.append((symbol, item))
        candidates.sort(
            key=lambda pair: (pair[1]["score"], pair[1]["vol_ratio"], abs(pair[1]["ret1"])),
            reverse=True,
        )
        for symbol, item in candidates:
            margin_frac, leverage = _params(item["score"])
            used_margin = sum(pos["margin"] for pos in positions.values())
            required = wallet * margin_frac
            available = max(0.0, wallet - used_margin)
            if required <= 0 or required > available + 1e-9:
                rejected_margin += 1
                pending.pop(symbol, None)
                continue
            bar = bar_maps[symbol][ts]
            direction = item["direction"]
            entry = bar[1] * (1.0 + direction * SLIPPAGE)
            notional = required * leverage
            entry_fee = notional * FEE
            wallet -= entry_fee
            fees += entry_fee
            risk = _stop_risk(leverage)
            liqdist = max(0.003, 1.0 / leverage - 0.005)
            positions[symbol] = {
                **item,
                "entry": entry,
                "margin": required,
                "notional": notional,
                "entry_fee": entry_fee,
                "leverage": leverage,
                "risk": risk,
                "trail_distance": _trail_distance(risk),
                "stop": entry * (1.0 - direction * risk),
                "liq": entry * (1.0 - direction * liqdist),
                "best_close": entry,
                "trail_active": False,
                "held_bars": 0,
            }
            max_concurrent = max(max_concurrent, len(positions))
            pending.pop(symbol, None)

        for symbol in list(positions):
            pos = positions[symbol]
            bar = bar_maps[symbol].get(ts)
            if bar is None:
                continue
            open_price, high, low, close = bar[1], bar[2], bar[3], bar[4]
            direction = pos["direction"]
            pos["held_bars"] += 1

            liq_gap = open_price <= pos["liq"] if direction == 1 else open_price >= pos["liq"]
            if liq_gap:
                wallet -= pos["margin"]
                trades.append(
                    {
                        "symbol": symbol,
                        "score": pos["score"],
                        "direction": "LONG" if direction == 1 else "SHORT",
                        "leverage": pos["leverage"],
                        "margin": pos["margin"],
                        "entry": pos["entry"],
                        "exit": open_price,
                        "net": -pos["margin"] - pos["entry_fee"],
                        "reason": "LIQ_GAP",
                        "held_bars": pos["held_bars"],
                        "signal_vol_ratio": pos["vol_ratio"],
                        "signal_ret_pct": pos["ret1"] * 100.0,
                        "exit_ts": ts,
                    }
                )
                liquidations += 1
                positions.pop(symbol)
                continue

            stop_hit = low <= pos["stop"] if direction == 1 else high >= pos["stop"]
            if stop_hit:
                raw_exit = min(pos["stop"], open_price) if direction == 1 else max(pos["stop"], open_price)
                exit_price = raw_exit * (1.0 - direction * SLIPPAGE)
                close_position(symbol, exit_price, "TRAIL_STOP" if pos["trail_active"] else "STOP", ts)
                continue

            favorable_close = direction * (close / pos["entry"] - 1.0)
            if direction == 1:
                pos["best_close"] = max(pos["best_close"], close)
            else:
                pos["best_close"] = min(pos["best_close"], close)

            if favorable_close >= 1.5 * pos["risk"]:
                pos["trail_active"] = True
                be = pos["entry"] * (1.0 + direction * 0.0015)
                if direction == 1:
                    trail = pos["best_close"] * (1.0 - pos["trail_distance"])
                    pos["stop"] = max(pos["stop"], be, trail)
                else:
                    trail = pos["best_close"] * (1.0 + pos["trail_distance"])
                    pos["stop"] = min(pos["stop"], be, trail)

            if pos["held_bars"] >= MAX_HOLD_BARS:
                exit_price = close * (1.0 - direction * SLIPPAGE)
                close_position(symbol, exit_price, "TIME_12H", ts)

        for symbol in SYMBOLS:
            if symbol in positions or symbol in pending:
                continue
            idx = index_maps[symbol].get(ts)
            if idx is None:
                continue
            item = signals[symbol].get(ts)
            if item is None or item["entry_ts"] >= TEST_END_MS:
                continue
            pending[symbol] = item

        marked = wallet
        for symbol, pos in positions.items():
            bar = bar_maps[symbol].get(ts)
            if bar is None:
                continue
            move = pos["direction"] * (bar[4] / pos["entry"] - 1.0)
            marked += pos["notional"] * move
        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        if wallet <= 0 or marked <= 0:
            break

    if timestamps:
        last_ts = timestamps[-1]
        for symbol in list(positions):
            pos = positions[symbol]
            close = bar_maps[symbol][last_ts][4]
            exit_price = close * (1.0 - pos["direction"] * SLIPPAGE)
            close_position(symbol, exit_price, "END", last_ts)

    wins = sum(1 for trade in trades if trade["net"] > 0)
    gross_profit = sum(max(0.0, trade["net"]) for trade in trades)
    gross_loss = -sum(min(0.0, trade["net"]) for trade in trades)
    reasons = {}
    by_symbol = {}
    by_score = {}
    for trade in trades:
        reasons[trade["reason"]] = reasons.get(trade["reason"], 0) + 1
    for symbol in SYMBOLS:
        subset = [trade for trade in trades if trade["symbol"] == symbol]
        by_symbol[symbol] = {
            "trades": len(subset),
            "wins": sum(1 for t in subset if t["net"] > 0),
            "net": round(sum(t["net"] for t in subset), 4),
        }
    for score in sorted({trade["score"] for trade in trades}):
        subset = [trade for trade in trades if trade["score"] == score]
        by_score[str(score)] = {
            "trades": len(subset),
            "wins": sum(1 for t in subset if t["net"] > 0),
            "net": round(sum(t["net"] for t in subset), 4),
        }

    return {
        "start_equity": 100.0,
        "final_equity": round(wallet, 4),
        "return_pct": round((wallet / 100.0 - 1.0) * 100.0, 2),
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": round(100.0 * wins / len(trades), 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else math.inf,
        "fees": round(fees, 4),
        "peak_marked_equity": round(peak, 4),
        "trough_marked_equity": round(trough, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "max_concurrent": max_concurrent,
        "rejected_margin": rejected_margin,
        "liquidations": liquidations,
        "exit_reasons": reasons,
        "by_symbol": by_symbol,
        "by_score": by_score,
        "trades_detail": [
            {
                **trade,
                "margin": round(trade["margin"], 4),
                "entry": round(trade["entry"], 8),
                "exit": round(trade["exit"], 8),
                "net": round(trade["net"], 4),
                "signal_vol_ratio": round(trade["signal_vol_ratio"], 3),
                "signal_ret_pct": round(trade["signal_ret_pct"], 3),
            }
            for trade in trades
        ],
    }


@pytest.mark.skipif(sys.version_info[:2] != (3, 11), reason="temporary network backtest")
def test_tmp_fivecoin_explosive_breakout():
    raw = {symbol: _download(symbol) for symbol in SYMBOLS}
    result = {
        "window": "2026-09-09T00:00:00Z/2026-09-16T00:00:00Z",
        "symbols": list(SYMBOLS),
        "portfolio": _run(raw),
        "rules": {
            "timeframe": "4h",
            "ema": [3, 8],
            "breakout": "close beyond prior 3-bar high/low required",
            "minimum_score": 5,
            "score5": "35% margin, 50x",
            "score6plus": "50% margin, 75x",
            "body_threshold": 0.015,
            "volume_ratio_threshold": 1.5,
            "momentum_threshold": 0.015,
            "max_hold_bars": 3,
            "fee_each_side": FEE,
            "slippage_each_side": SLIPPAGE,
            "funding": "not included",
        },
    }
    print("EXPLOSIVE_BREAKOUT_RESULT=" + json.dumps(result, sort_keys=True))
