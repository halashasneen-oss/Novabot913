from __future__ import annotations

from dataclasses import replace

import pytest

from novabot913.execution_bus import ExecutionEvent
from novabot913.live_engine import (
    JsonLiveStateStore,
    PendingEntry,
    Strategy913LiveEngine,
)
from novabot913.signal_bus import make_position_id
from novabot913.strategy_core import CANONICAL_UNIVERSE, MINUTE_MS, MarketFilterResult


def _allow_market(*args) -> MarketFilterResult:
    return MarketFilterResult(passed=True, checks={"all": True})


def _fill(
    *,
    price: float = 100.0,
    score: int = 6,
    breakout: float = 101.0,
    timestamp_ms: int = 0,
) -> ExecutionEvent:
    symbol = CANONICAL_UNIVERSE[0]
    side = "long"
    source = 0
    return ExecutionEvent(
        event_id=f"entry_fill:{price}:{breakout}",
        symbol=symbol,
        event="entry_fill",
        side=side,
        timestamp_ms=timestamp_ms,
        price=price,
        leverage=75.0 if score >= 6 else 50.0,
        position_id=make_position_id(symbol, source, side),
        score=score,
        breakout_level=breakout,
        source_candle_open_ms=source,
    )


def _bar(
    timestamp: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> tuple[int, float, float, float, float, float]:
    return timestamp, open_price, high, low, close, 100.0


def test_live_engine_rejects_silent_leverage_change() -> None:
    engine = Strategy913LiveEngine()
    event = _fill()
    changed = replace(event, event_id="entry_fill:bad", leverage=50.0)
    with pytest.raises(ValueError, match="does not match frozen"):
        engine.apply_execution_event(changed)


def test_early_failure_emits_next_open_exit_signal() -> None:
    engine = Strategy913LiveEngine()
    engine.apply_execution_event(_fill(breakout=101.0))
    symbol = CANONICAL_UNIVERSE[0]

    bars = [
        _bar(0, open_price=100.0, high=100.1, low=99.7, close=99.9),
        _bar(MINUTE_MS, open_price=99.9, high=100.0, low=99.6, close=99.8),
    ]

    assert engine.process_closed_candle(symbol, bars[:1], [], _allow_market) == []
    intents = engine.process_closed_candle(symbol, bars, [], _allow_market)

    exits = [intent for intent in intents if intent.intent == "exit"]
    assert len(exits) == 1
    assert exits[0].reason == "EARLY_FAILURE"


def test_progressive_trailing_uses_actual_fill_price() -> None:
    engine = Strategy913LiveEngine()
    engine.apply_execution_event(_fill(breakout=99.0))
    symbol = CANONICAL_UNIVERSE[0]
    bars = [_bar(0, open_price=100.0, high=101.2, low=99.9, close=101.0)]

    intents = engine.process_closed_candle(symbol, bars, [], _allow_market)

    stops = [intent for intent in intents if intent.intent == "stop_update"]
    assert len(stops) == 1
    assert stops[0].stop_price is not None
    assert stops[0].stop_price > 100.0


def test_state_store_round_trip_preserves_position(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = JsonLiveStateStore(path)
    engine = Strategy913LiveEngine()
    engine.apply_execution_event(_fill())

    store.save(engine)
    restored = store.load()

    symbol = CANONICAL_UNIVERSE[0]
    assert restored.states[symbol].position is not None
    assert restored.states[symbol].position.entry_price == pytest.approx(100.0)
    assert restored.processed_event_ids == engine.processed_event_ids


def test_non_contiguous_candle_is_rejected() -> None:
    engine = Strategy913LiveEngine()
    symbol = CANONICAL_UNIVERSE[0]
    first = [_bar(0, open_price=1.0, high=1.0, low=1.0, close=1.0)]
    engine.process_closed_candle(symbol, first, [], _allow_market)

    skipped = first + [_bar(2 * MINUTE_MS, open_price=1.0, high=1.0, low=1.0, close=1.0)]
    with pytest.raises(ValueError, match="non-contiguous"):
        engine.process_closed_candle(symbol, skipped, [], _allow_market)


def test_entry_rejection_clears_only_matching_pending_entry() -> None:
    engine = Strategy913LiveEngine()
    symbol = CANONICAL_UNIVERSE[0]
    position_id = make_position_id(symbol, 0, "long")
    engine.states[symbol].pending_entry = PendingEntry(
        position_id=position_id,
        side="long",
        score=6,
        breakout_level=101.0,
        source_candle_open_ms=0,
        expires_ms=3 * MINUTE_MS,
    )
    rejection = ExecutionEvent(
        event_id="entry_rejected:matching",
        symbol=symbol,
        event="entry_rejected",
        side="long",
        timestamp_ms=MINUTE_MS,
        position_id=position_id,
        leverage=20.0,
        score=6,
        breakout_level=101.0,
        source_candle_open_ms=0,
        reason="LEVERAGE_UNAVAILABLE",
    )

    engine.apply_execution_event(rejection)

    assert engine.states[symbol].pending_entry is None
    assert engine.states[symbol].position is None
    assert rejection.event_id in engine.processed_event_ids


def test_entry_rejection_cannot_clear_different_pending_position() -> None:
    engine = Strategy913LiveEngine()
    symbol = CANONICAL_UNIVERSE[0]
    pending_id = make_position_id(symbol, 0, "long")
    engine.states[symbol].pending_entry = PendingEntry(
        position_id=pending_id,
        side="long",
        score=6,
        breakout_level=101.0,
        source_candle_open_ms=0,
        expires_ms=3 * MINUTE_MS,
    )
    rejection = ExecutionEvent(
        event_id="entry_rejected:mismatch",
        symbol=symbol,
        event="entry_rejected",
        side="long",
        timestamp_ms=MINUTE_MS,
        position_id=make_position_id(symbol, MINUTE_MS, "long"),
        score=6,
        breakout_level=101.0,
        source_candle_open_ms=MINUTE_MS,
        reason="STAKE_ABOVE_MAX",
    )

    with pytest.raises(ValueError, match="does not match pending"):
        engine.apply_execution_event(rejection)

    assert engine.states[symbol].pending_entry is not None
    assert rejection.event_id not in engine.processed_event_ids


def test_pending_exit_is_reemitted_until_fill_feedback() -> None:
    engine = Strategy913LiveEngine()
    engine.apply_execution_event(_fill(breakout=99.0))
    symbol = CANONICAL_UNIVERSE[0]
    state = engine.states[symbol]
    state.exit_pending = True
    state.pending_exit_reason = "FOLLOWTHROUGH_15M"

    first = [_bar(0, open_price=100.0, high=100.1, low=99.9, close=100.0)]
    retry = engine.process_closed_candle(symbol, first, [], _allow_market)

    assert len(retry) == 1
    assert retry[0].intent == "exit"
    assert retry[0].reason == "FOLLOWTHROUGH_15M"
    assert state.position is not None
    assert state.position.held_minutes == 0

    exit_fill = ExecutionEvent(
        event_id="exit_fill:retry",
        symbol=symbol,
        event="exit_fill",
        side="long",
        timestamp_ms=MINUTE_MS,
        position_id=state.position.position_id,
        price=100.0,
        leverage=75.0,
        reason="FOLLOWTHROUGH_15M",
    )
    engine.apply_execution_event(exit_fill)

    assert state.position is None
    assert state.exit_pending is False
    assert state.pending_exit_reason is None
