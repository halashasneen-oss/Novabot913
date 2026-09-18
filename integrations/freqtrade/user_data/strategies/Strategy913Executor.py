from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute
from pandas import DataFrame

from novabot913.execution_bus import ExecutionEvent, JsonlExecutionBus
from novabot913.signal_bus import (
    JsonlIntentBus,
    Strategy913Intent,
    canonical_leverage,
    canonical_margin_fraction,
    canonical_stop_price_risk,
    make_position_id,
)

_DEFAULT_INTENT_PATH = "/freqtrade/user_data/data/strategy913_intents.jsonl"
_DEFAULT_EXECUTION_PATH = "/freqtrade/user_data/data/strategy913_execution.jsonl"


class Strategy913Executor(IStrategy):
    """Freqtrade execution adapter for frozen Strategy 913 intents."""

    INTERFACE_VERSION = 3

    can_short = True
    timeframe = "1m"
    process_only_new_candles = True
    startup_candle_count = 1

    minimal_roi: dict[str, float] = {}
    stoploss = -0.45
    trailing_stop = False
    use_custom_stoploss = True
    use_exit_signal = True
    exit_profit_only = False
    position_adjustment_enable = False

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,
    }

    @property
    def intent_path(self) -> Path:
        return Path(os.environ.get("NOVABOT913_INTENT_FILE", _DEFAULT_INTENT_PATH))

    @property
    def execution_path(self) -> Path:
        return Path(os.environ.get("NOVABOT913_EXECUTION_FILE", _DEFAULT_EXECUTION_PATH))

    def _intents(self, pair: str) -> list[Strategy913Intent]:
        return JsonlIntentBus(self.intent_path).for_symbol(pair)

    @staticmethod
    def _tag(intent: Strategy913Intent) -> str:
        breakout = intent.breakout_level or 0.0
        score = intent.score or 0
        return f"s913|{score}|{breakout!r}|{intent.candle_open_ms}"

    @staticmethod
    def _parse_tag(entry_tag: str | None) -> tuple[int, float, int] | None:
        if not entry_tag or not entry_tag.startswith("s913|"):
            return None
        parts = entry_tag.split("|")
        if len(parts) != 4:
            return None
        try:
            return int(parts[1]), float(parts[2]), int(parts[3])
        except ValueError:
            return None

    @staticmethod
    def _candle_ms(value: object) -> int:
        timestamp = getattr(value, "timestamp", None)
        if timestamp is None:
            raise TypeError("Freqtrade candle date must provide timestamp()")
        return int(timestamp() * 1000)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["s913_enter_side"] = ""
        dataframe["s913_exit_side"] = ""
        dataframe["s913_score"] = 0
        dataframe["s913_breakout"] = 0.0
        dataframe["s913_exit_reason"] = ""

        if dataframe.empty:
            return dataframe

        row_by_ms = {self._candle_ms(value): index for index, value in dataframe["date"].items()}
        for intent in self._intents(metadata["pair"]):
            row_index = row_by_ms.get(intent.candle_open_ms)
            if row_index is None:
                continue
            if intent.intent == "enter":
                dataframe.at[row_index, "s913_enter_side"] = intent.side
                dataframe.at[row_index, "s913_score"] = intent.score or 0
                dataframe.at[row_index, "s913_breakout"] = intent.breakout_level or 0.0
            elif intent.intent == "exit":
                dataframe.at[row_index, "s913_exit_side"] = intent.side
                dataframe.at[row_index, "s913_exit_reason"] = intent.reason or "STRATEGY_913_EXIT"
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_mask = dataframe["s913_enter_side"] == "long"
        short_mask = dataframe["s913_enter_side"] == "short"

        dataframe.loc[long_mask, "enter_long"] = 1
        dataframe.loc[short_mask, "enter_short"] = 1

        for index in dataframe.index[long_mask | short_mask]:
            candle_ms = self._candle_ms(dataframe.at[index, "date"])
            score = int(dataframe.at[index, "s913_score"])
            breakout = float(dataframe.at[index, "s913_breakout"])
            side = "long" if bool(long_mask.at[index]) else "short"
            intent = Strategy913Intent(
                symbol=metadata["pair"],
                candle_open_ms=candle_ms,
                decision_ms=candle_ms + 60_000,
                intent="enter",
                side=side,
                score=score,
                breakout_level=breakout,
            )
            dataframe.at[index, "enter_tag"] = self._tag(intent)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_mask = dataframe["s913_exit_side"] == "long"
        short_mask = dataframe["s913_exit_side"] == "short"
        dataframe.loc[long_mask, "exit_long"] = 1
        dataframe.loc[short_mask, "exit_short"] = 1
        dataframe.loc[long_mask | short_mask, "exit_tag"] = dataframe.loc[
            long_mask | short_mask, "s913_exit_reason"
        ]
        return dataframe

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        parsed = self._parse_tag(entry_tag)
        if parsed is None:
            return proposed_leverage
        score, _, _ = parsed
        return min(canonical_leverage(score), max_leverage)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        parsed = self._parse_tag(entry_tag)
        if parsed is None:
            return 0.0

        score, _, _ = parsed
        requested_leverage = canonical_leverage(score)
        if abs(leverage - requested_leverage) > 1e-9:
            return 0.0

        total_stake = self.wallets.get_total_stake_amount()
        requested = total_stake * canonical_margin_fraction(score)
        if requested > max_stake:
            return 0.0
        if min_stake is not None and requested < min_stake:
            return 0.0
        return requested

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        parsed = self._parse_tag(entry_tag)
        if parsed is None:
            return False

        score, breakout, candle_open_ms = parsed
        current_ms = int(current_time.astimezone(UTC).timestamp() * 1000)
        decision_ms = candle_open_ms + 60_000
        if current_ms < decision_ms or current_ms > decision_ms + 90_000:
            return False

        expected_side = "short" if side == "short" else "long"
        position_id = make_position_id(pair, candle_open_ms, expected_side)
        for intent in self._intents(pair):
            if (
                intent.intent == "enter"
                and intent.position_id == position_id
                and intent.side == expected_side
                and intent.score == score
                and intent.breakout_level is not None
                and abs(intent.breakout_level - breakout) <= 1e-12
                and intent.decision_ms == decision_ms
            ):
                return True
        return False

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        parsed = self._parse_tag(trade.enter_tag)
        if parsed is None:
            return None

        _, _, candle_open_ms = parsed
        side = "short" if trade.is_short else "long"
        position_id = make_position_id(pair, candle_open_ms, side)
        current_ms = int(current_time.astimezone(UTC).timestamp() * 1000)
        stop_updates = [
            intent
            for intent in self._intents(pair)
            if intent.intent == "stop_update"
            and intent.position_id == position_id
            and intent.decision_ms <= current_ms
        ]

        if stop_updates:
            stop_price = stop_updates[-1].stop_price
            if stop_price is not None:
                return stoploss_from_absolute(
                    stop_price,
                    current_rate=current_rate,
                    is_short=trade.is_short,
                    leverage=trade.leverage,
                )

        price_risk = canonical_stop_price_risk(trade.leverage)
        stop_price = (
            trade.open_rate * (1.0 + price_risk)
            if trade.is_short
            else trade.open_rate * (1.0 - price_risk)
        )
        return stoploss_from_absolute(
            stop_price,
            current_rate=current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def order_filled(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: datetime,
        **kwargs,
    ) -> None:
        parsed = self._parse_tag(trade.enter_tag)
        if parsed is None:
            return

        score, breakout, candle_open_ms = parsed
        side = "short" if trade.is_short else "long"
        position_id = make_position_id(pair, candle_open_ms, side)
        filled_at = order.order_filled_utc or current_time
        timestamp_ms = int(filled_at.astimezone(UTC).timestamp() * 1000)
        order_key = order.order_id or f"{getattr(trade, 'id', 'na')}:{timestamp_ms}"
        event_type = "entry_fill" if order.ft_is_entry else "exit_fill"
        event_id = f"{event_type}:{order_key}"

        event = ExecutionEvent(
            event_id=event_id,
            symbol=pair,
            event=event_type,
            side=side,
            timestamp_ms=timestamp_ms,
            price=float(order.safe_price),
            leverage=float(trade.leverage),
            position_id=position_id,
            order_id=order.order_id,
            score=score if order.ft_is_entry else None,
            breakout_level=breakout if order.ft_is_entry else None,
            source_candle_open_ms=candle_open_ms if order.ft_is_entry else None,
            reason=None if order.ft_is_entry else (order.ft_order_tag or trade.exit_reason),
        )
        JsonlExecutionBus(self.execution_path).append_once(event)

