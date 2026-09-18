import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone

import tmp_hybrid_zec_score7plus_60d as h

_premium_cache = {}
_funding_cache = {}


def _premium_day(symbol, day):
    key = (symbol, day.isoformat())
    if key in _premium_cache:
        return _premium_cache[key]
    stamp = day.isoformat()
    name = f"{symbol}-5m-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/"
        f"{symbol}/5m/{name}"
    )
    out = []
    try:
        for row in h._read_zip_rows(url):
            if row and row[0].isdigit():
                out.append({"timestamp": int(row[0]), "close": float(row[4])})
    except Exception:
        out = []
    out.sort(key=lambda x: x["timestamp"])
    _premium_cache[key] = out
    return out


def _parse_funding_payload(payload):
    out = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    for row in csv.DictReader(io.StringIO(text)):
        try:
            out.append({
                "timestamp": int(row["calc_time"]),
                "rate": float(row["last_funding_rate"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["timestamp"])
    return out


def _funding_month(symbol, year, month):
    key = ("month", symbol, year, month)
    if key in _funding_cache:
        return _funding_cache[key]
    stamp = f"{year:04d}-{month:02d}"
    name = f"{symbol}-fundingRate-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        f"{symbol}/{name}"
    )
    try:
        out = _parse_funding_payload(h._request_bytes(url))
    except Exception:
        out = []
    _funding_cache[key] = out
    return out


def _funding_day(symbol, day):
    key = ("day", symbol, day.isoformat())
    if key in _funding_cache:
        return _funding_cache[key]
    stamp = day.isoformat()
    name = f"{symbol}-fundingRate-{stamp}.zip"
    url = (
        "https://data.binance.vision/data/futures/um/daily/fundingRate/"
        f"{symbol}/{name}"
    )
    try:
        out = _parse_funding_payload(h._request_bytes(url))
    except Exception:
        out = []
    _funding_cache[key] = out
    return out


def _funding_before(symbol, timestamp):
    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    day = dt.date()
    first = dt.replace(day=1)
    prev_month = first - timedelta(days=1)
    rows = _funding_month(symbol, prev_month.year, prev_month.month)
    rows += _funding_month(symbol, dt.year, dt.month)
    # Current incomplete months are not yet in the monthly archive; daily is authoritative fallback.
    rows += _funding_day(symbol, day - timedelta(days=1))
    rows += _funding_day(symbol, day)
    rows.sort(key=lambda x: x["timestamp"])
    return h._latest_before(rows, timestamp)


def archive_market_filters(symbol, timestamp, direction, btc_bars, btc_index):
    key = ("archive", symbol, timestamp, direction)
    if key in h._market_cache:
        return h._market_cache[key]
    day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).date()
    metrics = h._metrics_day(symbol, day - timedelta(days=1)) + h._metrics_day(symbol, day)
    metrics.sort(key=lambda x: x["timestamp"])
    now = h._latest_before(metrics, timestamp)
    old = h._latest_before(metrics, timestamp - 30 * 60_000)
    premium = h._latest_before(_premium_day(symbol, day), timestamp)
    funding = _funding_before(symbol, timestamp)
    if now is None or old is None or premium is None or funding is None:
        result = {"pass": False, "reason": "missing_archive_market_data"}
        h._market_cache[key] = result
        return result

    taker_ratio = now["taker_ratio"]
    taker_ok = (
        taker_ratio >= h.TAKER_LONG_MIN
        if direction == 1
        else taker_ratio <= h.TAKER_SHORT_MAX
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
        "source": "Binance Vision archive",
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
