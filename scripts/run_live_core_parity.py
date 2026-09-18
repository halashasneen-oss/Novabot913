from __future__ import annotations

import argparse
import json
import math
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from run_canonical_trade_audit import NEXT_OPEN_EXITS, _prepare_named_window
from run_corrected_reference import (
    _causal_filter_factory,
    _load_modules,
    _run_engine,
    _stage_reference_files,
    _summary,
)

from novabot913.execution_bus import ExecutionEvent
from novabot913.live_engine import Strategy913LiveEngine
from novabot913.signal_bus import (
    Strategy913Intent,
    canonical_leverage,
    canonical_margin_fraction,
    canonical_stop_price_risk,
)
from novabot913.strategy_core import (
    CANONICAL_UNIVERSE,
    MINUTE_MS,
    MarketFilterInputs,
    MarketFilterResult,
    aggregate,
    evaluate_market_filters,
)

CANONICAL_STRATEGY_SHA = "158fb1c45a0cf88d549e301913f43435c337d7a1"


def _direction_name(direction: int) -> str:
    return "LONG" if direction == 1 else "SHORT"


def _close_enough(left: float, right: float, tolerance: float = 1e-8) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=tolerance)


def _canonical_trade_view(trade: dict[str, Any]) -> dict[str, Any]:
    held = int(trade["held_minutes"])
    exit_ts = int(trade["exit_ts"])
    elapsed = held if trade["reason"] in NEXT_OPEN_EXITS else held - 1
    return {
        "symbol": trade["symbol"],
        "direction": trade["direction"],
        "score": int(trade["score"]),
        "entry_ts": exit_ts - elapsed * MINUTE_MS,
        "exit_ts": exit_ts,
        "reason": trade["reason"],
        "held_minutes": held,
        "entry": float(trade["entry"]),
        "exit": float(trade["exit"]),
        "margin": float(trade["margin"]),
        "leverage": float(trade["leverage"]),
        "net": float(trade["net"]),
    }


def _replay_trade_view(trade: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "symbol",
        "direction",
        "score",
        "entry_ts",
        "exit_ts",
        "reason",
        "held_minutes",
        "entry",
        "exit",
        "margin",
        "leverage",
        "net",
    )
    return {key: trade[key] for key in keys}


def _first_trade_divergence(
    canonical: list[dict[str, Any]],
    replay: list[dict[str, Any]],
) -> dict[str, Any] | None:
    left = [_canonical_trade_view(item) for item in canonical]
    right = [_replay_trade_view(item) for item in replay]
    exact_fields = (
        "symbol",
        "direction",
        "score",
        "entry_ts",
        "exit_ts",
        "reason",
        "held_minutes",
        "leverage",
    )
    float_fields = ("entry", "exit", "margin", "net")

    for index in range(max(len(left), len(right))):
        expected = left[index] if index < len(left) else None
        actual = right[index] if index < len(right) else None
        if expected is None or actual is None:
            return {"index": index + 1, "canonical": expected, "replay": actual}

        failed = [field for field in exact_fields if expected[field] != actual[field]]
        failed.extend(
            field
            for field in float_fields
            if not _close_enough(
                float(expected[field]),
                float(actual[field]),
                tolerance=1e-7,
            )
        )
        if failed:
            return {
                "index": index + 1,
                "failed_fields": failed,
                "canonical": expected,
                "replay": actual,
            }
    return None


def _core_filter_factory(
    h: Any,
    market_ref: Any,
    reference_filter: Any,
    btc_bars: list[tuple],
    btc_index: dict[int, int],
    mismatches: list[dict[str, Any]],
):
    def market_filter(
        symbol: str,
        timestamp: int,
        direction: int,
    ) -> MarketFilterResult:
        day = datetime.fromtimestamp(timestamp / 1000, tz=UTC).date()
        metrics = list(h._metrics_day(symbol, day - timedelta(days=1)))
        metrics += list(h._metrics_day(symbol, day))
        metrics.sort(key=lambda item: item["timestamp"])

        premium_rows = list(market_ref._premium_day(symbol, day - timedelta(days=1)))
        premium_rows += list(market_ref._premium_day(symbol, day))
        premium_rows.sort(key=lambda item: item["timestamp"])

        funding = market_ref._funding_before(symbol, timestamp)
        funding_rows = [funding] if funding is not None else []

        core = evaluate_market_filters(
            timestamp,
            direction,
            MarketFilterInputs(
                taker_rows=metrics,
                oi_rows=metrics,
                crowding_rows=metrics,
                premium_rows=premium_rows,
                funding_rows=funding_rows,
                btc_bars=btc_bars,
            ),
        )
        reference = reference_filter(
            symbol,
            timestamp,
            direction,
            btc_bars,
            btc_index,
        )
        reference_pass = bool(reference.get("pass"))
        reference_checks = reference.get("checks") or {}
        filter_differs = core.passed != reference_pass or dict(core.checks) != reference_checks
        if filter_differs and len(mismatches) < 50:
            mismatches.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "direction": direction,
                    "core_pass": core.passed,
                    "reference_pass": reference_pass,
                    "core_checks": dict(core.checks),
                    "reference_checks": reference_checks,
                    "core_reason": core.reason,
                    "reference_reason": reference.get("reason"),
                }
            )
        return core

    return market_filter


def _replay(
    raw: dict[str, list[tuple]],
    h: Any,
    market_filter: Any,
) -> dict[str, Any]:
    if tuple(raw) != CANONICAL_UNIVERSE:
        raise RuntimeError("historical universe does not match frozen Strategy 913 universe")

    fee_rate = float(h.FEE)
    slippage = float(h.SLIPPAGE)
    start_ms = int(h.TEST_START_MS)
    end_ms = int(h.TEST_END_MS)

    maps = {symbol: {int(bar[0]): bar for bar in bars} for symbol, bars in raw.items()}
    indices = {
        symbol: {int(bar[0]): index for index, bar in enumerate(bars)}
        for symbol, bars in raw.items()
    }
    four_hour = {symbol: aggregate(bars, 240) for symbol, bars in raw.items()}
    timestamps = sorted(ts for ts in maps["BTCUSDT"] if start_ms <= ts < end_ms)

    engine = Strategy913LiveEngine()
    for state in engine.states.values():
        state.last_processed_ms = start_ms - MINUTE_MS

    scheduled_entries: dict[int, list[Strategy913Intent]] = defaultdict(list)
    scheduled_exits: dict[int, list[Strategy913Intent]] = defaultdict(list)
    scheduled_stops: dict[int, list[Strategy913Intent]] = defaultdict(list)

    wallet = 100.0
    positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    fees = 0.0
    peak = 100.0
    trough = 100.0
    max_dd = 0.0
    max_concurrent = 0
    liquidations = 0
    entry_rejections = 0
    emitted = {"enter": 0, "exit": 0, "stop_update": 0}

    def exit_position(
        symbol: str,
        raw_exit: float,
        reason: str,
        timestamp: int,
        held_minutes: int,
        mfe: float,
        *,
        slip: bool = True,
    ) -> None:
        nonlocal wallet, fees
        pos = positions.pop(symbol)
        exit_price = raw_exit * (1.0 - pos["direction"] * slippage) if slip else raw_exit
        move = pos["direction"] * (exit_price / pos["entry"] - 1.0)
        gross = pos["notional"] * move
        exit_fee = pos["notional"] * fee_rate
        wallet += gross - exit_fee
        fees += exit_fee
        net = gross - pos["entry_fee"] - exit_fee
        trades.append(
            {
                "symbol": symbol,
                "direction": _direction_name(pos["direction"]),
                "score": pos["score"],
                "leverage": pos["leverage"],
                "margin": pos["margin"],
                "entry": pos["entry"],
                "exit": exit_price,
                "net": net,
                "reason": reason,
                "held_minutes": held_minutes,
                "mfe_pct": mfe * 100.0,
                "breakout_level": pos["breakout_level"],
                "entry_ts": pos["entry_ts"],
                "exit_ts": timestamp,
            }
        )
        engine.apply_execution_event(
            ExecutionEvent(
                event_id=f"replay_exit:{symbol}:{timestamp}:{reason}",
                symbol=symbol,
                event="exit_fill",
                side=pos["side"],
                timestamp_ms=timestamp,
                position_id=pos["position_id"],
                price=exit_price,
                leverage=pos["leverage"],
                reason=reason,
            )
        )

    for timestamp in timestamps:
        for intent in scheduled_stops.pop(timestamp, []):
            pos = positions.get(intent.symbol)
            if pos is None or pos["position_id"] != intent.position_id or intent.stop_price is None:
                continue
            if pos["direction"] == 1:
                pos["stop"] = max(pos["stop"], intent.stop_price)
            else:
                pos["stop"] = min(pos["stop"], intent.stop_price)

        for intent in scheduled_exits.pop(timestamp, []):
            pos = positions.get(intent.symbol)
            if pos is None or pos["position_id"] != intent.position_id:
                continue
            bar = maps[intent.symbol].get(timestamp)
            state_pos = engine.states[intent.symbol].position
            if bar is None or state_pos is None:
                continue
            exit_position(
                intent.symbol,
                float(bar[1]),
                intent.reason or "STRATEGY_913_EXIT",
                timestamp,
                state_pos.held_minutes,
                state_pos.mfe,
            )

        for intent in scheduled_entries.pop(timestamp, []):
            if intent.score is None or intent.breakout_level is None:
                raise RuntimeError("entry replay intent lost required metadata")
            if intent.symbol in positions:
                raise RuntimeError(f"entry replay attempted while {intent.symbol} is active")

            leverage = canonical_leverage(intent.score)
            margin = wallet * canonical_margin_fraction(intent.score)
            used_margin = sum(pos["margin"] for pos in positions.values())
            available = max(0.0, wallet - used_margin)
            if margin > available:
                entry_rejections += 1
                engine.apply_execution_event(
                    ExecutionEvent(
                        event_id=(f"replay_reject:{intent.position_id}:STAKE_ABOVE_MAX"),
                        symbol=intent.symbol,
                        event="entry_rejected",
                        side=intent.side,
                        timestamp_ms=timestamp,
                        position_id=str(intent.position_id),
                        leverage=leverage,
                        score=intent.score,
                        breakout_level=intent.breakout_level,
                        source_candle_open_ms=intent.candle_open_ms,
                        reason="STAKE_ABOVE_MAX",
                    )
                )
                continue

            bar = maps[intent.symbol].get(timestamp)
            if bar is None:
                raise RuntimeError(f"missing entry bar for {intent.symbol} at {timestamp}")
            direction = 1 if intent.side == "long" else -1
            entry = float(bar[1]) * (1.0 + direction * slippage)
            notional = margin * leverage
            entry_fee = notional * fee_rate
            wallet -= entry_fee
            fees += entry_fee
            risk = canonical_stop_price_risk(leverage)
            liquidation_distance = max(0.003, 1.0 / leverage - 0.005)
            position_id = str(intent.position_id)
            positions[intent.symbol] = {
                "position_id": position_id,
                "side": intent.side,
                "direction": direction,
                "score": intent.score,
                "leverage": leverage,
                "margin": margin,
                "notional": notional,
                "entry": entry,
                "entry_fee": entry_fee,
                "entry_ts": timestamp,
                "stop": entry * (1.0 - direction * risk),
                "liq": entry * (1.0 - direction * liquidation_distance),
                "risk": risk,
                "breakout_level": intent.breakout_level,
            }
            engine.apply_execution_event(
                ExecutionEvent(
                    event_id=f"replay_entry:{intent.symbol}:{timestamp}",
                    symbol=intent.symbol,
                    event="entry_fill",
                    side=intent.side,
                    timestamp_ms=timestamp,
                    position_id=position_id,
                    price=entry,
                    leverage=leverage,
                    score=intent.score,
                    breakout_level=intent.breakout_level,
                    source_candle_open_ms=intent.candle_open_ms,
                )
            )
            max_concurrent = max(max_concurrent, len(positions))

        for symbol in list(positions):
            pos = positions.get(symbol)
            state_pos = engine.states[symbol].position
            bar = maps[symbol].get(timestamp)
            if pos is None or state_pos is None or bar is None:
                continue

            opn = float(bar[1])
            high = float(bar[2])
            low = float(bar[3])
            held = state_pos.held_minutes + 1

            liquidation_gap = opn <= pos["liq"] if pos["direction"] == 1 else opn >= pos["liq"]
            if liquidation_gap:
                liquidations += 1
                wallet -= pos["margin"]
                net = -pos["margin"] - pos["entry_fee"]
                trades.append(
                    {
                        "symbol": symbol,
                        "direction": _direction_name(pos["direction"]),
                        "score": pos["score"],
                        "leverage": pos["leverage"],
                        "margin": pos["margin"],
                        "entry": pos["entry"],
                        "exit": opn,
                        "net": net,
                        "reason": "LIQ_GAP",
                        "held_minutes": held,
                        "mfe_pct": state_pos.mfe * 100.0,
                        "breakout_level": pos["breakout_level"],
                        "entry_ts": pos["entry_ts"],
                        "exit_ts": timestamp,
                    }
                )
                positions.pop(symbol)
                engine.apply_execution_event(
                    ExecutionEvent(
                        event_id=f"replay_exit:{symbol}:{timestamp}:LIQ_GAP",
                        symbol=symbol,
                        event="exit_fill",
                        side=pos["side"],
                        timestamp_ms=timestamp,
                        position_id=pos["position_id"],
                        price=opn,
                        leverage=pos["leverage"],
                        reason="LIQ_GAP",
                    )
                )
                continue

            stop_hit = low <= pos["stop"] if pos["direction"] == 1 else high >= pos["stop"]
            if stop_hit:
                raw_exit = min(pos["stop"], opn) if pos["direction"] == 1 else max(pos["stop"], opn)
                reason = "TRAIL_STOP" if state_pos.mfe >= 1.5 * pos["risk"] else "STOP"
                exit_position(
                    symbol,
                    raw_exit,
                    reason,
                    timestamp,
                    held,
                    state_pos.mfe,
                )

        for symbol in CANONICAL_UNIVERSE:
            index = indices[symbol].get(timestamp)
            if index is None:
                raise RuntimeError(f"missing replay candle for {symbol} at {timestamp}")
            history_start = max(0, index - 499)
            history = raw[symbol][history_start : index + 1]
            intents = engine.process_closed_candle(
                symbol,
                history,
                four_hour[symbol],
                market_filter,
            )
            for intent in intents:
                emitted[intent.intent] += 1
                if intent.intent == "enter":
                    scheduled_entries[intent.decision_ms].append(intent)
                elif intent.intent == "exit":
                    scheduled_exits[intent.decision_ms].append(intent)
                else:
                    scheduled_stops[intent.decision_ms].append(intent)

        marked = wallet
        for symbol, pos in positions.items():
            bar = maps[symbol].get(timestamp)
            if bar is None:
                continue
            move = pos["direction"] * (float(bar[4]) / pos["entry"] - 1.0)
            marked += pos["notional"] * move
        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        if marked <= 0 or wallet <= 0:
            break

    if timestamps:
        last_ts = timestamps[-1]
        for symbol in list(positions):
            state_pos = engine.states[symbol].position
            if state_pos is None:
                continue
            bar = maps[symbol][last_ts]
            exit_position(
                symbol,
                float(bar[4]),
                "END",
                last_ts,
                state_pos.held_minutes,
                state_pos.mfe,
            )

    wins = sum(1 for trade in trades if trade["net"] > 0)
    gross_profit = sum(max(0.0, trade["net"]) for trade in trades)
    gross_loss = -sum(min(0.0, trade["net"]) for trade in trades)
    reasons: dict[str, int] = {}
    for trade in trades:
        reason = str(trade["reason"])
        reasons[reason] = reasons.get(reason, 0) + 1

    return {
        "start_equity": 100.0,
        "final_equity": round(wallet, 4),
        "return_pct": round((wallet / 100.0 - 1.0) * 100.0, 2),
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate_pct": (round(100.0 * wins / len(trades), 2) if trades else 0.0),
        "profit_factor": (
            round(gross_profit / gross_loss, 3)
            if gross_loss
            else (math.inf if gross_profit else 0.0)
        ),
        "fees": round(fees, 4),
        "peak_marked_equity": round(peak, 4),
        "trough_marked_equity": round(trough, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "max_concurrent": max_concurrent,
        "liquidations": liquidations,
        "exit_reasons": reasons,
        "entry_rejections": entry_rejections,
        "emitted_intents": emitted,
        "trades_detail": trades,
    }


def _summary_checks(
    canonical: dict[str, Any],
    replay: dict[str, Any],
) -> dict[str, bool]:
    exact = (
        "trades",
        "wins",
        "losses",
        "liquidations",
        "exit_reasons",
    )
    approximate = (
        "final_equity",
        "return_pct",
        "win_rate_pct",
        "profit_factor",
        "fees",
        "peak_marked_equity",
        "trough_marked_equity",
        "max_drawdown_pct",
    )
    checks = {f"{key}_matches": canonical.get(key) == replay.get(key) for key in exact}
    for key in approximate:
        left = canonical.get(key)
        right = replay.get(key)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if math.isinf(float(left)) and math.isinf(float(right)):
                checks[f"{key}_matches"] = True
            else:
                checks[f"{key}_matches"] = _close_enough(
                    float(left),
                    float(right),
                    tolerance=1e-4,
                )
        else:
            checks[f"{key}_matches"] = left == right
    return checks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "window",
        choices=(
            "apr_2026",
            "may_2026",
            "jun_jul_2026",
            "jul_aug_2026",
            "aug_sep_2026",
        ),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix=f"novabot913-live-core-parity-{args.window}-") as tmp:
        directory = Path(tmp)
        _stage_reference_files(directory)
        modules = _load_modules(directory)
        h = modules["tmp_hybrid_zec_score7plus_60d"]
        market_ref = modules["tmp_hybrid_archive_market"]

        run_fn, raw, window = _prepare_named_window(args.window, modules)
        if tuple(raw) != CANONICAL_UNIVERSE:
            raise SystemExit("prepared universe differs from canonical live universe")

        btc_bars = raw["BTCUSDT"]
        btc_index = {int(bar[0]): index for index, bar in enumerate(btc_bars)}
        reference_filter = _causal_filter_factory(h, market_ref)
        canonical_result = _run_engine(
            h,
            run_fn,
            raw,
            reference_filter,
        )
        canonical_summary = _summary(canonical_result)

        filter_mismatches: list[dict[str, Any]] = []
        core_filter = _core_filter_factory(
            h,
            market_ref,
            reference_filter,
            btc_bars,
            btc_index,
            filter_mismatches,
        )
        replay = _replay(raw, h, core_filter)
        checks = _summary_checks(canonical_summary, replay)
        trade_divergence = _first_trade_divergence(
            canonical_result["trades_detail"],
            replay["trades_detail"],
        )

        passed = all(checks.values()) and trade_divergence is None and not filter_mismatches
        report = {
            "phase": "strategy_913_live_core_historical_parity",
            "canonical_strategy_sha": CANONICAL_STRATEGY_SHA,
            "strategy_parameters_modified": False,
            "window_name": args.window,
            "window": window,
            "canonical": canonical_summary,
            "replay": {key: value for key, value in replay.items() if key != "trades_detail"},
            "summary_checks": checks,
            "first_trade_divergence": trade_divergence,
            "filter_mismatch_count": len(filter_mismatches),
            "filter_mismatches": filter_mismatches,
            "canonical_trades": [
                _canonical_trade_view(item) for item in canonical_result["trades_detail"]
            ],
            "replay_trades": [_replay_trade_view(item) for item in replay["trades_detail"]],
            "pass": passed,
        }
        output = Path(f"live_core_parity_{args.window}.json")
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(
            "LIVE_CORE_PARITY=" + json.dumps(report, sort_keys=True),
            flush=True,
        )
        if not passed:
            raise SystemExit("Strategy 913 live-core historical parity failed")


if __name__ == "__main__":
    main()