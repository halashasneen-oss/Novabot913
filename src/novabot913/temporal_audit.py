from __future__ import annotations

from collections.abc import Mapping, Sequence

THREE_MINUTES_MS = 3 * 60_000
FIVE_MINUTES_MS = 5 * 60_000
FOUR_HOURS_MS = 4 * 60 * 60_000
ONE_MINUTE_MS = 60_000


def latest_completed_bucket_open(decision_ms: int, interval_ms: int) -> int:
    """Return the open time of the latest interval fully closed by decision_ms.

    This mirrors the reference completed-index convention used for aggregated
    3m/5m candles. A bucket ending exactly at decision_ms is available; the
    bucket currently in progress is not.
    """

    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    return (decision_ms // interval_ms) * interval_ms - interval_ms


def three_minute_usable_open(decision_ms: int) -> int:
    return latest_completed_bucket_open(decision_ms, THREE_MINUTES_MS)


def five_minute_usable_open(decision_ms: int) -> int:
    return latest_completed_bucket_open(decision_ms, FIVE_MINUTES_MS)


def four_hour_arm_time(bar_open_ms: int) -> int:
    """A 4H setup becomes armable only once its entire 4H candle has closed."""

    return bar_open_ms + FOUR_HOURS_MS


def latest_point_observation[T: Mapping[str, object]](
    items: Sequence[T],
    cutoff_ms: int,
    *,
    timestamp_key: str = "timestamp",
) -> T | None:
    """Return the latest point-in-time observation at or before cutoff_ms.

    This deliberately preserves the legacy selection semantics used for OI,
    Crowding and Funding. It does not apply the 5m completion rule authorized
    only for Premium and Taker.
    """

    answer: T | None = None
    answer_ts = -1
    for item in items:
        raw_timestamp = item.get(timestamp_key)
        if not isinstance(raw_timestamp, int):
            raise TypeError(f"{timestamp_key} must be int milliseconds")
        if raw_timestamp <= cutoff_ms and raw_timestamp > answer_ts:
            answer = item
            answer_ts = raw_timestamp
    return answer


def btc_confirmation_indices(current_index: int) -> tuple[int, int] | None:
    """Return current and 10-bars-back indexes used by the frozen BTC check."""

    if current_index < 10:
        return None
    return current_index, current_index - 10
