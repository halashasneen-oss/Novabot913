import contextlib
import csv
import io
import json
import math
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

import tmp_ten_score7plus_flow as s7
import tmp_zec_extreme_reference as zec

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ZECUSDT", "HYPEUSDT", "SUIUSDT", "ENAUSDT", "ARBUSDT",
)
DATA_START = date(2026, 7, 15)
DATA_END = date(2026, 9, 16)
TEST_START_MS = int(datetime(2026, 7, 17, tzinfo=timezone.utc).timestamp() * 1000)
TEST_END_MS = int(datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp() * 1000)
FEE = 0.0005
SLIPPAGE = 0.0002
ARM_WINDOW_MINUTES = 60
RETEST_WINDOW_MINUTES = 15
INVALIDATION_MOVE = 0.0075
RETEST_APPROACH = 0.0015
RETEST_FAIL = 0.0025
EARLY_FAILURE_MINUTES = 10
MAX_HOLD_MINUTES = 720
BE_LOCK = 0.0015
TAKER_LONG_MIN = 1.20
TAKER_SHORT_MAX = 1.0 / TAKER_LONG_MIN
OI_MIN_RISE = 0.001
BTC_CONFIRM_MOVE = 0.001
MAX_FUNDING_ABS = 0.0003
MAX_PREMIUM_ABS = 0.003

_price_cache = {}
_metrics_cache = {}
_market_cache = {}


def _request_bytes(url, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NovaArb/1.0"})
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
    raise last


def _get_json(url, params, attempts=3):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    return json.loads(_request_bytes(full, attempts=attempts).decode("utf-8"))


def _read_zip_rows(url):
    payload = _request_bytes(url)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def _download_symbol(symbol):
    if symbol in _price_cache:
        return _price_cache[symbol]
    rows = []
    start_ms = int(datetime.combine(DATA_START, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(DATA_END, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    for year, month in ((2026, 7), (2026, 8)):
        stamp = f"{year:04d}-{month:02d}"
        name = f"{symbol}-1m-{stamp}.zip"
        url = "https://data.binance.vision/data/futures/um/monthly/klines/" + f"{symbol}/1m/{name}"
        for row in _read_zip_rows(url):
            if row and row[0].isdigit():
                ts = int(row[0])
                if start_ms <= ts < end_ms:
                    rows.append((ts, *(float(row[i]) for i in range(1, 6))))
    current = date(2026, 9, 1)
    while current < DATA_END:
        stamp = current.isoformat()
        name = f"{symbol}-1m-{stamp}.zip"
        url = "https://data.binance.vision/data/futures/um/daily/klines/" + f"{symbol}/1m/{name}"
        for row in _read_zip_rows(url):
            if row and row[0].isdigit():
                ts = int(row[0])
                if start_ms <= ts < end_ms:
                    rows.append((ts, *(float(row[i]) for i in range(1, 6))))
        current += timedelta(days=1)
    rows.sort(key=lambda x: x[0])
    _price_cache[symbol] = rows
    return rows


def _parse_metric_time(value):
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _metric_value(row, *names):
    for name in names:
        if row.get(name) not in (None, ""):
            return float(row[name])
    raise KeyError(names[0])


def _metrics_day(symbol, day):
    key = (symbol, day.isoformat())
    if key in _metrics_cache:
        return _metrics_cache[key]
    name = f"{symbol}-metrics-{day.isoformat()}.zip"
    url = "https://data.binance.vision/data/futures/um/daily/metrics/" + f"{symbol}/{name}"
    out = []
    try:
        payload = _request_bytes(url)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        for row in csv.DictReader(io.StringIO(text)):
            try:
                out.append({
                    "timestamp": _parse_metric_time(row["create_time"]),
                    "oi": _metric_value(row, "sum_open_interest"),
                    "taker_ratio": _metric_value(
                        row, "sum_taker_long_short_vol_ratio", "taker_buy_sell_ratio"
                    ),
                    "top_ratio": _metric_value(
                        row,
                        "sum_toptrader_long_short_ratio",
                        "count_toptrader_long_short_ratio_position",
                    ),
                })
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: x["timestamp"])
    except Exception:
        out = []
    _metrics_cache[key] = out
    return out


def _latest_before(items, timestamp):
    answer = None
    for item in items:
        if item["timestamp"] <= timestamp:
            answer = item
        else:
            break
    return answer


def _market_filters(symbol, timestamp, direction, btc_bars, btc_index):
    key = (symbol, timestamp, direction)
    if key in _market_cache:
        return _market_cache[key]
    day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).date()
    metrics = _metrics_day(symbol, day - timedelta(days=1)) + _metrics_day(symbol, day)
    metrics.sort(key=lambda x: x["timestamp"])
    now = _latest_before(metrics, timestamp)
    old = _latest_before(metrics, timestamp - 30 * 60_000)
    if now is None or old is None:
        result = {"pass": False, "reason": "missing_metrics"}
        _market_cache[key] = result
        return result
    start = timestamp - 30 * 60_000
    end = timestamp + 60_000
    base = "https://fapi.binance.com"
    try:
        premium = _get_json(base + "/fapi/v1/premiumIndexKlines", {
            "symbol": symbol, "interval": "5m", "startTime": start, "endTime": end, "limit": 20,
        })
        funding = _get_json(base + "/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": timestamp - 24 * 60 * 60_000,
            "endTime": timestamp, "limit": 10,
        })
    except Exception as exc:
        result = {"pass": False, "reason": f"market_api:{type(exc).__name__}"}
        _market_cache[key] = result
        return result
    p = max((x for x in premium if int(x[0]) <= timestamp), key=lambda x: int(x[0]), default=None)
    f = max(
        (x for x in funding if int(x["fundingTime"]) <= timestamp),
        key=lambda x: int(x["fundingTime"]),
        default=None,
    )
    if p is None:
        result = {"pass": False, "reason": "missing_premium"}
        _market_cache[key] = result
        return result
    taker_ratio = now["taker_ratio"]
    taker_ok = taker_ratio >= TAKER_LONG_MIN if direction == 1 else taker_ratio <= TAKER_SHORT_MAX
    oi_change = now["oi"] / old["oi"] - 1.0 if old["oi"] else 0.0
    oi_ok = oi_change >= OI_MIN_RISE
    premium_close = float(p[4])
    premium_ok = abs(premium_close) <= MAX_PREMIUM_ABS
    funding_rate = float(f["fundingRate"]) if f else 0.0
    funding_ok = abs(funding_rate) <= MAX_FUNDING_ABS
    ratio = now["top_ratio"]
    long_share = ratio / (1.0 + ratio) if ratio > 0 else 0.5
    short_share = 1.0 - long_share
    crowd_ok = (long_share >= 0.55 or short_share >= 0.55)
    bi = btc_index.get(timestamp)
    btc_move = 0.0
    btc_ok = False
    if bi is not None and bi >= 10:
        btc_move = btc_bars[bi][4] / btc_bars[bi - 10][4] - 1.0
        btc_ok = direction * btc_move >= BTC_CONFIRM_MOVE
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
        "taker_ratio": taker_ratio,
        "oi_change_pct": oi_change * 100.0,
        "premium_pct": premium_close * 100.0,
        "funding_pct": funding_rate * 100.0,
        "top_long_share": long_share,
        "btc_move_pct": btc_move * 100.0,
        "checks": checks,
    }
    _market_cache[key] = result
    return result


def _breakout_level(timestamp, data, direction, confirmation):
    i1 = data["index_1m"].get(timestamp)
    if i1 is None or i1 < 5:
        return None
    if confirmation["bo1"]:
        prior = data[1][i1 - 5:i1]
        return max(x[2] for x in prior) if direction == 1 else min(x[3] for x in prior)
    close_time = timestamp + 60_000
    i3 = s7._completed_index(data[3], close_time, 3)
    if i3 < 3:
        return None
    prior = data[3][i3 - 3:i3]
    return max(x[2] for x in prior) if direction == 1 else min(x[3] for x in prior)


def _params(score):
    return (0.50, 75) if score >= 6 else (0.35, 50)


def _stop_risk(leverage):
    liqdist = max(0.002, 1.0 / leverage - 0.005)
    return min(0.028, max(0.006, liqdist * 0.55))


def _progressive_trail(risk, mfe):
    if mfe >= 6.0 * risk:
        return 0.35 * risk
    if mfe >= 4.0 * risk:
        return 0.50 * risk
    if mfe >= 2.5 * risk:
        return 0.75 * risk
    if mfe >= 1.5 * risk:
        return risk
    return None


def _fresh_score7_reference(raw, btc_bars, btc_index):
    s7.DATA_START = DATA_START
    s7.DATA_END = DATA_END
    s7.TEST_START_MS = TEST_START_MS
    s7.TEST_END_MS = TEST_END_MS
    s7._download_symbol = lambda symbol: raw[symbol]
    s7._market_filters = _market_filters
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        s7.run()
    marker = "TEN_SCORE7PLUS_RESULT="
    line = next((x for x in capture.getvalue().splitlines() if x.startswith(marker)), None)
    return json.loads(line[len(marker):]) if line else {"error": "no_result_marker"}


def _zec_style_reference(raw):
    zec.SYMBOLS = SYMBOLS
    zec.TEST_START_MS = TEST_START_MS
    zec.TEST_END_MS = TEST_END_MS
    raw4 = {symbol: s7._aggregate(bars, 240) for symbol, bars in raw.items()}
    return zec._run(raw4)


def _run_hybrid(raw):
    datasets = {}
    signals = {}
    maps = {}
    for symbol, bars in raw.items():
        data = {
            1: bars,
            3: s7._aggregate(bars, 3),
            5: s7._aggregate(bars, 5),
            240: s7._aggregate(bars, 240),
            "index_1m": {bar[0]: i for i, bar in enumerate(bars)},
        }
        datasets[symbol] = data
        signals[symbol] = zec._signals(data[240])
        maps[symbol] = {bar[0]: bar for bar in bars}
    btc_bars = raw["BTCUSDT"]
    btc_index = datasets["BTCUSDT"]["index_1m"]
    timestamps = sorted(ts for ts in maps["BTCUSDT"] if TEST_START_MS <= ts < TEST_END_MS)

    wallet = 100.0
    positions = {}
    armed = {}
    retests = {}
    pending_entries = {}
    pending_early = {}
    trades = []
    fees = 0.0
    stats = {
        "4h_setups": 0,
        "micro_confirmations": 0,
        "retests_seen": 0,
        "retest_failures": 0,
        "retest_expired": 0,
        "external_approvals": 0,
        "external_rejections": 0,
        "margin_rejections": 0,
        "early_failure_exits": 0,
    }
    reject_reasons = {}
    peak = 100.0
    trough = 100.0
    max_dd = 0.0
    max_concurrent = 0
    liquidations = 0

    def close_position(symbol, raw_exit, reason, ts, slip=True):
        nonlocal wallet, fees
        pos = positions.pop(symbol)
        exit_price = raw_exit * (1.0 - pos["direction"] * SLIPPAGE) if slip else raw_exit
        move = pos["direction"] * (exit_price / pos["entry"] - 1.0)
        gross = pos["notional"] * move
        exit_fee = pos["notional"] * FEE
        wallet += gross - exit_fee
        fees += exit_fee
        net = gross - pos["entry_fee"] - exit_fee
        trades.append({
            "symbol": symbol,
            "direction": "LONG" if pos["direction"] == 1 else "SHORT",
            "score": pos["score"],
            "leverage": pos["leverage"],
            "margin": pos["margin"],
            "entry": pos["entry"],
            "exit": exit_price,
            "net": net,
            "reason": reason,
            "held_minutes": pos["held_minutes"],
            "mfe_pct": pos["mfe"] * 100.0,
            "breakout_level": pos["breakout_level"],
            "exit_ts": ts,
        })

    for ts in timestamps:
        # Apply stop changes calculated from the prior completed minute only.
        for pos in positions.values():
            if pos.get("pending_stop") is not None:
                new_stop = pos.pop("pending_stop")
                if pos["direction"] == 1:
                    pos["stop"] = max(pos["stop"], new_stop)
                else:
                    pos["stop"] = min(pos["stop"], new_stop)

        # Objective early-failure exits occur at the next minute open.
        for symbol in list(pending_early):
            if pending_early[symbol] != ts or symbol not in positions:
                continue
            bar = maps[symbol].get(ts)
            if bar is not None:
                close_position(symbol, bar[1], "EARLY_FAILURE", ts)
                stats["early_failure_exits"] += 1
            pending_early.pop(symbol, None)

        # Entries are always the minute after all confirmation/retest/filter conditions.
        for symbol in list(pending_entries):
            item = pending_entries[symbol]
            if item["entry_ts"] != ts or symbol in positions:
                continue
            margin_fraction, leverage = _params(item["score"])
            required = wallet * margin_fraction
            used = sum(p["margin"] for p in positions.values())
            available = max(0.0, wallet - used)
            if required <= 0 or required > available + 1e-9:
                stats["margin_rejections"] += 1
                pending_entries.pop(symbol, None)
                continue
            bar = maps[symbol].get(ts)
            if bar is None:
                pending_entries.pop(symbol, None)
                continue
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
                "stop": entry * (1.0 - direction * risk),
                "liq": entry * (1.0 - direction * liqdist),
                "best_close": entry,
                "mfe": 0.0,
                "held_minutes": 0,
                "wrong_side_count": 0,
                "pending_stop": None,
            }
            max_concurrent = max(max_concurrent, len(positions))
            pending_entries.pop(symbol, None)

        # Manage open positions before processing new signals.
        for symbol in list(positions):
            pos = positions[symbol]
            bar = maps[symbol].get(ts)
            if bar is None:
                continue
            opn, high, low, close = bar[1], bar[2], bar[3], bar[4]
            direction = pos["direction"]
            pos["held_minutes"] += 1
            liq_gap = opn <= pos["liq"] if direction == 1 else opn >= pos["liq"]
            if liq_gap:
                wallet -= pos["margin"]
                trades.append({
                    "symbol": symbol,
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "score": pos["score"],
                    "leverage": pos["leverage"],
                    "margin": pos["margin"],
                    "entry": pos["entry"],
                    "exit": opn,
                    "net": -pos["margin"] - pos["entry_fee"],
                    "reason": "LIQ_GAP",
                    "held_minutes": pos["held_minutes"],
                    "mfe_pct": pos["mfe"] * 100.0,
                    "breakout_level": pos["breakout_level"],
                    "exit_ts": ts,
                })
                liquidations += 1
                positions.pop(symbol)
                continue
            stop_hit = low <= pos["stop"] if direction == 1 else high >= pos["stop"]
            if stop_hit:
                raw_exit = min(pos["stop"], opn) if direction == 1 else max(pos["stop"], opn)
                close_position(symbol, raw_exit, "TRAIL_STOP" if pos["mfe"] >= 1.5 * pos["risk"] else "STOP", ts)
                continue
            if direction == 1:
                pos["best_close"] = max(pos["best_close"], close)
                mfe = pos["best_close"] / pos["entry"] - 1.0
            else:
                pos["best_close"] = min(pos["best_close"], close)
                mfe = pos["entry"] / pos["best_close"] - 1.0
            pos["mfe"] = max(pos["mfe"], mfe)
            trail_distance = _progressive_trail(pos["risk"], pos["mfe"])
            if trail_distance is not None:
                be = pos["entry"] * (1.0 + direction * BE_LOCK)
                trail = (
                    pos["best_close"] * (1.0 - trail_distance)
                    if direction == 1
                    else pos["best_close"] * (1.0 + trail_distance)
                )
                proposed = max(be, trail) if direction == 1 else min(be, trail)
                pos["pending_stop"] = proposed
            if pos["held_minutes"] <= EARLY_FAILURE_MINUTES:
                wrong_side = close < pos["breakout_level"] if direction == 1 else close > pos["breakout_level"]
                i1 = datasets[symbol]["index_1m"].get(ts)
                vote_ok = i1 is not None and s7._tf_vote(datasets[symbol][1], i1, direction, 1)
                if wrong_side and not vote_ok:
                    pos["wrong_side_count"] += 1
                else:
                    pos["wrong_side_count"] = 0
                if pos["wrong_side_count"] >= 2:
                    pending_early[symbol] = ts + 60_000
            if pos["held_minutes"] >= MAX_HOLD_MINUTES:
                close_position(symbol, close, "TIME_12H", ts)

        # Arm fresh 4H reconstructed extreme setups at completed 4H close.
        for symbol in SYMBOLS:
            if symbol in positions or symbol in armed or symbol in retests or symbol in pending_entries:
                continue
            for signal_ts, setup in signals[symbol].items():
                arm_ts = signal_ts + 4 * 60 * 60_000
                if arm_ts != ts or not (TEST_START_MS <= arm_ts < TEST_END_MS):
                    continue
                armed[symbol] = {
                    **setup,
                    "arm_ts": arm_ts,
                    "expires": arm_ts + ARM_WINDOW_MINUTES * 60_000,
                }
                stats["4h_setups"] += 1
                break

        # Search the armed setup for exact Score7+ micro confirmation.
        for symbol in list(armed):
            item = armed[symbol]
            direction = item["direction"]
            bar = maps[symbol].get(ts)
            if bar is None:
                continue
            if ts > item["expires"]:
                armed.pop(symbol)
                continue
            adverse = -direction * (bar[4] / item["signal_close"] - 1.0)
            if adverse > INVALIDATION_MOVE:
                armed.pop(symbol)
                continue
            confirm = s7._micro_confirmation(ts, datasets[symbol], direction)
            if confirm is None:
                continue
            level = _breakout_level(ts, datasets[symbol], direction, confirm)
            if level is None:
                continue
            retests[symbol] = {
                **item,
                "confirm_ts": ts,
                "breakout_level": level,
                "retest_seen": False,
                "expires": ts + RETEST_WINDOW_MINUTES * 60_000,
            }
            stats["micro_confirmations"] += 1
            armed.pop(symbol)

        # Require causal retest, hold, and re-launch before external confirmation.
        for symbol in list(retests):
            item = retests[symbol]
            direction = item["direction"]
            bar = maps[symbol].get(ts)
            if bar is None or ts <= item["confirm_ts"]:
                continue
            level = item["breakout_level"]
            if ts > item["expires"]:
                stats["retest_expired"] += 1
                retests.pop(symbol)
                continue
            close = bar[4]
            failed = close < level * (1.0 - RETEST_FAIL) if direction == 1 else close > level * (1.0 + RETEST_FAIL)
            if failed:
                stats["retest_failures"] += 1
                retests.pop(symbol)
                continue
            touched = bar[3] <= level * (1.0 + RETEST_APPROACH) if direction == 1 else bar[2] >= level * (1.0 - RETEST_APPROACH)
            if touched and not item["retest_seen"]:
                item["retest_seen"] = True
                stats["retests_seen"] += 1
            if not item["retest_seen"]:
                continue
            i1 = datasets[symbol]["index_1m"].get(ts)
            vote_ok = i1 is not None and s7._tf_vote(datasets[symbol][1], i1, direction, 1)
            relaunched = close > level if direction == 1 else close < level
            if not (vote_ok and relaunched):
                continue
            filters = _market_filters(symbol, ts, direction, btc_bars, btc_index)
            if not filters.get("pass"):
                stats["external_rejections"] += 1
                reason = filters.get("reason") or ",".join(k for k, v in filters.get("checks", {}).items() if not v)
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                continue
            stats["external_approvals"] += 1
            pending_entries[symbol] = {
                **item,
                "filters": filters,
                "entry_ts": ts + 60_000,
            }
            retests.pop(symbol)

        marked = wallet
        for symbol, pos in positions.items():
            bar = maps[symbol].get(ts)
            if bar is None:
                continue
            move = pos["direction"] * (bar[4] / pos["entry"] - 1.0)
            marked += pos["notional"] * move
        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        if marked <= 0 or wallet <= 0:
            break

    if timestamps:
        last_ts = timestamps[-1]
        for symbol in list(positions):
            close_position(symbol, maps[symbol][last_ts][4], "END", last_ts)

    wins = sum(1 for trade in trades if trade["net"] > 0)
    gross_profit = sum(max(0.0, trade["net"]) for trade in trades)
    gross_loss = -sum(min(0.0, trade["net"]) for trade in trades)
    reasons = {}
    by_symbol = {}
    by_score = {}
    for trade in trades:
        reasons[trade["reason"]] = reasons.get(trade["reason"], 0) + 1
    for symbol in SYMBOLS:
        subset = [t for t in trades if t["symbol"] == symbol]
        by_symbol[symbol] = {
            "trades": len(subset),
            "wins": sum(1 for t in subset if t["net"] > 0),
            "net": round(sum(t["net"] for t in subset), 4),
        }
    for score in sorted({t["score"] for t in trades}):
        subset = [t for t in trades if t["score"] == score]
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
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (math.inf if gross_profit else 0.0),
        "fees": round(fees, 4),
        "peak_marked_equity": round(peak, 4),
        "trough_marked_equity": round(trough, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "max_concurrent": max_concurrent,
        "liquidations": liquidations,
        "exit_reasons": reasons,
        "pipeline": stats,
        "filter_reject_reasons": reject_reasons,
        "by_symbol": by_symbol,
        "by_score": by_score,
        "trades_detail": trades,
    }


def main():
    raw = {symbol: _download_symbol(symbol) for symbol in SYMBOLS}
    btc_bars = raw["BTCUSDT"]
    btc_index = {bar[0]: i for i, bar in enumerate(btc_bars)}
    zec_reference = _zec_style_reference(raw)
    score7_reference = _fresh_score7_reference(raw, btc_bars, btc_index)
    hybrid = _run_hybrid(raw)
    result = {
        "window": "2026-07-17T00:00:00Z/2026-09-16T00:00:00Z",
        "symbols": list(SYMBOLS),
        "references": {
            "zec_style_reconstructed": zec_reference,
            "score7plus_fresh_same_window": score7_reference,
            "score7plus_previously_validated": {
                "final_equity": 63.252325990523445,
                "return_pct": -36.74767400947655,
                "trades": 3,
                "wins": 1,
                "losses": 2,
                "profit_factor": 0.21328037143725828,
            },
        },
        "hybrid": hybrid,
        "rules": {
            "setup": "reconstructed ZEC-style 4H score >=5 breakout",
            "micro": "Score7+ 1m/3m/5m 3-of-3 + micro breakout + volume",
            "retest": "15m causal retest within 0.15%, fail if close breaches 0.25%, then 1m re-launch",
            "external": "taker aligned + OI +0.1%/30m + funding/premium limits + crowding + BTC 10m confirmation",
            "sizing": "score>=6: 50% wallet at 75x; score5: 35% at 50x",
            "take_profit": None,
            "initial_stop": "reconstructed ZEC risk rule (75x≈0.6%, 50x≈0.825%)",
            "trailing": "1.0R/0.75R/0.50R/0.35R distance after 1.5R/2.5R/4R/6R MFE; BE lock 0.15%",
            "early_failure": "first 10m: two consecutive closes beyond breakout level with failed 1m directional vote -> next-open exit",
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "fee_each_side": FEE,
            "slippage_each_side": SLIPPAGE,
        },
        "caveats": [
            "ZEC-style reference is the later reconstructed rule set, not verified as the exact source of the earlier +369% exploratory ZEC run.",
            "Funding cashflows are not applied to PnL; funding rate is used only as an entry filter.",
            "Margin/liquidation and order-book execution are simplified; minute OHLC uses adverse stop precedence.",
        ],
    }
    with open("hybrid_zec_score7plus_60d_result.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    print("HYBRID_60D_RESULT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
