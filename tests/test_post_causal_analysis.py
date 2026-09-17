from __future__ import annotations

from copy import deepcopy

import pytest

from novabot913.post_causal_analysis import build_post_causal_report, summarize_trades


def _trade(
    symbol: str,
    direction: str,
    net: float,
    *,
    reason: str = "TRAIL_STOP",
    score: int = 6,
    entry_ts: int = 0,
    held_minutes: int = 10,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "direction": direction,
        "net": net,
        "reason": reason,
        "score": score,
        "entry_ts": entry_ts,
        "held_minutes": held_minutes,
    }


def test_summarize_trades_groups_without_mutating_input() -> None:
    trades = [
        _trade("AAAUSDT", "LONG", 10.0, entry_ts=0),
        _trade("AAAUSDT", "LONG", -4.0, reason="STOP", entry_ts=3_600_000),
        _trade("BBBUSDT", "SHORT", 2.0, entry_ts=3_600_000),
    ]
    original = deepcopy(trades)

    report = summarize_trades(trades)

    assert trades == original
    assert report["overall"]["net"] == 8.0
    assert report["overall"]["trades"] == 3
    assert report["overall"]["profit_factor"] == 3.0
    assert [row["group"] for row in report["by_symbol"]] == ["BBBUSDT", "AAAUSDT"]
    assert [row["group"] for row in report["by_entry_hour_utc"]] == ["1", "0"]


def test_zero_gross_loss_reports_infinite_profit_factor() -> None:
    report = summarize_trades([_trade("AAAUSDT", "LONG", 5.0)])
    assert report["overall"]["profit_factor"] == "inf"


def test_build_report_requires_frozen_timing_scope() -> None:
    reference = {
        "unchanged_timing": ["OI", "Crowding", "Funding"],
        "may_2026": {"corrected_trades": [_trade("AAAUSDT", "LONG", 1.0)]},
        "aug_sep_2026": {"corrected_trades": [_trade("BBBUSDT", "SHORT", -1.0)]},
    }

    report = build_post_causal_report(reference)

    assert report["analysis_only"] is True
    assert report["strategy_logic_changed"] is False
    assert report["combined"]["overall"]["trades"] == 2
    assert report["combined"]["overall"]["net"] == 0.0


def test_build_report_rejects_timing_scope_drift() -> None:
    reference = {
        "unchanged_timing": ["OI", "Funding"],
        "may_2026": {"corrected_trades": []},
        "aug_sep_2026": {"corrected_trades": []},
    }

    with pytest.raises(ValueError, match="unexpected timing scope"):
        build_post_causal_report(reference)
