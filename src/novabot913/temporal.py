from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

FIVE_MINUTES_MS = 5 * 60_000


@dataclass(frozen=True, slots=True)
class TemporalObservation:
    """A timestamped observation whose timestamp marks interval start."""

    timestamp: int
    interval_ms: int

    @property
    def available_at(self) -> int:
        return self.timestamp + self.interval_ms


def latest_completed_interval[T: Mapping[str, object]](
    items: Sequence[T],
    decision_ms: int,
    *,
    interval_ms: int,
    timestamp_key: str = "timestamp",
) -> T | None:
    """Return the latest interval that is fully complete at decision_ms.

    Strategy 913's archived Premium and Taker rows are timestamped by the
    opening time of the 5-minute interval they represent. Therefore a row is
    causal only when ``open_timestamp + interval_ms <= decision_ms``.
    """

    answer: T | None = None
    answer_ts = -1
    for item in items:
        raw_timestamp = item.get(timestamp_key)
        if not isinstance(raw_timestamp, int):
            raise TypeError(f"{timestamp_key} must be int milliseconds")
        if raw_timestamp + interval_ms <= decision_ms and raw_timestamp > answer_ts:
            answer = item
            answer_ts = raw_timestamp
    return answer


def latest_completed_premium[T: Mapping[str, object]](
    items: Sequence[T], decision_ms: int
) -> T | None:
    """Select the latest fully completed Binance Premium 5m candle."""

    return latest_completed_interval(items, decision_ms, interval_ms=FIVE_MINUTES_MS)


def latest_completed_taker[T: Mapping[str, object]](
    items: Sequence[T], decision_ms: int
) -> T | None:
    """Select the latest fully completed archived Taker 5m observation."""

    return latest_completed_interval(items, decision_ms, interval_ms=FIVE_MINUTES_MS)
