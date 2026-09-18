from __future__ import annotations

from novabot913.strategy913r1 import (
    DD_RECOVERY_THRESHOLD,
    DD_THROTTLE_LIMIT,
    HWM_FLOOR_RATIO,
    LEVERAGE_CAP,
    LOSS_STREAK_LIMIT,
    CircuitBreaker,
    position_size,
    regime_filter,
    sizing_basis,
)


def _bar(ts: int, close: float) -> tuple[int, float, float, float, float, float]:
    return (ts, close, close * 1.01, close * 0.99, close, 1.0)


def test_position_size_never_exceeds_risk_budget() -> None:
    for equity in (10.0, 100.0, 1000.0):
        for risk_fraction in (0.0025, 0.005, 0.01):
            for stop_distance in (0.006, 0.01, 0.028):
                for cap in (1.0, 5.0, LEVERAGE_CAP):
                    margin, leverage, notional = position_size(
                        equity,
                        risk_fraction,
                        stop_distance,
                        cap,
                    )
                    assert margin >= 0
                    assert 0 <= leverage <= cap
                    assert notional * stop_distance <= equity * risk_fraction + 1e-12
                    assert margin <= equity + 1e-12


def test_sizing_basis_never_exceeds_current_equity() -> None:
    assert sizing_basis(100.0, 100.0) == 100.0 * HWM_FLOOR_RATIO
    assert sizing_basis(50.0, 100.0) == 50.0
    assert sizing_basis(80.0, 100.0) == 100.0 * HWM_FLOOR_RATIO
    assert sizing_basis(20.0, 200.0) <= 20.0


def test_circuit_breaker_throttles_and_recovers() -> None:
    circuit = CircuitBreaker()
    audit: list[dict] = []

    for index in range(LOSS_STREAK_LIMIT):
        circuit.on_trade_close(index, -1.0, audit)
    assert circuit.throttled
    assert circuit.reason == "loss_streak"

    circuit.on_trade_close(100, 1.0, audit)
    assert not circuit.throttled
    assert circuit.loss_streak == 0

    circuit.on_equity_mark(1_000, 100.0, audit)
    circuit.on_equity_mark(2_000, 100.0 * (1.0 - DD_THROTTLE_LIMIT), audit)
    assert circuit.throttled
    assert circuit.reason == "rolling_drawdown"

    recovery_equity = 100.0 * (1.0 - DD_RECOVERY_THRESHOLD)
    circuit.on_equity_mark(3_000, recovery_equity, audit)
    assert not circuit.throttled


def test_regime_filter_ignores_future_4h_bars() -> None:
    four_hours = 4 * 60 * 60_000
    start = 1_700_000_000_000
    bars = [_bar(start + index * four_hours, 100.0 + index * 2.0) for index in range(14)]
    timestamp = bars[-1][0] + four_hours - 60_000

    data = {240: list(bars)}
    first = regime_filter("TEST", timestamp, data)

    future = _bar(timestamp + 60_000, 1_000_000.0)
    data_with_future = {240: [*bars, future]}
    second = regime_filter("TEST", timestamp, data_with_future)

    assert first["pass"] == second["pass"]
    assert first["efficiency"] == second["efficiency"]
    assert first["last_completed_4h_open"] == second["last_completed_4h_open"]
