from novabot913.temporal import (
    FIVE_MINUTES_MS,
    latest_completed_premium,
    latest_completed_taker,
)


def _row(ts: int, value: float) -> dict[str, object]:
    return {"timestamp": ts, "value": value}


def test_premium_before_boundary_uses_previous_completed_interval() -> None:
    rows = [_row(0, 1.0), _row(FIVE_MINUTES_MS, 2.0)]
    assert latest_completed_premium(rows, 2 * FIVE_MINUTES_MS - 1) == rows[0]


def test_premium_exact_boundary_makes_interval_available() -> None:
    rows = [_row(0, 1.0), _row(FIVE_MINUTES_MS, 2.0)]
    assert latest_completed_premium(rows, 2 * FIVE_MINUTES_MS) == rows[1]


def test_taker_before_boundary_uses_previous_completed_interval() -> None:
    rows = [_row(0, 2.394062102), _row(FIVE_MINUTES_MS, 1.282378)]
    assert latest_completed_taker(rows, 2 * FIVE_MINUTES_MS - 1) == rows[0]


def test_taker_exact_boundary_makes_interval_available() -> None:
    rows = [_row(0, 2.394062102), _row(FIVE_MINUTES_MS, 1.282378)]
    assert latest_completed_taker(rows, 2 * FIVE_MINUTES_MS) == rows[1]


def test_future_value_change_cannot_change_past_premium_selection() -> None:
    past = _row(0, 0.002)
    future_a = _row(FIVE_MINUTES_MS, 0.002)
    future_b = _row(FIVE_MINUTES_MS, 0.004)
    decision = 2 * FIVE_MINUTES_MS - 1
    assert latest_completed_premium([past, future_a], decision) == past
    assert latest_completed_premium([past, future_b], decision) == past


def test_future_value_change_cannot_change_past_taker_selection() -> None:
    past = _row(0, 2.394062102)
    future_a = _row(FIVE_MINUTES_MS, 1.282378)
    future_b = _row(FIVE_MINUTES_MS, 0.1)
    decision = 2 * FIVE_MINUTES_MS - 1
    assert latest_completed_taker([past, future_a], decision) == past
    assert latest_completed_taker([past, future_b], decision) == past


def test_midnight_crossing_selects_previous_day_interval() -> None:
    day_ms = 24 * 60 * 60_000
    rows = [
        _row(day_ms - FIVE_MINUTES_MS, 1.0),
        _row(day_ms, 2.0),
    ]
    assert latest_completed_premium(rows, day_ms + 60_000) == rows[0]
    assert latest_completed_taker(rows, day_ms + 60_000) == rows[0]


def test_no_eligible_interval_returns_none() -> None:
    rows = [_row(FIVE_MINUTES_MS, 1.0)]
    assert latest_completed_premium(rows, FIVE_MINUTES_MS) is None
    assert latest_completed_taker(rows, FIVE_MINUTES_MS) is None


def test_latest_completed_selection_does_not_depend_on_input_sort_order() -> None:
    rows = [_row(2 * FIVE_MINUTES_MS, 3.0), _row(0, 1.0), _row(FIVE_MINUTES_MS, 2.0)]
    decision = 3 * FIVE_MINUTES_MS
    assert latest_completed_premium(rows, decision) == rows[0]
    assert latest_completed_taker(rows, decision) == rows[0]
