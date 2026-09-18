from __future__ import annotations

import pytest

from novabot913.execution_bus import ExecutionEvent, JsonlExecutionBus


def _entry_fill(**overrides: object) -> ExecutionEvent:
    payload: dict[str, object] = {
        "event_id": "entry_fill:123",
        "symbol": "DOGE/USDT:USDT",
        "event": "entry_fill",
        "side": "long",
        "timestamp_ms": 1_120_000,
        "price": 0.25,
        "leverage": 75.0,
        "position_id": "DOGEUSDT:1060000:long",
        "order_id": "123",
        "score": 6,
        "breakout_level": 0.249,
        "source_candle_open_ms": 1_060_000,
    }
    payload.update(overrides)
    return ExecutionEvent(**payload)


def test_entry_fill_normalizes_pair_and_validates_metadata() -> None:
    event = _entry_fill()
    assert event.symbol == "DOGEUSDT"

    with pytest.raises(ValueError, match="score"):
        _entry_fill(score=None)
    with pytest.raises(ValueError, match="breakout"):
        _entry_fill(breakout_level=None)


def test_execution_bus_is_idempotent(tmp_path) -> None:
    bus = JsonlExecutionBus(tmp_path / "fills.jsonl")
    event = _entry_fill()

    assert bus.append_once(event)
    assert not bus.append_once(event)
    assert bus.read_all() == [event]


def test_exit_fill_does_not_require_entry_metadata() -> None:
    event = ExecutionEvent(
        event_id="exit_fill:456",
        symbol="DOGEUSDT",
        event="exit_fill",
        side="long",
        timestamp_ms=1_240_000,
        price=0.26,
        leverage=75.0,
        position_id="DOGEUSDT:1060000:long",
        reason="TRAIL_STOP",
    )
    assert event.reason == "TRAIL_STOP"


def test_entry_rejection_requires_reason_and_metadata() -> None:
    event = ExecutionEvent(
        event_id="entry_rejected:DOGEUSDT:1060000:long:LEVERAGE_UNAVAILABLE",
        symbol="DOGEUSDT",
        event="entry_rejected",
        side="long",
        timestamp_ms=1_120_000,
        position_id="DOGEUSDT:1060000:long",
        leverage=20.0,
        score=6,
        breakout_level=0.249,
        source_candle_open_ms=1_060_000,
        reason="LEVERAGE_UNAVAILABLE",
    )
    assert event.price is None
    assert event.reason == "LEVERAGE_UNAVAILABLE"

    with pytest.raises(ValueError, match="rejection requires reason"):
        ExecutionEvent(
            event_id="entry_rejected:bad",
            symbol="DOGEUSDT",
            event="entry_rejected",
            side="long",
            timestamp_ms=1_120_000,
            position_id="DOGEUSDT:1060000:long",
            score=6,
            breakout_level=0.249,
            source_candle_open_ms=1_060_000,
        )


def test_fill_event_still_requires_price_and_leverage() -> None:
    with pytest.raises(ValueError, match="positive price"):
        _entry_fill(price=None)
    with pytest.raises(ValueError, match="positive leverage"):
        _entry_fill(leverage=None)
