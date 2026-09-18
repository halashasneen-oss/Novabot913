from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RISK_FLOOR = 0.0025
RISK_CEILING = 0.0100
KELLY_MULTIPLIER = 0.35
KELLY_WINDOW = 20
KELLY_MIN_SAMPLES = 8
LEVERAGE_CAP = 75.0
HWM_FLOOR_RATIO = 0.70

STOP_ATR_LOOKBACK = 20
STOP_ATR_MULTIPLIER = 4.0
STOP_DISTANCE_MIN = 0.006
STOP_DISTANCE_MAX = 0.028

REGIME_LOOKBACK_4H = 12
REGIME_MIN_EFFICIENCY = 0.25
FOUR_HOURS_MS = 4 * 60 * 60_000

LOSS_STREAK_LIMIT = 2
DD_WINDOW_MS = 7 * 24 * 60 * 60_000
DD_THROTTLE_LIMIT = 0.10
DD_RECOVERY_THRESHOLD = 0.04
THROTTLE_FACTOR = 0.25


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stop_distance(
    symbol: str,
    entry: float,
    direction: int,
    ts: int,
    data: dict[Any, Any],
) -> float:
    del symbol, direction
    bars = data[1]
    completed = [bar for bar in bars if int(bar[0]) + 60_000 <= ts]
    sample = completed[-(STOP_ATR_LOOKBACK + 1) :]
    if len(sample) < 2 or entry <= 0:
        return STOP_DISTANCE_MIN

    true_ranges: list[float] = []
    for previous, current in zip(sample, sample[1:], strict=False):
        prev_close = float(previous[4])
        high = float(current[2])
        low = float(current[3])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr / entry)

    atr_fraction = sum(true_ranges) / len(true_ranges)
    return clamp(
        atr_fraction * STOP_ATR_MULTIPLIER,
        STOP_DISTANCE_MIN,
        STOP_DISTANCE_MAX,
    )


def position_size(
    equity: float,
    risk_fraction: float,
    stop_distance_fraction: float,
    leverage_cap: float,
) -> tuple[float, float, float]:
    if equity <= 0 or risk_fraction <= 0 or stop_distance_fraction <= 0 or leverage_cap <= 0:
        return 0.0, 0.0, 0.0

    risk_amount = equity * risk_fraction
    desired_notional = risk_amount / stop_distance_fraction
    leverage_needed = max(1.0, desired_notional / equity)
    leverage = min(float(leverage_cap), leverage_needed)
    notional = min(desired_notional, equity * leverage)
    margin = notional / leverage if leverage > 0 else 0.0
    return margin, leverage, notional


def sizing_basis(current_equity: float, high_water_mark: float) -> float:
    if current_equity <= 0 or high_water_mark <= 0:
        return 0.0
    return min(current_equity, high_water_mark * HWM_FLOOR_RATIO)


@dataclass
class RollingPerformanceTracker:
    by_score: dict[int, list[float]] = field(default_factory=dict)

    def record(self, score: int, r_multiple: float) -> None:
        bucket = self.by_score.setdefault(int(score), [])
        bucket.append(float(r_multiple))
        if len(bucket) > KELLY_WINDOW:
            del bucket[:-KELLY_WINDOW]

    def risk_fraction(self, score: int) -> float:
        bucket = self.by_score.get(int(score), [])
        if len(bucket) < KELLY_MIN_SAMPLES:
            return RISK_FLOOR

        wins = [value for value in bucket if value > 0]
        losses = [-value for value in bucket if value < 0]
        if not wins or not losses:
            return RISK_FLOOR

        win_rate = len(wins) / len(bucket)
        avg_win_r = sum(wins) / len(wins)
        avg_loss_r = sum(losses) / len(losses)
        if avg_loss_r <= 0:
            return RISK_FLOOR

        b = avg_win_r / avg_loss_r
        if b <= 0:
            return RISK_FLOOR

        kelly = win_rate - (1.0 - win_rate) / b
        if kelly <= 0:
            return RISK_FLOOR

        return clamp(
            kelly * KELLY_MULTIPLIER,
            RISK_FLOOR,
            RISK_CEILING,
        )


def regime_filter(symbol: str, timestamp: int, data: dict[Any, Any]) -> dict[str, Any]:
    decision_ms = int(timestamp) + 60_000
    completed = [
        bar
        for bar in data[240]
        if int(bar[0]) + FOUR_HOURS_MS <= decision_ms
    ]

    if len(completed) < REGIME_LOOKBACK_4H + 1:
        return {
            "pass": False,
            "reason": "regime_reject",
            "symbol": symbol,
            "decision_ms": decision_ms,
            "completed_bars": len(completed),
            "efficiency": 0.0,
        }

    sample = completed[-(REGIME_LOOKBACK_4H + 1) :]
    closes = [float(bar[4]) for bar in sample]
    path = sum(\n        abs(current / previous - 1.0)\n        for previous, current in zip(closes, closes[1:], strict=False)\n    )
    net_move = abs(closes[-1] / closes[0] - 1.0)
    efficiency = net_move / path if path > 0 else 0.0
    passed = efficiency >= REGIME_MIN_EFFICIENCY
    return {
        "pass": passed,
        "reason": None if passed else "regime_reject",
        "symbol": symbol,
        "decision_ms": decision_ms,
        "last_completed_4h_open": int(sample[-1][0]),
        "efficiency": efficiency,
        "threshold": REGIME_MIN_EFFICIENCY,
    }


@dataclass
class CircuitBreaker:
    loss_streak: int = 0
    throttled: bool = False
    reason: str | None = None
    rolling_drawdown: float = 0.0
    marks: list[tuple[int, float]] = field(default_factory=list)

    def effective_risk_fraction(self, base_risk_fraction: float) -> float:
        if not self.throttled:
            return base_risk_fraction
        return max(RISK_FLOOR * THROTTLE_FACTOR, base_risk_fraction * THROTTLE_FACTOR)

    def on_trade_close(
        self,
        timestamp: int,
        net: float,
        audit_sink: list[dict[str, Any]],
    ) -> None:
        was_throttled = self.throttled
        if net > 0:
            self.loss_streak = 0
            self.throttled = False
            self.reason = None
        else:
            self.loss_streak += 1
            if self.loss_streak >= LOSS_STREAK_LIMIT:
                self.throttled = True
                self.reason = "loss_streak"

        if self.throttled != was_throttled:
            audit_sink.append(
                {
                    "timestamp": int(timestamp),
                    "event": "throttle_on" if self.throttled else "throttle_off",
                    "reason": self.reason or "winning_trade",
                    "loss_streak": self.loss_streak,
                    "rolling_drawdown": self.rolling_drawdown,
                }
            )

    def on_equity_mark(
        self,
        timestamp: int,
        equity: float,
        audit_sink: list[dict[str, Any]],
    ) -> None:
        cutoff = int(timestamp) - DD_WINDOW_MS
        self.marks.append((int(timestamp), float(equity)))
        self.marks = [item for item in self.marks if item[0] >= cutoff]
        peak = max((value for _, value in self.marks), default=float(equity))
        self.rolling_drawdown = (peak - equity) / peak if peak > 0 else 0.0

        was_throttled = self.throttled
        if self.rolling_drawdown >= DD_THROTTLE_LIMIT:
            self.throttled = True
            self.reason = "rolling_drawdown"
        elif (
            self.throttled
            and self.reason == "rolling_drawdown"
            and self.rolling_drawdown <= DD_RECOVERY_THRESHOLD
        ):
            self.throttled = False
            self.reason = None

        if self.throttled != was_throttled:
            audit_sink.append(
                {
                    "timestamp": int(timestamp),
                    "event": "throttle_on" if self.throttled else "throttle_off",
                    "reason": self.reason or "drawdown_recovery",
                    "loss_streak": self.loss_streak,
                    "rolling_drawdown": self.rolling_drawdown,
                }
            )
