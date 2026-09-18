import csv
import io
import json
import math
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ZECUSDT", "HYPEUSDT", "SUIUSDT", "ENAUSDT", "ARBUSDT",
)
DATA_START = date(2026, 9, 7)
DATA_END = date(2026, 9, 16)
TEST_START_MS = int(datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)
FEE = 0.0005
SLIPPAGE = 0.0002
LEVERAGE = 75
MARGIN_FRACTION = 0.50
INITIAL_STOP = 0.006
BREAKEVEN_TRIGGER = 0.005
BREAKEVEN_LOCK = 0.0015
ARM_WINDOW_MINUTES = 60
INVALIDATION_MOVE = 0.0075
TAKER_LONG_MIN = 1.20
TAKER_SHORT_MAX = 1.0 / TAKER_LONG_MIN
OI_MIN_RISE = 0.001
BTC_CONFIRM_MOVE = 0.001
MAX_FUNDING_ABS = 0.0003
MAX_PREMIUM_ABS = 0.003


def _get_json(url, params):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}", headers={"User-Agent": "NovaArb/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_symbol(symbol):
    rows = []
    current = DATA_START
    while current < DATA_END:
        stamp = current.isoformat()
        name = f"{symbol}-1m-{stamp}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/1m/{name}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "NovaArb/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        for row in csv.reader(io.StringIO(text)):
            if row and row[0].isdigit():
                rows.append((int(row[0]), *(float(row[i]) for i in range(1, 6))))
        current += timedelta(days=1)
    return rows


def _aggregate(bars, minutes):
    bucket_ms = minutes * 60_000
    out = []
    current_bucket = None
    agg = None
    for ts, opn, high, low, close, volume in bars:
        bucket = ts // bucket_ms
        if bucket != current_bucket:
            if agg is not None:
                out.append(tuple(agg))
            current_bucket = bucket
            agg = [bucket * bucket_ms, opn, high, low, close, volume]
        else:
            agg[2] = max(agg[2], high)
            agg[3] = min(agg[3], low)
            agg[4] = close
            agg[5] += volume
    if agg is not None:
        out.append(tuple(agg))
    return out


def _ema(values, period):
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    out = [value]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
        out.append(value)
    return out


def _score7_signals(bars_4h):
    closes = [bar[4] for bar in bars_4h]
    ema3 = _ema(closes, 3)
    ema8 = _ema(closes, 8)
    signals = {}
    for i in range(8, len(bars_4h)):
        bar = bars_4h[i]
        prev = bars_4h[i - 3:i]
        prev_high = max(x[2] for x in prev)
        prev_low = min(x[3] for x in prev)
        opn, high, low, close, volume = bar[1], bar[2], bar[3], bar[4], bar[5]
        prior_close = bars_4h[i - 1][4]
        ret1 = close / prior_close - 1.0
        body = (close - opn) / opn
        mean_vol = sum(x[5] for x in bars_4h[i - 5:i]) / 5.0
        vol_ratio = volume / mean_vol if mean_vol else 0.0
        span = high - low
        close_pos = (close - low) / span if span > 0 else 0.5

        long_ok = (
            ema3[i] > ema8[i]
            and close > prev_high
            and body >= 0.015
            and vol_ratio >= 1.5
            and close_pos >= 0.75
            and ret1 >= 0.015
        )
        short_ok = (
            ema3[i] < ema8[i]
            and close < prev_low
            and body <= -0.015
            and vol_ratio >= 1.5
            and close_pos <= 0.25
            and ret1 <= -0.015
        )
        if not long_ok and not short_ok:
            continue
        direction = 1 if long_ok else -1
        arm_ts = bar[0] + 4 * 60 * 60 * 1000
        signals[arm_ts] = {
            "direction": direction,
            "signal_close": close,
            "ret_pct": ret1 * 100.0,
            "vol_ratio": vol_ratio,
            "signal_ts": bar[0],
        }
    return signals


def _completed_index(series, close_time_ms, minutes):
    bucket_ms = minutes * 60_000
    usable_open = (close_time_ms // bucket_ms) * bucket_ms - bucket_ms
    lo, hi, answer = 0, len(series) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= usable_open:
            answer = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return answer


def _tf_vote(series, index, direction, minutes):
    if index < 6:
        return False
    close = series[index][4]
    if minutes == 1:
        reference, threshold = series[index - 3][4], 0.0015
    elif minutes == 3:
        reference, threshold = series[index - 2][4], 0.0025
    else:
        reference, threshold = series[index - 1][4], 0.0035
    return direction * (close / reference - 1.0) >= threshold


def _micro_confirmation(timestamp, data, direction):
    close_time = timestamp + 60_000
    bars_1m = data[1]
    i1 = data["index_1m"].get(timestamp)
    if i1 is None or i1 < 20:
        return None
    votes = 0
    for minutes in (1, 3, 5):
        series = data[minutes]
        idx = i1 if minutes == 1 else _completed_index(series, close_time, minutes)
        if idx >= 0 and _tf_vote(series, idx, direction, minutes):
            votes += 1
    if votes != 3:
        return None

    current = bars_1m[i1]
    prior_1m = bars_1m[i1 - 20:i1]
    mean_v1 = sum(x[5] for x in prior_1m) / len(prior_1m)
    vr1 = current[5] / mean_v1 if mean_v1 else 0.0
    recent_1m = bars_1m[i1 - 5:i1]
    bo1 = current[4] > max(x[2] for x in recent_1m) if direction == 1 else current[4] < min(x[3] for x in recent_1m)

    i3 = _completed_index(data[3], close_time, 3)
    if i3 < 10:
        return None
    bar3 = data[3][i3]
    prior3 = data[3][i3 - 10:i3]
    mean_v3 = sum(x[5] for x in prior3) / len(prior3)
    vr3 = bar3[5] / mean_v3 if mean_v3 else 0.0
    recent3 = data[3][i3 - 3:i3]
    bo3 = bar3[4] > max(x[2] for x in recent3) if direction == 1 else bar3[4] < min(x[3] for x in recent3)

    if not (vr1 >= 1.5 or vr3 >= 1.3):
        return None
    if not (bo1 or bo3):
        return None
    return {"votes": 3, "vr1": vr1, "vr3": vr3, "bo1": bo1, "bo3": bo3}


def _latest_before(items, timestamp, key="timestamp"):
    valid = [x for x in items if int(x[key]) <= timestamp]
    return max(valid, key=lambda x: int(x[key])) if valid else None


def _market_filters(symbol, timestamp, direction, btc_bars, btc_index):
    start = timestamp - 30 * 60_000
    end = timestamp + 60_000
    base = "https://fapi.binance.com"
    try:
        taker = _get_json(base + "/futures/data/takerlongshortRatio", {
            "symbol": symbol, "period": "5m", "startTime": start, "endTime": end, "limit": 20,
        })
        oi = _get_json(base + "/futures/data/openInterestHist", {
            "symbol": symbol, "period": "5m", "startTime": start, "endTime": end, "limit": 20,
        })
        top = _get_json(base + "/futures/data/topLongShortPositionRatio", {
            "symbol": symbol, "period": "5m", "startTime": start, "endTime": end, "limit": 20,
        })
        premium = _get_json(base + "/fapi/v1/premiumIndexKlines", {
            "symbol": symbol, "interval": "5m", "startTime": start, "endTime": end, "limit": 20,
        })
        funding = _get_json(base + "/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": timestamp - 24 * 60 * 60_000,
            "endTime": timestamp, "limit": 10,
        })
    except Exception as exc:
        return {"pass": False, "reason": f"market_data_error:{type(exc).__name__}"}

    t = _latest_before(taker, timestamp)
    o_now = _latest_before(oi, timestamp)
    o_old = _latest_before(oi, timestamp - 15 * 60_000)
    p = max((x for x in premium if int(x[0]) <= timestamp), key=lambda x: int(x[0]), default=None)
    f = max((x for x in funding if int(x["fundingTime"]) <= timestamp), key=lambda x: int(x["fundingTime"]), default=None)
    tp = _latest_before(top, timestamp)
    if None in (t, o_now, o_old, p, tp):
        return {"pass": False, "reason": "missing_market_data"}

    taker_ratio = float(t["buySellRatio"])
    taker_ok = taker_ratio >= TAKER_LONG_MIN if direction == 1 else taker_ratio <= TAKER_SHORT_MAX
    oi_now = float(o_now["sumOpenInterest"])
    oi_old = float(o_old["sumOpenInterest"])
    oi_change = oi_now / oi_old - 1.0 if oi_old else 0.0
    oi_ok = oi_change >= OI_MIN_RISE
    premium_close = float(p[4])
    premium_ok = abs(premium_close) <= MAX_PREMIUM_ABS
    funding_rate = float(f["fundingRate"]) if f else 0.0
    funding_ok = abs(funding_rate) <= MAX_FUNDING_ABS
    long_share = float(tp["longAccount"])
    short_share = float(tp["shortAccount"])
    if direction == 1:
        crowd_ok = long_share >= 0.55 or short_share >= 0.55
    else:
        crowd_ok = short_share >= 0.55 or long_share >= 0.55

    bi = btc_index.get(timestamp)
    btc_move = 0.0
    btc_ok = False
    if bi is not None and bi >= 10:
        btc_move = btc_bars[bi][4] / btc_bars[bi - 10][4] - 1.0
        btc_ok = direction * btc_move >= BTC_CONFIRM_MOVE

    passed = taker_ok and oi_ok and premium_ok and funding_ok and crowd_ok and btc_ok
    return {
        "pass": passed,
        "taker_ratio": taker_ratio,
        "oi_change_pct": oi_change * 100.0,
        "premium_pct": premium_close * 100.0,
        "funding_pct": funding_rate * 100.0,
        "top_long_share": long_share,
        "btc_move_pct": btc_move * 100.0,
        "checks": {
            "taker": taker_ok, "oi": oi_ok, "premium": premium_ok,
            "funding": funding_ok, "crowding": crowd_ok, "btc": btc_ok,
        },
    }


def _trail_distance(max_fav):
    if max_fav >= 0.040:
        return 0.003
    if max_fav >= 0.025:
        return 0.004
    if max_fav >= 0.015:
        return 0.005
    if max_fav >= 0.009:
        return 0.006
    return None


def run():
    raw = {symbol: _download_symbol(symbol) for symbol in SYMBOLS}
    datasets = {}
    signals = {}
    maps = {}
    for symbol, bars in raw.items():
        data = {
            1: bars,
            3: _aggregate(bars, 3),
            5: _aggregate(bars, 5),
            240: _aggregate(bars, 240),
            "index_1m": {bar[0]: i for i, bar in enumerate(bars)},
        }
        datasets[symbol] = data
        signals[symbol] = _score7_signals(data[240])
        maps[symbol] = {bar[0]: bar for bar in bars}

    btc_bars = raw["BTCUSDT"]
    btc_index = datasets["BTCUSDT"]["index_1m"]
    timestamps = sorted(ts for ts in maps["BTCUSDT"] if TEST_START_MS <= ts < TEST_END_MS)

    wallet = 100.0
    positions = {}
    armed = {}
    pending = {}
    trades = []
    filter_rejects = {}
    confirmations = 0
    fees = 0.0
    peak = 100.0
    trough = 100.0
    max_dd = 0.0
    max_concurrent = 0
    liquidations = 0

    def close_position(symbol, exit_price, reason, timestamp):
        nonlocal wallet, fees
        pos = positions.pop(symbol)
        move = pos["direction"] * (exit_price / pos["entry"] - 1.0)
        gross = pos["notional"] * move
        exit_fee = pos["notional"] * FEE
        wallet += gross - exit_fee
        fees += exit_fee
        net = gross - pos["entry_fee"] - exit_fee
        trades.append({
            "symbol": symbol,
            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
            "entry": pos["entry"], "exit": exit_price, "net": net,
            "margin": pos["margin"], "leverage": LEVERAGE,
            "reason": reason,
            "held_minutes": int((timestamp - pos["entry_ts"]) / 60_000) + 1,
            "max_fav_pct": pos["max_fav"] * 100.0,
            "filters": pos["filters"],
        })

    for timestamp in timestamps:
        for symbol in SYMBOLS:
            sig = signals[symbol].get(timestamp)
            if sig and symbol not in positions and symbol not in pending:
                armed[symbol] = {**sig, "arm_ts": timestamp, "expires": timestamp + ARM_WINDOW_MINUTES * 60_000}

        for symbol in list(armed):
            arm = armed[symbol]
            if symbol in positions or symbol in pending:
                armed.pop(symbol, None)
                continue
            if timestamp >= arm["expires"]:
                armed.pop(symbol, None)
                continue
            bar = maps[symbol].get(timestamp)
            if not bar:
                continue
            adverse = -arm["direction"] * (bar[4] / arm["signal_close"] - 1.0)
            if adverse > INVALIDATION_MOVE:
                armed.pop(symbol, None)
                continue
            confirm = _micro_confirmation(timestamp, datasets[symbol], arm["direction"])
            if not confirm:
                continue
            confirmations += 1
            filters = _market_filters(symbol, timestamp, arm["direction"], btc_bars, btc_index)
            if not filters.get("pass"):
                reason = filters.get("reason") or ",".join(k for k, v in filters.get("checks", {}).items() if not v)
                filter_rejects[reason] = filter_rejects.get(reason, 0) + 1
                armed.pop(symbol, None)
                continue
            idx = datasets[symbol]["index_1m"][timestamp]
            if idx + 1 >= len(raw[symbol]):
                armed.pop(symbol, None)
                continue
            entry_ts = raw[symbol][idx + 1][0]
            if entry_ts >= TEST_END_MS:
                armed.pop(symbol, None)
                continue
            pending[symbol] = {**arm, **confirm, "filters": filters, "entry_ts": entry_ts}
            armed.pop(symbol, None)

        for symbol, item in list(pending.items()):
            if item["entry_ts"] != timestamp or symbol in positions:
                continue
            used_margin = sum(x["margin"] for x in positions.values())
            margin = wallet * MARGIN_FRACTION
            if margin <= 0 or margin > max(0.0, wallet - used_margin):
                pending.pop(symbol, None)
                continue
            bar = maps[symbol][timestamp]
            raw_entry = bar[1]
            entry = raw_entry * (1.0 + item["direction"] * SLIPPAGE)
            notional = margin * LEVERAGE
            entry_fee = notional * FEE
            wallet -= entry_fee
            fees += entry_fee
            stop = entry * (1.0 - item["direction"] * INITIAL_STOP)
            liq_gap = max(0.003, 1.0 / LEVERAGE - 0.005)
            liq = entry * (1.0 - item["direction"] * liq_gap)
            positions[symbol] = {
                "direction": item["direction"], "entry": entry, "entry_ts": timestamp,
                "margin": margin, "notional": notional, "entry_fee": entry_fee,
                "stop": stop, "liq": liq, "best_close": entry, "max_fav": 0.0,
                "filters": item["filters"],
            }
            pending.pop(symbol, None)

        for symbol in list(positions):
            bar = maps[symbol].get(timestamp)
            if not bar:
                continue
            pos = positions[symbol]
            d = pos["direction"]
            # Stop is closer than the simplified liquidation threshold; assume stop-market executes first.
            stop_hit = bar[3] <= pos["stop"] if d == 1 else bar[2] >= pos["stop"]
            if stop_hit:
                exit_price = pos["stop"] * (1.0 - d * SLIPPAGE)
                close_position(symbol, exit_price, "TRAIL_STOP" if pos["max_fav"] >= BREAKEVEN_TRIGGER else "STOP", timestamp)
                continue
            liq_hit = bar[3] <= pos["liq"] if d == 1 else bar[2] >= pos["liq"]
            if liq_hit:
                liquidations += 1
                close_position(symbol, pos["liq"], "LIQUIDATION", timestamp)
                continue

            close = bar[4]
            favorable = d * (close / pos["entry"] - 1.0)
            if favorable > pos["max_fav"]:
                pos["max_fav"] = favorable
                pos["best_close"] = close
            if pos["max_fav"] >= BREAKEVEN_TRIGGER:
                be = pos["entry"] * (1.0 + d * BREAKEVEN_LOCK)
                if d == 1:
                    pos["stop"] = max(pos["stop"], be)
                else:
                    pos["stop"] = min(pos["stop"], be)
            distance = _trail_distance(pos["max_fav"])
            if distance is not None:
                candidate = pos["best_close"] * (1.0 - d * distance)
                if d == 1:
                    pos["stop"] = max(pos["stop"], candidate)
                else:
                    pos["stop"] = min(pos["stop"], candidate)

        marked = wallet
        for symbol, pos in positions.items():
            bar = maps[symbol].get(timestamp)
            if bar:
                marked += pos["notional"] * pos["direction"] * (bar[4] / pos["entry"] - 1.0)
        peak = max(peak, marked)
        trough = min(trough, marked)
        max_dd = max(max_dd, (peak - marked) / peak if peak > 0 else 0.0)
        max_concurrent = max(max_concurrent, len(positions))

    final_ts = timestamps[-1]
    for symbol in list(positions):
        bar = maps[symbol][final_ts]
        pos = positions[symbol]
        exit_price = bar[4] * (1.0 - pos["direction"] * SLIPPAGE)
        close_position(symbol, exit_price, "END", final_ts)

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    gross_win = sum(t["net"] for t in wins)
    gross_loss = -sum(t["net"] for t in losses)
    by_symbol = {}
    for symbol in SYMBOLS:
        subset = [t for t in trades if t["symbol"] == symbol]
        by_symbol[symbol] = {
            "trades": len(subset), "wins": sum(t["net"] > 0 for t in subset),
            "net": round(sum(t["net"] for t in subset), 4),
        }

    result = {
        "window": "2026-09-09T00:00:00Z/2026-09-16T00:00:00Z",
        "symbols": list(SYMBOLS),
        "rules": {
            "entry": "4H score7 + 3/3 1m/3m/5m + taker + rising OI + nonextreme funding/premium + crowding + BTC confirmation",
            "leverage": LEVERAGE, "margin_fraction": MARGIN_FRACTION,
            "take_profit": None, "initial_stop_underlying": INITIAL_STOP,
            "trailing": "BE at +0.5%; then 0.6/0.5/0.4/0.3% trail as MFE reaches 0.9/1.5/2.5/4.0%",
            "time_exit": None, "fee_each_side": FEE, "slippage_each_side": SLIPPAGE,
        },
        "portfolio": {
            "start_equity": 100.0, "final_equity": round(wallet, 4),
            "return_pct": round((wallet / 100.0 - 1.0) * 100.0, 2),
            "trades": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(100.0 * len(wins) / len(trades), 2) if trades else 0.0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
            "fees": round(fees, 4), "max_drawdown_pct": round(max_dd * 100.0, 2),
            "peak_marked_equity": round(peak, 4), "trough_marked_equity": round(trough, 4),
            "max_concurrent": max_concurrent, "liquidations": liquidations,
            "micro_confirmations": confirmations, "filter_rejects": filter_rejects,
            "by_symbol": by_symbol, "trades_detail": trades,
        },
    }
    print("TEN_SCORE7PLUS_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    run()
