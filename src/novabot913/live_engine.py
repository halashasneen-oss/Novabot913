from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from novabot913.execution_bus import ExecutionEvent
from novabot913.signal_bus import (
    Side,
    Strategy913Intent,
    canonical_leverage,
    canonical_progressive_trail,
    canonical_stop_price_risk,
    make_position_id,
    normalize_symbol,
)
from novabot913.strategy_core import (
    ARM_WINDOW_MINUTES,
    BE_LOCK,
    CANONICAL_UNIVERSE,
    EARLY_FAILURE_MINUTES,
    FOLLOWTHROUGH_MIN_CLOSE_MOVE,
    FOLLOWTHROUGH_MIN_MFE,
    FOLLOWTHROUGH_MINUTE,
    INVALIDATION_MOVE,
    MAX_HOLD_MINUTES,
    MINUTE_MS,
    RETEST_APPROACH,
    RETEST_FAIL,
    RETEST_WINDOW_MINUTES,
    Bar,
    MarketFilterResult,
    Setup,
    breakout_level,
    micro_confirmation,
    setup_closing_at,
    tf_vote,
)

type MarketFilterFn = Callable[[str, int, int], MarketFilterResult]


@dataclass(slots=True)
class ArmedSetup:
    direction: int
    score: int
    signal_close: float
    arm_ts: int
    expires_ms: int


@dataclass(slots=True)
class RetestState:
    direction: int
    score: int
    signal_close: float
    confirm_ts: int
    breakout_level: float
    expires_ms: int
    retest_seen: bool = False


@dataclass(slots=True)
class PendingEntry:
    position_id: str
    side: Side
    score: int
    breakout_level: float
    source_candle_open_ms: int
    expires_ms: int


@dataclass(slots=True)
class PositionState:
    position_id: str
    side: Side
    direction: int
    score: int
    breakout_level: float
    entry_price: float
    leverage: float
    fill_timestamp_ms: int
    held_minutes: int = 0
    best_close: float = 0.0
    mfe: float = 0.0
    followthrough_mfe: float = 0.0
    wrong_side_count: int = 0
    active_stop: float = 0.0
    pending_stop: float | None = None


@dataclass(slots=True)
class SymbolState:
    last_processed_ms: int = -1
    armed: ArmedSetup | None = None
    retest: RetestState | None = None
    pending_entry: PendingEntry | None = None
    position: PositionState | None = None
    exit_pending: bool = False


@dataclass(slots=True)
class Strategy913LiveEngine:
    """Stateful causal Strategy 913 signal producer.

    The engine consumes completed one-minute candles in strict order. It emits
    intents only; Freqtrade remains responsible for exchange execution.
    """

    symbols: tuple[str, ...] = CANONICAL_UNIVERSE
    states: dict[str, SymbolState] = field(default_factory=dict)
    processed_event_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        normalized = tuple(normalize_symbol(symbol) for symbol in self.symbols)
        if normalized != CANONICAL_UNIVERSE:
            raise ValueError("live Strategy 913 must use the frozen canonical 10-symbol universe")
        self.symbols = normalized
        for symbol in self.symbols:
            self.states.setdefault(symbol, SymbolState())

    def apply_execution_event(self, event: ExecutionEvent) -> None:
        if event.event_id in self.processed_event_ids:
            return
        if event.symbol not in self.states:
            raise ValueError(f"execution event symbol outside canonical universe: {event.symbol}")

        state = self.states[event.symbol]
        if event.event == "entry_fill":
            if event.score is None or event.breakout_level is None:
                raise ValueError("entry fill is missing Strategy 913 metadata")
            expected_leverage = canonical_leverage(event.score)
            if abs(event.leverage - expected_leverage) > 1e-9:
                raise ValueError(
                    f"live fill leverage {event.leverage} does not match frozen {expected_leverage}"
                )

            direction = 1 if event.side == "long" else -1
            risk = canonical_stop_price_risk(event.leverage)
            active_stop = event.price * (1.0 - direction * risk)
            state.position = PositionState(
                position_id=event.position_id,
                side=event.side,
                direction=direction,
                score=event.score,
                breakout_level=event.breakout_level,
                entry_price=event.price,
                leverage=event.leverage,
                fill_timestamp_ms=event.timestamp_ms,
                best_close=event.price,
                active_stop=active_stop,
            )
            state.pending_entry = None
            state.armed = None
            state.retest = None
            state.exit_pending = False
        else:
            if state.position is not None and state.position.position_id != event.position_id:
                message = "exit fill position_id does not match active Strategy 913 position"
                raise ValueError(message)
            state.position = None
            state.pending_entry = None
            state.exit_pending = False

        self.processed_event_ids.add(event.event_id)

    def process_closed_candle(
        self,
        symbol_or_pair: str,
        one_minute_bars: Sequence[Bar],
        four_hour_bars: Sequence[Bar],
        market_filter: MarketFilterFn,
    ) -> list[Strategy913Intent]:
        symbol = normalize_symbol(symbol_or_pair)
        if symbol not in self.states:
            raise ValueError(f"symbol outside canonical Strategy 913 universe: {symbol}")
        if not one_minute_bars:
            raise ValueError("one_minute_bars cannot be empty")

        bars = list(one_minute_bars)
        current = bars[-1]
        candle_open_ms = current[0]
        state = self.states[symbol]

        if state.last_processed_ms == candle_open_ms:
            return []
        if state.last_processed_ms >= 0 and candle_open_ms != state.last_processed_ms + MINUTE_MS:
            raise ValueError(
                f"non-contiguous candle for {symbol}: "
                f"{state.last_processed_ms} -> {candle_open_ms}"
            )

        intents: list[Strategy913Intent] = []
        if state.position is not None:
            intents.extend(self._manage_position(symbol, state, bars))
        else:
            self._expire_unfilled_entry(state, candle_open_ms)
            if state.pending_entry is None:
                intents.extend(
                    self._search_entry(
                        symbol,
                        state,
                        bars,
                        four_hour_bars,
                        market_filter,
                    )
                )

        state.last_processed_ms = candle_open_ms
        return intents

    def _expire_unfilled_entry(self, state: SymbolState, candle_open_ms: int) -> None:
        pending = state.pending_entry
        if pending is not None and candle_open_ms + MINUTE_MS > pending.expires_ms:
            state.pending_entry = None

    def _search_entry(
        self,
        symbol: str,
        state: SymbolState,
        bars: list[Bar],
        four_hour_bars: Sequence[Bar],
        market_filter: MarketFilterFn,
    ) -> list[Strategy913Intent]:
        candle = bars[-1]
        candle_open_ms = candle[0]

        if state.armed is None and state.retest is None:
            setup = setup_closing_at(four_hour_bars, candle_open_ms)
            if setup is not None:
                state.armed = self._arm_setup(setup, candle_open_ms)

        if state.armed is not None:
            armed = state.armed
            if candle_open_ms > armed.expires_ms:
                state.armed = None
            else:
                adverse = -armed.direction * (candle[4] / armed.signal_close - 1.0)
                if adverse > INVALIDATION_MOVE:
                    state.armed = None
                else:
                    confirmation = micro_confirmation(candle_open_ms, bars, armed.direction)
                    if confirmation is not None:
                        level = breakout_level(
                            candle_open_ms,
                            bars,
                            armed.direction,
                            confirmation,
                        )
                        if level is not None:
                            state.retest = RetestState(
                                direction=armed.direction,
                                score=armed.score,
                                signal_close=armed.signal_close,
                                confirm_ts=candle_open_ms,
                                breakout_level=level,
                                expires_ms=(
                                    candle_open_ms + RETEST_WINDOW_MINUTES * MINUTE_MS
                                ),
                            )
                            state.armed = None

        retest = state.retest
        if retest is None or candle_open_ms <= retest.confirm_ts:
            return []

        if candle_open_ms > retest.expires_ms:
            state.retest = None
            return []

        level = retest.breakout_level
        direction = retest.direction
        close = candle[4]
        failed = (
            close < level * (1.0 - RETEST_FAIL)
            if direction == 1
            else close > level * (1.0 + RETEST_FAIL)
        )
        if failed:
            state.retest = None
            return []

        touched = (
            candle[3] <= level * (1.0 + RETEST_APPROACH)
            if direction == 1
            else candle[2] >= level * (1.0 - RETEST_APPROACH)
        )
        if touched:
            retest.retest_seen = True
        if not retest.retest_seen:
            return []

        index = len(bars) - 1
        vote_ok = tf_vote(bars, index, direction, 1)
        relaunched = close > level if direction == 1 else close < level
        if not (vote_ok and relaunched):
            return []

        filter_result = market_filter(symbol, candle_open_ms, direction)
        if not filter_result.passed:
            return []

        side: Side = "long" if direction == 1 else "short"
        position_id = make_position_id(symbol, candle_open_ms, side)
        intent = Strategy913Intent(
            symbol=symbol,
            candle_open_ms=candle_open_ms,
            decision_ms=candle_open_ms + MINUTE_MS,
            intent="enter",
            side=side,
            score=retest.score,
            breakout_level=level,
            position_id=position_id,
        )
        state.pending_entry = PendingEntry(
            position_id=position_id,
            side=side,
            score=retest.score,
            breakout_level=level,
            source_candle_open_ms=candle_open_ms,
            expires_ms=candle_open_ms + 3 * MINUTE_MS,
        )
        state.retest = None
        return [intent]

    @staticmethod
    def _arm_setup(setup: Setup, arm_ts: int) -> ArmedSetup:
        return ArmedSetup(
            direction=setup.direction,
            score=setup.score,
            signal_close=setup.signal_close,
            arm_ts=arm_ts,
            expires_ms=arm_ts + ARM_WINDOW_MINUTES * MINUTE_MS,
        )

    def _manage_position(
        self,
        symbol: str,
        state: SymbolState,
        bars: list[Bar],
    ) -> list[Strategy913Intent]:
        position = state.position
        if position is None or state.exit_pending:
            return []

        candle = bars[-1]
        candle_open_ms = candle[0]
        fill_minute = (position.fill_timestamp_ms // MINUTE_MS) * MINUTE_MS
        if candle_open_ms < fill_minute:
            return []

        if position.pending_stop is not None:
            if position.direction == 1:
                position.active_stop = max(position.active_stop, position.pending_stop)
            else:
                position.active_stop = min(position.active_stop, position.pending_stop)
            position.pending_stop = None

        position.held_minutes += 1
        direction = position.direction
        high = candle[2]
        low = candle[3]
        close = candle[4]
        intents: list[Strategy913Intent] = []

        current_follow_mfe = (
            high / position.entry_price - 1.0
            if direction == 1
            else position.entry_price / low - 1.0
        )
        position.followthrough_mfe = max(position.followthrough_mfe, current_follow_mfe)

        exit_reason: str | None = None
        if position.held_minutes == FOLLOWTHROUGH_MINUTE:
            close_move = direction * (close / position.entry_price - 1.0)
            if (
                close_move < FOLLOWTHROUGH_MIN_CLOSE_MOVE
                and position.followthrough_mfe < FOLLOWTHROUGH_MIN_MFE
            ):
                exit_reason = "FOLLOWTHROUGH_15M"

        if direction == 1:
            position.best_close = max(position.best_close, close)
            current_mfe = position.best_close / position.entry_price - 1.0
        else:
            position.best_close = min(position.best_close, close)
            current_mfe = position.entry_price / position.best_close - 1.0
        position.mfe = max(position.mfe, current_mfe)

        risk = canonical_stop_price_risk(position.leverage)
        trail_distance = canonical_progressive_trail(risk, position.mfe)
        if trail_distance is not None:
            break_even = position.entry_price * (1.0 + direction * BE_LOCK)
            trail = (
                position.best_close * (1.0 - trail_distance)
                if direction == 1
                else position.best_close * (1.0 + trail_distance)
            )
            proposed = max(break_even, trail) if direction == 1 else min(break_even, trail)
            effective = (
                max(position.active_stop, proposed)
                if direction == 1
                else min(position.active_stop, proposed)
            )
            tightened = (
                effective > position.active_stop + 1e-12
                if direction == 1
                else effective < position.active_stop - 1e-12
            )
            if tightened:
                position.pending_stop = effective
                intents.append(
                    Strategy913Intent(
                        symbol=symbol,
                        candle_open_ms=candle_open_ms,
                        decision_ms=candle_open_ms + MINUTE_MS,
                        intent="stop_update",
                        side=position.side,
                        stop_price=effective,
                        position_id=position.position_id,
                    )
                )

        if position.held_minutes <= EARLY_FAILURE_MINUTES:
            wrong_side = (
                close < position.breakout_level
                if direction == 1
                else close > position.breakout_level
            )
            vote_ok = tf_vote(bars, len(bars) - 1, direction, 1)
            if wrong_side and not vote_ok:
                position.wrong_side_count += 1
            else:
                position.wrong_side_count = 0
            if position.wrong_side_count >= 2:
                exit_reason = "EARLY_FAILURE"

        if position.held_minutes >= MAX_HOLD_MINUTES:
            exit_reason = "TIME_12H"

        if exit_reason is not None:
            state.exit_pending = True
            intents.append(
                Strategy913Intent(
                    symbol=symbol,
                    candle_open_ms=candle_open_ms,
                    decision_ms=candle_open_ms + MINUTE_MS,
                    intent="exit",
                    side=position.side,
                    reason=exit_reason,
                    position_id=position.position_id,
                )
            )
        return intents

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "processed_event_ids": sorted(self.processed_event_ids),
            "states": {symbol: asdict(state) for symbol, state in self.states.items()},
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> Strategy913LiveEngine:
        symbols = tuple(payload.get("symbols", ()))
        engine = cls(symbols=symbols)
        engine.processed_event_ids = set(payload.get("processed_event_ids", ()))
        raw_states = payload.get("states", {})

        for symbol in engine.symbols:
            raw = raw_states.get(symbol, {})
            engine.states[symbol] = SymbolState(
                last_processed_ms=int(raw.get("last_processed_ms", -1)),
                armed=_restore(ArmedSetup, raw.get("armed")),
                retest=_restore(RetestState, raw.get("retest")),
                pending_entry=_restore(PendingEntry, raw.get("pending_entry")),
                position=_restore(PositionState, raw.get("position")),
                exit_pending=bool(raw.get("exit_pending", False)),
            )
        return engine


def _restore[T](kind: type[T], value: object) -> T | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"invalid persisted {kind.__name__}")
    return kind(**value)


class JsonLiveStateStore:
    """Atomic persistence for the live producer state."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def load(self) -> Strategy913LiveEngine:
        if not self.path.exists():
            return Strategy913LiveEngine()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("live state must decode to an object")
        return Strategy913LiveEngine.from_snapshot(payload)

    def save(self, engine: Strategy913LiveEngine) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(engine.snapshot(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
