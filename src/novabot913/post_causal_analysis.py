from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import inf
from typing import Any

BASELINE_CAUSAL_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"
EXPECTED_UNCHANGED_TIMING = {"OI", "Crowding", "Funding"}
WINDOW_KEYS = ("may_2026", "aug_sep_2026")

Trade = Mapping[str, Any]


def _number(value: object, field: str) -> float:
    if not isinstance(value, int | float):
        raise TypeError(f"{field} must be numeric")
    return float(value)


def _group_stats(items: Sequence[Trade], label: str) -> dict[str, Any]:
    nets = [_number(item["net"], "net") for item in items]
    holds = [_number(item["held_minutes"], "held_minutes") for item in items]
    gross_profit = sum(max(value, 0.0) for value in nets)
    gross_loss = -sum(min(value, 0.0) for value in nets)
    wins = sum(value > 0.0 for value in nets)
    losses = sum(value < 0.0 for value in nets)
    profit_factor = gross_profit / gross_loss if gross_loss else inf
    return {
        "group": label,
        "trades": len(items),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round((wins / len(items) * 100.0) if items else 0.0, 4),
        "net": round(sum(nets), 8),
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": round(profit_factor, 8) if profit_factor != inf else "inf",
        "avg_net": round((sum(nets) / len(items)) if items else 0.0, 8),
        "avg_hold_minutes": round((sum(holds) / len(items)) if items else 0.0, 4),
    }


def _grouped(trades: Sequence[Trade], key_fn: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade))].append(trade)
    rows = [_group_stats(items, label) for label, items in groups.items()]
    return sorted(rows, key=lambda row: (row["net"], row["group"]))


def summarize_trades(trades: Sequence[Trade]) -> dict[str, Any]:
    """Build deterministic diagnostics from completed causal trades only."""

    def entry_hour_utc(trade: Trade) -> int:
        entry_ts = int(_number(trade["entry_ts"], "entry_ts"))
        return datetime.fromtimestamp(entry_ts / 1000.0, UTC).hour

    return {
        "overall": _group_stats(trades, "all"),
        "by_symbol": _grouped(trades, lambda trade: trade["symbol"]),
        "by_direction": _grouped(trades, lambda trade: trade["direction"]),
        "by_exit_reason": _grouped(trades, lambda trade: trade["reason"]),
        "by_score": _grouped(trades, lambda trade: trade["score"]),
        "by_entry_hour_utc": _grouped(trades, entry_hour_utc),
    }


def build_post_causal_report(reference: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze the frozen corrected reference without changing strategy behavior."""

    unchanged = set(reference.get("unchanged_timing", []))
    if unchanged != EXPECTED_UNCHANGED_TIMING:
        raise ValueError("unexpected timing scope; refusing to mix research with causal correction")

    windows: dict[str, Any] = {}
    combined: list[Trade] = []
    for window_key in WINDOW_KEYS:
        window = reference.get(window_key)
        if not isinstance(window, Mapping):
            raise ValueError(f"missing reference window: {window_key}")
        trades = window.get("corrected_trades")
        if not isinstance(trades, list):
            raise ValueError(f"missing corrected trades: {window_key}")
        windows[window_key] = summarize_trades(trades)
        combined.extend(trades)

    return {
        "phase": "post_causal_research_phase_1_diagnostics",
        "baseline_causal_sha": BASELINE_CAUSAL_SHA,
        "analysis_only": True,
        "strategy_logic_changed": False,
        "timing_scope_changed": False,
        "windows": windows,
        "combined": summarize_trades(combined),
    }
