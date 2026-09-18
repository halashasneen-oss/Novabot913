from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from novabot913.strategy_core import (
    MINUTE_MS,
    MarketFilterInputs,
    aggregate,
    evaluate_market_filters,
    four_hour_setups,
    micro_confirmation,
)

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference" / "strategy913"


def _load_reference(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REFERENCE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reference module {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _four_hour_bars() -> list[tuple[int, float, float, float, float, float]]:
    bars = []
    step = 4 * 60 * MINUTE_MS
    previous_close = 100.0
    for index in range(15):
        open_price = previous_close
        close = open_price * 1.001
        volume = 100.0
        if index == 10:
            close = open_price * 1.03
            volume = 300.0
        high = max(open_price, close) * 1.001
        low = min(open_price, close) * 0.999
        bars.append((index * step, open_price, high, low, close, volume))
        previous_close = close
    return bars


def _minute_bars() -> list[tuple[int, float, float, float, float, float]]:
    bars = []
    previous_close = 100.0
    for index in range(90):
        open_price = previous_close
        close = open_price * 1.001
        high = close * 1.0002
        low = open_price * 0.9998
        volume = 1000.0 if index == 89 else 100.0
        bars.append((index * MINUTE_MS, open_price, high, low, close, volume))
        previous_close = close
    return bars


def test_four_hour_scoring_matches_frozen_reference() -> None:
    reference = _load_reference("s913_zec_reference", "tmp_zec_extreme_reference.py")
    bars = _four_hour_bars()

    expected = reference._signals(bars)
    actual = four_hour_setups(bars, require_next_bar=True)

    assert set(actual) == set(expected)
    for timestamp, setup in actual.items():
        item = expected[timestamp]
        assert setup.direction == item["direction"]
        assert setup.score == item["score"]
        assert setup.vol_ratio == pytest.approx(item["vol_ratio"])
        assert setup.ret1 == pytest.approx(item["ret1"])
        assert setup.signal_close == pytest.approx(item["signal_close"])


def test_micro_confirmation_matches_frozen_reference() -> None:
    reference = _load_reference("s913_micro_reference", "tmp_ten_score7plus_flow.py")
    bars = _minute_bars()
    data = {
        1: bars,
        3: reference._aggregate(bars, 3),
        5: reference._aggregate(bars, 5),
        "index_1m": {bar[0]: index for index, bar in enumerate(bars)},
    }
    timestamp = bars[-1][0]

    expected = reference._micro_confirmation(timestamp, data, 1)
    actual = micro_confirmation(timestamp, bars, 1)

    assert expected is not None
    assert actual is not None
    assert actual.votes == expected["votes"]
    assert actual.vr1 == pytest.approx(expected["vr1"])
    assert actual.vr3 == pytest.approx(expected["vr3"])
    assert actual.bo1 == expected["bo1"]
    assert actual.bo3 == expected["bo3"]


def test_market_filters_keep_authorized_timing_boundaries() -> None:
    candle_open = 2_400_000
    decision = candle_open + MINUTE_MS

    taker_rows = [
        {"timestamp": decision - 5 * MINUTE_MS, "taker_ratio": 1.30},
        {"timestamp": candle_open, "taker_ratio": 0.50},
    ]
    premium_rows = [
        {"timestamp": decision - 5 * MINUTE_MS, "close": 0.001},
        {"timestamp": candle_open, "close": 0.010},
    ]
    oi_rows = [
        {"timestamp": candle_open - 30 * MINUTE_MS, "oi": 100.0},
        {"timestamp": candle_open, "oi": 101.0},
    ]
    crowding_rows = [{"timestamp": candle_open, "top_ratio": 2.0}]
    funding_rows = [{"timestamp": candle_open, "rate": 0.0}]

    btc_bars = []
    price = 100.0
    for index in range(11):
        close = price * 1.002
        btc_bars.append(
            (
                candle_open - (10 - index) * MINUTE_MS,
                price,
                close,
                price,
                close,
                1.0,
            )
        )
        price = close

    result = evaluate_market_filters(
        candle_open,
        1,
        MarketFilterInputs(
            taker_rows=taker_rows,
            oi_rows=oi_rows,
            crowding_rows=crowding_rows,
            premium_rows=premium_rows,
            funding_rows=funding_rows,
            btc_bars=btc_bars,
        ),
    )

    assert result.passed
    assert result.taker_ratio == pytest.approx(1.30)
    assert result.premium_pct == pytest.approx(0.1)


def test_aggregate_uses_exchange_aligned_time_buckets() -> None:
    bars = _minute_bars()[:6]
    three = aggregate(bars, 3)
    assert [bar[0] for bar in three] == [0, 3 * MINUTE_MS]
    assert three[0][5] == pytest.approx(300.0)
