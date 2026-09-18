from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from novabot913.strategy_core import (
    MINUTE_MS,
    Bar,
    MarketFilterInputs,
    MarketFilterResult,
    evaluate_market_filters,
)

_FAPI_BASE = "https://fapi.binance.com"


class BinancePublicDataError(RuntimeError):
    """Raised when required Binance public market data cannot be fetched or parsed."""


class BinanceUSDMPublicClient:
    """Minimal read-only Binance USD-M client for Strategy 913 live signals."""

    def __init__(self, *, timeout: float = 15.0, attempts: int = 3) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if attempts <= 0:
            raise ValueError("attempts must be positive")
        self.timeout = timeout
        self.attempts = attempts

    def _get_json(self, path: str, params: Mapping[str, object]) -> Any:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{_FAPI_BASE}{path}?{query}",
            headers={"User-Agent": "Novabot913/1.0"},
        )
        last_error: Exception | None = None

        for attempt in range(self.attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(0.5 * (attempt + 1))

        raise BinancePublicDataError(f"Binance request failed for {path}") from last_error

    @staticmethod
    def _bar(row: list[object]) -> Bar:
        return (
            int(row[0]),
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
        )

    def klines(
        self,
        symbol: str,
        interval: str,
        *,
        decision_ms: int,
        limit: int,
    ) -> list[Bar]:
        rows = self._get_json(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "endTime": decision_ms - 1,
                "limit": limit,
            },
        )
        if not isinstance(rows, list):
            raise BinancePublicDataError("unexpected Binance kline response")

        bars: list[Bar] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise BinancePublicDataError("malformed Binance kline row")
            close_time = int(row[6])
            if close_time < decision_ms:
                bars.append(self._bar(row))
        bars.sort(key=lambda item: item[0])
        return bars

    def one_minute_bars(
        self,
        symbol: str,
        *,
        decision_ms: int,
        limit: int = 500,
    ) -> list[Bar]:
        return self.klines(symbol, "1m", decision_ms=decision_ms, limit=limit)

    def four_hour_bars(
        self,
        symbol: str,
        *,
        decision_ms: int,
        limit: int = 40,
    ) -> list[Bar]:
        return self.klines(symbol, "4h", decision_ms=decision_ms, limit=limit)

    def _period_rows(
        self,
        path: str,
        symbol: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        rows = self._get_json(
            path,
            {
                "symbol": symbol,
                "period": "5m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            },
        )
        if not isinstance(rows, list):
            raise BinancePublicDataError(f"unexpected Binance response for {path}")
        return [dict(row) for row in rows if isinstance(row, dict)]

    def market_inputs(self, symbol: str, candle_open_ms: int) -> MarketFilterInputs:
        decision_ms = candle_open_ms + MINUTE_MS
        start_ms = candle_open_ms - 90 * MINUTE_MS

        taker_raw = self._period_rows(
            "/futures/data/takerlongshortRatio",
            symbol,
            start_ms=start_ms,
            end_ms=decision_ms,
        )
        oi_raw = self._period_rows(
            "/futures/data/openInterestHist",
            symbol,
            start_ms=start_ms,
            end_ms=decision_ms,
        )
        crowd_raw = self._period_rows(
            "/futures/data/topLongShortPositionRatio",
            symbol,
            start_ms=start_ms,
            end_ms=decision_ms,
        )

        premium_raw = self._get_json(
            "/fapi/v1/premiumIndexKlines",
            {
                "symbol": symbol,
                "interval": "5m",
                "startTime": start_ms,
                "endTime": decision_ms,
                "limit": 50,
            },
        )
        funding_raw = self._get_json(
            "/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": candle_open_ms - 24 * 60 * MINUTE_MS,
                "endTime": candle_open_ms,
                "limit": 20,
            },
        )

        taker_rows = [
            {
                "timestamp": int(row["timestamp"]),
                "taker_ratio": float(row["buySellRatio"]),
            }
            for row in taker_raw
            if "timestamp" in row and "buySellRatio" in row
        ]
        oi_rows = [
            {
                "timestamp": int(row["timestamp"]),
                "oi": float(row["sumOpenInterest"]),
            }
            for row in oi_raw
            if "timestamp" in row and "sumOpenInterest" in row
        ]

        crowding_rows: list[dict[str, object]] = []
        for row in crowd_raw:
            if "timestamp" not in row:
                continue
            if "longShortRatio" in row:
                ratio = float(row["longShortRatio"])
            elif "longAccount" in row and "shortAccount" in row:
                short_share = float(row["shortAccount"])
                ratio = float(row["longAccount"]) / short_share if short_share else 0.0
            else:
                continue
            crowding_rows.append({"timestamp": int(row["timestamp"]), "top_ratio": ratio})

        premium_rows: list[dict[str, object]] = []
        if isinstance(premium_raw, list):
            for row in premium_raw:
                if isinstance(row, list) and len(row) >= 5:
                    premium_rows.append(
                        {
                            "timestamp": int(row[0]),
                            "close": float(row[4]),
                        }
                    )

        funding_rows: list[dict[str, object]] = []
        if isinstance(funding_raw, list):
            for row in funding_raw:
                if isinstance(row, dict) and "fundingTime" in row and "fundingRate" in row:
                    funding_rows.append(
                        {
                            "timestamp": int(row["fundingTime"]),
                            "rate": float(row["fundingRate"]),
                        }
                    )

        btc_bars = self.one_minute_bars(
            "BTCUSDT",
            decision_ms=decision_ms,
            limit=30,
        )
        return MarketFilterInputs(
            taker_rows=taker_rows,
            oi_rows=oi_rows,
            crowding_rows=crowding_rows,
            premium_rows=premium_rows,
            funding_rows=funding_rows,
            btc_bars=btc_bars,
        )

    def market_filter(
        self,
        symbol: str,
        candle_open_ms: int,
        direction: int,
    ) -> MarketFilterResult:
        try:
            inputs = self.market_inputs(symbol, candle_open_ms)
            return evaluate_market_filters(candle_open_ms, direction, inputs)
        except (BinancePublicDataError, KeyError, TypeError, ValueError) as exc:
            return MarketFilterResult(
                passed=False,
                checks={},
                reason=f"market_data_error:{type(exc).__name__}",
            )
