from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from novabot913.signal_bus import Side, normalize_symbol

ExecutionEventType = Literal["entry_fill", "exit_fill", "entry_rejected"]


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    """Actual fill feedback from Freqtrade to the Strategy 913 live engine."""

    event_id: str
    symbol: str
    event: ExecutionEventType
    side: Side
    timestamp_ms: int
    position_id: str
    price: float | None = None
    leverage: float | None = None
    order_id: str | None = None
    score: int | None = None
    breakout_level: float | None = None
    source_candle_open_ms: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if not self.event_id.strip():
            raise ValueError("event_id cannot be empty")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if not self.position_id:
            raise ValueError("position_id cannot be empty")

        if self.event in {"entry_fill", "exit_fill"}:
            if self.price is None or self.price <= 0:
                raise ValueError("fill event requires a positive price")
            if self.leverage is None or self.leverage <= 0:
                raise ValueError("fill event requires positive leverage")

        if self.event == "entry_fill":
            if self.score is None or self.score < 5:
                raise ValueError("entry fill requires Strategy 913 score >= 5")
            if self.breakout_level is None or self.breakout_level <= 0:
                raise ValueError("entry fill requires breakout_level")
            if self.source_candle_open_ms is None or self.source_candle_open_ms < 0:
                raise ValueError("entry fill requires source_candle_open_ms")
        elif self.event == "entry_rejected":
            if not self.reason:
                raise ValueError("entry rejection requires reason")
            if self.score is None or self.score < 5:
                raise ValueError("entry rejection requires Strategy 913 score >= 5")
            if self.breakout_level is None or self.breakout_level <= 0:
                raise ValueError("entry rejection requires breakout_level")
            if self.source_candle_open_ms is None or self.source_candle_open_ms < 0:
                raise ValueError("entry rejection requires source_candle_open_ms")

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> ExecutionEvent:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise TypeError("execution payload must decode to an object")
        return cls(**raw)


class JsonlExecutionBus:
    """Append-only idempotent fill-feedback log."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def read_all(self) -> list[ExecutionEvent]:
        if not self.path.exists():
            return []

        events: list[ExecutionEvent] = []
        seen: set[str] = set()
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                payload = line.strip()
                if not payload:
                    continue
                try:
                    event = ExecutionEvent.from_json(payload)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    message = f"invalid execution event at {self.path}:{line_number}"
                    raise ValueError(message) from exc
                if event.event_id in seen:
                    raise ValueError(f"duplicate execution event_id: {event.event_id}")
                seen.add(event.event_id)
                events.append(event)
        events.sort(key=lambda item: (item.timestamp_ms, item.event_id))
        return events

    def append_once(self, event: ExecutionEvent) -> bool:
        if event.event_id in {item.event_id for item in self.read_all()}:
            return False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
