from __future__ import annotations

import json

import pytest

from novabot913.signal_bus import (
    CANONICAL_STRATEGY_913_SHA,
    JsonlIntentBus,
    Strategy913Intent,
    canonical_leverage,
    canonical_margin_fraction,
    canonical_progressive_trail,
    canonical_stop_price_risk,
    normalize_symbol,
)


def _entry(**overrides: object) -> Strategy913Intent:
    payload: dict[str, object] = {
        "symbol": "ZECUSDT",
        "candle_open_ms": 1_000_000,
        "decision_ms": 1_060_000,
        "intent": "enter",
        "side": "long",
        "score": 7,
        "breakout_level": 55.25,
    }
    payload.update(overrides)
    return Strategy913Intent(**payload)


def test_symbol_normalization_matches_freqtrade_futures_format() -> None:
    assert normalize_symbol("ZEC/USDT:USDT") == "ZECUSDT"
    assert normalize_symbol("zec-usdt") == "ZECUSDT"


def test_intent_requires_exact_completed_minute_decision() -> None:
    with pytest.raises(ValueError, match="completed 1m candle close"):
        _entry(decision_ms=1_059_999)


def test_intent_rejects_noncanonical_source() -> None:
    with pytest.raises(ValueError, match="source_sha"):
        _entry(source_sha="deadbeef")


def test_entry_requires_score_and_breakout() -> None:
    with pytest.raises(ValueError, match="score"):
        _entry(score=None)
    with pytest.raises(ValueError, match="breakout"):
        _entry(breakout_level=0.0)


def test_exit_requires_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        Strategy913Intent(
            symbol="ZECUSDT",
            candle_open_ms=1_000_000,
            decision_ms=1_060_000,
            intent="exit",
            side="long",
        )


def test_stop_update_requires_positive_price() -> None:
    with pytest.raises(ValueError, match="stop_price"):
        Strategy913Intent(
            symbol="ZECUSDT",
            candle_open_ms=1_000_000,
            decision_ms=1_060_000,
            intent="stop_update",
            side="long",
            stop_price=0.0,
        )


def test_json_round_trip_keeps_canonical_identity() -> None:
    original = _entry()
    decoded = Strategy913Intent.from_json(original.to_json())
    assert decoded == original
    assert decoded.source_sha == CANONICAL_STRATEGY_913_SHA


def test_bus_append_read_and_pair_filter(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    bus = JsonlIntentBus(path)
    first = _entry()
    second = _entry(
        symbol="BTCUSDT",
        candle_open_ms=1_060_000,
        decision_ms=1_120_000,
        side="short",
    )
    bus.append(second)
    bus.append(first)

    assert bus.read_all() == [first, second]
    assert bus.for_symbol("ZEC/USDT:USDT") == [first]


def test_bus_rejects_duplicate_execution_key(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    intent = _entry()
    path.write_text(intent.to_json() + "\n" + intent.to_json() + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        JsonlIntentBus(path).read_all()


def test_bus_rejects_malformed_line(tmp_path) -> None:
    path = tmp_path / "intents.jsonl"
    path.write_text(json.dumps({"not": "an intent"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid intent"):
        JsonlIntentBus(path).read_all()


def test_frozen_sizing_and_leverage_policy() -> None:
    assert canonical_margin_fraction(5) == 0.35
    assert canonical_leverage(5) == 50.0
    assert canonical_margin_fraction(6) == 0.50
    assert canonical_leverage(6) == 75.0
    assert canonical_margin_fraction(7) == 0.50
    assert canonical_leverage(7) == 75.0


def test_frozen_stop_and_trailing_policy() -> None:
    risk_75x = canonical_stop_price_risk(75.0)
    risk_50x = canonical_stop_price_risk(50.0)

    assert risk_75x == pytest.approx(0.006)
    assert risk_50x == pytest.approx(0.00825)

    assert canonical_progressive_trail(risk_75x, 1.49 * risk_75x) is None
    assert canonical_progressive_trail(risk_75x, 1.5 * risk_75x) == pytest.approx(risk_75x)
    assert canonical_progressive_trail(risk_75x, 2.5 * risk_75x) == pytest.approx(0.75 * risk_75x)
    assert canonical_progressive_trail(risk_75x, 4.0 * risk_75x) == pytest.approx(0.50 * risk_75x)
    assert canonical_progressive_trail(risk_75x, 6.0 * risk_75x) == pytest.approx(0.35 * risk_75x)
