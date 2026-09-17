from novabot913.temporal_audit import (
    FIVE_MINUTES_MS,
    FOUR_HOURS_MS,
    ONE_MINUTE_MS,
    THREE_MINUTES_MS,
    btc_confirmation_indices,
    five_minute_usable_open,
    four_hour_arm_time,
    latest_point_observation,
    three_minute_usable_open,
)


def test_3m_incomplete_bucket_is_not_visible() -> None:
    decision = 12 * 60 * 60_000 + 2 * 60_000
    assert three_minute_usable_open(decision) == decision - 5 * 60_000


def test_3m_bucket_becomes_visible_exactly_at_close() -> None:
    decision = 12 * 60 * 60_000 + 3 * 60_000
    assert three_minute_usable_open(decision) == 12 * 60 * 60_000


def test_5m_incomplete_bucket_is_not_visible() -> None:
    decision = 12 * 60 * 60_000 + 2 * 60_000
    assert five_minute_usable_open(decision) == 11 * 60 * 60_000 + 55 * 60_000


def test_5m_bucket_becomes_visible_exactly_at_close() -> None:
    decision = 12 * 60 * 60_000 + 5 * 60_000
    assert five_minute_usable_open(decision) == 12 * 60 * 60_000


def test_interval_constants_are_exact() -> None:
    assert THREE_MINUTES_MS == 180_000
    assert FIVE_MINUTES_MS == 300_000
    assert ONE_MINUTE_MS == 60_000


def test_4h_setup_arms_only_after_full_candle() -> None:
    bar_open = 8 * 60 * 60_000
    assert four_hour_arm_time(bar_open) == bar_open + FOUR_HOURS_MS


def test_point_observation_excludes_future_rows() -> None:
    rows = [
        {"timestamp": 100, "value": "old"},
        {"timestamp": 200, "value": "current"},
        {"timestamp": 300, "value": "future"},
    ]
    selected = latest_point_observation(rows, 250)
    assert selected is not None
    assert selected["value"] == "current"


def test_point_observation_accepts_exact_timestamp() -> None:
    rows = [{"timestamp": 100}, {"timestamp": 200}]
    assert latest_point_observation(rows, 200) == rows[1]


def test_point_observation_does_not_apply_taker_completion_shift() -> None:
    rows = [{"timestamp": 100, "value": 1}, {"timestamp": 200, "value": 2}]
    assert latest_point_observation(rows, 200) == rows[1]


def test_btc_confirmation_uses_current_completed_bar_and_ten_back() -> None:
    assert btc_confirmation_indices(10) == (10, 0)
    assert btc_confirmation_indices(37) == (37, 27)


def test_btc_confirmation_requires_ten_prior_bars() -> None:
    assert btc_confirmation_indices(9) is None
