from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from novabot913.temporal import latest_completed_premium, latest_completed_taker

MINUTE_MS = 60_000
FOUR_HOURS_MS = 4 * 60 * MINUTE_MS

CANONICAL_UNIVERSE = (
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

ARM_WINDOW_MINUTES = 60
RETEST_WINDOW_MINUTES = 15
INVALIDATION_MOVE = 0.0075
RETEST_APPROACH = 0.0015
RETEST_FAIL = 0.0025
EARLY_FAILURE_MINUTES = 10
FOLLOWTHROUGH_MINUTE = 15
FOLLOWTHROUGH_MIN_CLOSE_MOVE = 0.0030
FOLLOWTHROUGH_MIN_MFE = 0.0077
MAX_HOLD_MINUTES = 720
BE_LOCK = 0.0015

TAKER_LONG_MIN = 1.20
TAKER_SHORT_MAX = 1.0 / TAKER_LONG_MIN
OI_MIN_RISE = 0.001
BTC_CONFIRM_MOVE = 0.001
MAX_FUNDING_ABS = 0.0003
MAX_PREMIUM_ABS = 0.003

type Bar = tuple[int, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class Setup:
    direction: int
    score: int
    vol_ratio: float
    ret1: float
    body_abs: float
    close_pos: float
    signal_close: float
    signal_ts: int


@dataclass(frozen=True, slots=True)
class MicroConfirmation:
    votes: int
    vr1: float
    vr3: float
    bo1: bool
    bo3: bool


@dataclass(frozen=True, slots=True)
class MarketFilterInputs:
    taker_rows: Sequence[Mapping[str, object]]
    oi_rows: Sequence[Mapping[str, object]]
    crowding_rows: Sequence[Mapping[str, object]]
    premium_rows: Sequence[Mapping[str, object]]
    funding_rows: Sequence[Mapping[str, object]]
    btc_bars: Sequence[Bar]


@dataclass(frozen=True, slots=True)
class MarketFilterResult:
    passed: bool
    checks: Mapping[str, bool]
    taker_ratio: float | None = None
    oi_change_pct: float | None = None
    premium_pct: float | None = None
    funding_pct: float | None = None
    top_long_share: float | None = None
    btc_move_pct: float | None = None
    reason: str | None = None


def aggregate(bars: Sequence[Bar], minutes: int) -> list[Bar]:
    if minutes <= 0:
        raise ValueError("minutes must be positive")

    bucket_ms = minutes * MINUTE_MS
    out: list[Bar] = []
    current_bucket: int | None = None
    agg: list[float | int] | None = None

    for ts, opn, high, low, close, volume in bars:
        bucket = ts // bucket_ms
        if bucket != current_bucket:
            if agg is not None:
                out.append(
                    (
                        int(agg[0]),
                        float(agg[1]),
                        float(agg[2]),
                        float(agg[3]),
                        float(agg[4]),
                        float(agg[5]),
                    )
                )
            current_bucket = bucket
            agg = [bucket * bucket_ms, opn, high, low, close, volume]
        else:
            if agg is None:
                raise RuntimeError("aggregation state was not initialized")
            agg[2] = max(float(agg[2]), high)
            agg[3] = min(float(agg[3]), low)
            agg[4] = close
            agg[5] = float(agg[5]) + volume

    if agg is not None:
        out.append(
            (
                int(agg[0]),
                float(agg[1]),
                float(agg[2]),
                float(agg[3]),
                float(agg[4]),
                float(agg[5]),
            )
        )
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    if period <= 0:
        raise ValueError("period must be positive")

    alpha = 2.0 / (period + 1.0)
    current = values[0]
    out: list[float] = []
    for value in values:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)
    return out


def four_hour_setups(
    bars: Sequence[Bar],
    *,
    require_next_bar: bool = True,
) -> dict[int, Setup]:
    """Reproduce the frozen reconstructed 4H score>=5 setup logic."""

    if len(bars) < 9:
        return {}

    closes = [bar[4] for bar in bars]
    ema3 = ema(closes, 3)
    ema8 = ema(closes, 8)
    stop = len(bars) - 1 if require_next_bar else len(bars)
    setups: dict[int, Setup] = {}

    for index in range(8, stop):
        ts, open_price, high, low, close, volume = bars[index]
        prev3 = bars[index - 3 : index]
        prev5 = bars[index - 5 : index]
        prev3_hi = max(bar[2] for bar in prev3)
        prev3_lo = min(bar[3] for bar in prev3)
        vol_avg5 = sum(bar[5] for bar in prev5) / len(prev5)
        vol_ratio = volume / vol_avg5 if vol_avg5 else 0.0
        ret1 = close / bars[index - 1][4] - 1.0
        body = (close - open_price) / open_price
        body_abs = abs(body)
        close_pos = (close - low) / (high - low) if high > low else 0.5

        long_score = 0
        short_score = 0
        if ema3[index] > ema8[index]:
            long_score += 1
        elif ema3[index] < ema8[index]:
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

        setups[ts] = Setup(
            direction=direction,
            score=score,
            vol_ratio=vol_ratio,
            ret1=ret1,
            body_abs=body_abs,
            close_pos=close_pos,
            signal_close=close,
            signal_ts=ts,
        )
    return setups


def setup_closing_at(bars: Sequence[Bar], close_ms: int) -> Setup | None:
    signal_ts = close_ms - FOUR_HOURS_MS
    return four_hour_setups(bars, require_next_bar=False).get(signal_ts)


def completed_index(series: Sequence[Bar], close_time_ms: int, minutes: int) -> int:
    bucket_ms = minutes * MINUTE_MS
    usable_open = (close_time_ms // bucket_ms) * bucket_ms - bucket_ms
    low = 0
    high = len(series) - 1
    answer = -1

    while low <= high:
        middle = (low + high) // 2
        if series[middle][0] <= usable_open:
            answer = middle
            low = middle + 1
        else:
            high = middle - 1
    return answer


def tf_vote(series: Sequence[Bar], index: int, direction: int, minutes: int) -> bool:
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


def micro_confirmation(
    candle_open_ms: int,
    one_minute_bars: Sequence[Bar],
    direction: int,
) -> MicroConfirmation | None:
    bars_1m = list(one_minute_bars)
    index_1m = {bar[0]: index for index, bar in enumerate(bars_1m)}
    index = index_1m.get(candle_open_ms)
    if index is None or index < 20:
        return None

    data_3m = aggregate(bars_1m, 3)
    data_5m = aggregate(bars_1m, 5)
    close_time = candle_open_ms + MINUTE_MS
    votes = 0

    for minutes, series in ((1, bars_1m), (3, data_3m), (5, data_5m)):
        current_index = index if minutes == 1 else completed_index(series, close_time, minutes)
        if current_index >= 0 and tf_vote(series, current_index, direction, minutes):
            votes += 1
    if votes != 3:
        return None

    current = bars_1m[index]
    prior_1m = bars_1m[index - 20 : index]
    mean_v1 = sum(bar[5] for bar in prior_1m) / len(prior_1m)
    vr1 = current[5] / mean_v1 if mean_v1 else 0.0
    recent_1m = bars_1m[index - 5 : index]
    bo1 = (
        current[4] > max(bar[2] for bar in recent_1m)
        if direction == 1
        else current[4] < min(bar[3] for bar in recent_1m)
    )

    index_3m = completed_index(data_3m, close_time, 3)
    if index_3m < 10:
        return None
    bar_3m = data_3m[index_3m]
    prior_3m = data_3m[index_3m - 10 : index_3m]
    mean_v3 = sum(bar[5] for bar in prior_3m) / len(prior_3m)
    vr3 = bar_3m[5] / mean_v3 if mean_v3 else 0.0
    recent_3m = data_3m[index_3m - 3 : index_3m]
    bo3 = (
        bar_3m[4] > max(bar[2] for bar in recent_3m)
        if direction == 1
        else bar_3m[4] < min(bar[3] for bar in recent_3m)
    )

    if not (vr1 >= 1.5 or vr3 >= 1.3):
        return None
    if not (bo1 or bo3):
        return None
    return MicroConfirmation(votes=3, vr1=vr1, vr3=vr3, bo1=bo1, bo3=bo3)


def breakout_level(
    candle_open_ms: int,
    one_minute_bars: Sequence[Bar],
    direction: int,
    confirmation: MicroConfirmation,
) -> float | None:
    bars_1m = list(one_minute_bars)
    index_1m = {bar[0]: index for index, bar in enumerate(bars_1m)}
    index = index_1m.get(candle_open_ms)
    if index is None or index < 5:
        return None

    if confirmation.bo1:
        prior = bars_1m[index - 5 : index]
        return max(bar[2] for bar in prior) if direction == 1 else min(bar[3] for bar in prior)

    data_3m = aggregate(bars_1m, 3)
    index_3m = completed_index(data_3m, candle_open_ms + MINUTE_MS, 3)
    if index_3m < 3:
        return None
    prior_3m = data_3m[index_3m - 3 : index_3m]
    return (
        max(bar[2] for bar in prior_3m)
        if direction == 1
        else min(bar[3] for bar in prior_3m)
    )


def latest_before(
    items: Sequence[Mapping[str, object]],
    timestamp_ms: int,
    *,
    timestamp_key: str = "timestamp",
) -> Mapping[str, object] | None:
    answer: Mapping[str, object] | None = None
    answer_ts = -1
    for item in items:
        raw_timestamp = item.get(timestamp_key)
        if not isinstance(raw_timestamp, int):
            raise TypeError(f"{timestamp_key} must be int milliseconds")
        if raw_timestamp <= timestamp_ms and raw_timestamp > answer_ts:
            answer = item
            answer_ts = raw_timestamp
    return answer


def _float(item: Mapping[str, object], key: str) -> float:
    value = item.get(key)
    if not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def evaluate_market_filters(
    candle_open_ms: int,
    direction: int,
    inputs: MarketFilterInputs,
) -> MarketFilterResult:
    """Frozen market filters with only the authorized Premium/Taker timing corrections."""

    decision_ms = candle_open_ms + MINUTE_MS
    taker = latest_completed_taker(inputs.taker_rows, decision_ms)
    premium = latest_completed_premium(inputs.premium_rows, decision_ms)
    oi_now = latest_before(inputs.oi_rows, candle_open_ms)
    oi_old = latest_before(inputs.oi_rows, candle_open_ms - 30 * MINUTE_MS)
    crowding = latest_before(inputs.crowding_rows, candle_open_ms)
    funding = latest_before(inputs.funding_rows, candle_open_ms)

    if None in (taker, premium, oi_now, oi_old, crowding, funding):
        return MarketFilterResult(
            passed=False,
            checks={},
            reason="missing_market_data",
        )

    if (
        taker is None
        or premium is None
        or oi_now is None
        or oi_old is None
        or crowding is None
        or funding is None
    ):
        raise AssertionError("unreachable market data state")

    taker_ratio = _float(taker, "taker_ratio")
    taker_ok = taker_ratio >= TAKER_LONG_MIN if direction == 1 else taker_ratio <= TAKER_SHORT_MAX

    oi_current = _float(oi_now, "oi")
    oi_previous = _float(oi_old, "oi")
    oi_change = oi_current / oi_previous - 1.0 if oi_previous else 0.0
    oi_ok = oi_change >= OI_MIN_RISE

    premium_close = _float(premium, "close")
    premium_ok = abs(premium_close) <= MAX_PREMIUM_ABS

    funding_rate = _float(funding, "rate")
    funding_ok = abs(funding_rate) <= MAX_FUNDING_ABS

    top_ratio = _float(crowding, "top_ratio")
    long_share = top_ratio / (1.0 + top_ratio) if top_ratio > 0 else 0.5
    short_share = 1.0 - long_share
    crowding_ok = long_share >= 0.55 or short_share >= 0.55

    btc_index = {bar[0]: index for index, bar in enumerate(inputs.btc_bars)}
    index = btc_index.get(candle_open_ms)
    btc_move = 0.0
    btc_ok = False
    if index is not None and index >= 10:
        btc_move = inputs.btc_bars[index][4] / inputs.btc_bars[index - 10][4] - 1.0
        btc_ok = direction * btc_move >= BTC_CONFIRM_MOVE

    checks = {
        "taker": taker_ok,
        "oi": oi_ok,
        "premium": premium_ok,
        "funding": funding_ok,
        "crowding": crowding_ok,
        "btc": btc_ok,
    }
    return MarketFilterResult(
        passed=all(checks.values()),
        checks=checks,
        taker_ratio=taker_ratio,
        oi_change_pct=oi_change * 100.0,
        premium_pct=premium_close * 100.0,
        funding_pct=funding_rate * 100.0,
        top_long_share=long_share,
        btc_move_pct=btc_move * 100.0,
    )
