from __future__ import annotations

from collections.abc import Callable
from typing import Any

MarketFilter = Callable[[str, int, int, Any, Any], dict[str, Any]]


def long_only_market_filter(base_filter: MarketFilter) -> MarketFilter:
    """Research-only wrapper that rejects otherwise-approved SHORT decisions."""

    def wrapped(
        symbol: str,
        timestamp: int,
        direction: int,
        btc_bars: Any,
        btc_index: Any,
    ) -> dict[str, Any]:
        result = base_filter(symbol, timestamp, direction, btc_bars, btc_index)
        if direction != -1 or not result.get("pass"):
            return result

        gated = dict(result)
        gated["pass"] = False
        gated["reason"] = "research_direction_gate"
        gated["research_gate"] = "LONG_ONLY"
        return gated

    return wrapped
