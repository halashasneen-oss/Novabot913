from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

CANONICAL_STRATEGY_913_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"
ONE_MINUTE_MS = 60_000

Side = Literal["long", "short"]
IntentType = Literal["enter", "exit", "stop_update"]


def normalize_symbol(symbol_or_pair: str) -> str:
    """Normalize Binance/Freqtrade symbols to Strategy 913's compact USD-M form."""

    value = symbol_or_pair.strip().upper()
    if not value:
        raise ValueError("symbol cannot be empty")

    if ":" in value:
        value = value.split(":", maxsplit=1)[0]
    return value.replace("/", "").replace("-", "")


def make_position_id(symbol_or_pair: str, entry_candle_open_ms: int, side: Side) -> str:
    """Build a deterministic identity for one Strategy 913 position attempt."""

    symbol = normalize_symbol(symbol_or_pair)
    if entry_candle_open_ms < 0:
        raise ValueError("entry_candle_open_ms must be non-negative")
    return f"{symbol}:{entry_candle_open_ms}:{side}"


@dataclass(frozen=True, slots=True)
class Strategy913Intent:
    """Execution intent emitted by Strategy 913 and consumed by an execution adapter."""

    symbol: str
    candle_open_ms: int
    decision_ms: int
    intent: IntentType
    side: Side
    score: int | None = None
    breakout_level: float | None = None
    stop_price: float | None = None
    reason: str | None = None
    position_id: str | None = None
    source_sha: str = CANONICAL_STRATEGY_913_SHA

    def __post_init__(self) -> None:
        symbol = normalize_symbol(self.symbol)
        object.__setattr__(self, "symbol", symbol)

        if self.candle_open_ms < 0 or self.decision_ms < 0:
            raise ValueError("timestamps must be non-negative milliseconds")
        expected_decision = self.candle_open_ms + ONE_MINUTE_MS
        if self.decision_ms != expected_decision:
            raise ValueError("intent decision_ms must equal the completed 1m candle close")
        if self.source_sha != CANONICAL_STRATEGY_913_SHA:
            raise ValueError("intent source_sha does not match frozen Strategy 913")

        if self.intent == "enter":
            if self.score is None or self.score < 5:
                raise ValueError("entry intent requires Strategy 913 score >= 5")
            if self.breakout_level is None or self.breakout_level <= 0:
                raise ValueError("entry intent requires a positive breakout_level")
            expected = make_position_id(symbol, self.candle_open_ms, self.side)
            if self.position_id is None:
                object.__setattr__(self, "position_id", expected)
            elif self.position_id != expected:
                raise ValueError("entry position_id does not match its deterministic identity")
        elif not self.position_id:
            raise ValueError(f"{self.intent} intent requires position_id")

        if self.intent == "stop_update" and (self.stop_price is None or self.stop_price <= 0):
            raise ValueError("stop_update intent requires a positive stop_price")
        if self.intent == "exit" and not self.reason:
            raise ValueError("exit intent requires a reason")

    @property
    def key(self) -> tuple[str, int, IntentType, str | None]:
        return self.symbol, self.candle_open_ms, self.intent, self.position_id

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> Strategy913Intent:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise TypeError("intent payload must decode to an object")
        return cls(**raw)


def canonical_leverage(score: int) -> float:
    """Frozen Strategy 913 requested leverage."""

    return 75.0 if score >= 6 else 50.0


def canonical_margin_fraction(score: int) -> float:
    """Frozen Strategy 913 requested fraction of total wallet margin."""

    return 0.50 if score >= 6 else 0.35


def canonical_stop_price_risk(leverage: float) -> float:
    """Frozen Strategy 913 initial stop as adverse price movement."""

    if leverage <= 0:
        raise ValueError("leverage must be positive")
    liquidation_distance = max(0.002, 1.0 / leverage - 0.005)
    return min(0.028, max(0.006, liquidation_distance * 0.55))


def canonical_progressive_trail(price_risk: float, mfe: float) -> float | None:
    """Frozen trailing distance as a fraction of price."""

    if price_risk <= 0:
        raise ValueError("price_risk must be positive")
    if mfe >= 6.0 * price_risk:
        return 0.35 * price_risk
    if mfe >= 4.0 * price_risk:
        return 0.50 * price_risk
    if mfe >= 2.5 * price_risk:
        return 0.75 * price_risk
    if mfe >= 1.5 * price_risk:
        return price_risk
    return None


class JsonlIntentBus:
    """Append-only JSONL contract between Strategy 913 and execution engines."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _append(self, intent: Strategy913Intent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(intent.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append(self, intent: Strategy913Intent) -> None:
        existing = {item.key for item in self.read_all()}
        if intent.key in existing:
            raise ValueError(f"duplicate Strategy 913 intent key: {intent.key}")
        self._append(intent)

    def append_once(self, intent: Strategy913Intent) -> bool:
        existing = {item.key for item in self.read_all()}
        if intent.key in existing:
            return False
        self._append(intent)
        return True

    def read_all(self) -> list[Strategy913Intent]:
        if not self.path.exists():
            return []

        intents: list[Strategy913Intent] = []
        seen: set[tuple[str, int, IntentType, str | None]] = set()
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                payload = line.strip()
                if not payload:
                    continue
                try:
                    intent = Strategy913Intent.from_json(payload)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid intent at {self.path}:{line_number}") from exc
                if intent.key in seen:
                    raise ValueError(f"duplicate Strategy 913 intent key: {intent.key}")
                seen.add(intent.key)
                intents.append(intent)
        intents.sort(key=lambda item: (item.decision_ms, item.symbol, item.intent))
        return intents

    def for_symbol(self, symbol_or_pair: str) -> list[Strategy913Intent]:
        symbol = normalize_symbol(symbol_or_pair)
        return [intent for intent in self.read_all() if intent.symbol == symbol]
