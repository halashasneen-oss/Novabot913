from __future__ import annotations

# ruff: noqa: E501

import inspect
import textwrap
from typing import Any

from novabot913.strategy913r1 import (
    LEVERAGE_CAP,
    CircuitBreaker,
    RollingPerformanceTracker,
    position_size,
    regime_filter,
    sizing_basis,
    stop_distance,
)

FOLLOWTHROUGH_MINUTE = 15
MIN_CLOSE_MOVE = 0.0030
MIN_MFE = 0.0077


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise RuntimeError(f"R1 patch anchor missing: {label}")
    return source.replace(old, new, 1)


def install_r1_patch(h: Any):
    source = textwrap.dedent(inspect.getsource(h._run_hybrid))

    source = _replace_once(
        source,
        '    liquidations = 0\n',
        '    liquidations = 0\n'
        '    performance_tracker = RollingPerformanceTracker()\n'
        '    circuit = CircuitBreaker()\n'
        '    risk_audit = []\n'
        '    regime_audit = []\n'
        '    circuit_audit = []\n'
        '    high_water_mark = 100.0\n'
        '    last_marked_equity = 100.0\n',
        "R1 state",
    )

    source = _replace_once(
        source,
        '        "early_failure_exits": 0,\n',
        '        "early_failure_exits": 0,\n'
        '        "followthrough_15m_exits": 0,\n'
        '        "regime_rejections": 0,\n'
        '        "throttled_entries": 0,\n',
        "R1 stats",
    )

    close_anchor = (
        '            "exit_ts": ts,\n'
        '        })\n'
        '\n'
        '    for ts in timestamps:\n'
    )
    close_replacement = (
        '            "exit_ts": ts,\n'
        '        })\n'
        '        risk_amount = max(float(pos.get("risk_amount", 0.0)), 1e-12)\n'
        '        r_multiple = net / risk_amount\n'
        '        trades[-1]["risk_fraction"] = pos.get("risk_fraction")\n'
        '        trades[-1]["risk_amount"] = risk_amount\n'
        '        trades[-1]["r_multiple"] = r_multiple\n'
        '        performance_tracker.record(pos["score"], r_multiple)\n'
        '        circuit.on_trade_close(ts, net, circuit_audit)\n'
        '\n'
        '    for ts in timestamps:\n'
    )
    source = _replace_once(
        source,
        close_anchor,
        close_replacement,
        "trade close tracker",
    )

    old_pending_exit = (
        '            if bar is not None:\n'
        '                close_position(symbol, bar[1], "EARLY_FAILURE", ts)\n'
        '                stats["early_failure_exits"] += 1\n'
    )
    new_pending_exit = (
        '            if bar is not None:\n'
        '                reason = positions[symbol].pop('
        '"pending_early_reason", "EARLY_FAILURE")\n'
        '                close_position(symbol, bar[1], reason, ts)\n'
        '                if reason == "EARLY_FAILURE":\n'
        '                    stats["early_failure_exits"] += 1\n'
        '                else:\n'
        '                    stats["followthrough_15m_exits"] += 1\n'
    )
    source = _replace_once(
        source,
        old_pending_exit,
        new_pending_exit,
        "followthrough exit",
    )

    old_entry = '''            margin_fraction, leverage = _params(item["score"])
            required = wallet * margin_fraction
            used = sum(p["margin"] for p in positions.values())
            available = max(0.0, wallet - used)
            if required <= 0 or required > available + 1e-9:
                stats["margin_rejections"] += 1
                pending_entries.pop(symbol, None)
                continue
            bar = maps[symbol].get(ts)
            if bar is None:
                pending_entries.pop(symbol, None)
                continue
            direction = item["direction"]
            entry = bar[1] * (1.0 + direction * SLIPPAGE)
            notional = required * leverage
            entry_fee = notional * FEE
            wallet -= entry_fee
            fees += entry_fee
            risk = _stop_risk(leverage)
            liqdist = max(0.003, 1.0 / leverage - 0.005)
'''
    new_entry = '''            used = sum(p["margin"] for p in positions.values())
            available = max(0.0, wallet - used)
            bar = maps[symbol].get(ts)
            if bar is None:
                pending_entries.pop(symbol, None)
                continue
            direction = item["direction"]
            entry = bar[1] * (1.0 + direction * SLIPPAGE)
            risk = stop_distance(symbol, entry, direction, ts, datasets[symbol])
            base_risk_fraction = performance_tracker.risk_fraction(item["score"])
            effective_risk_fraction = circuit.effective_risk_fraction(
                base_risk_fraction
            )
            current_equity = max(0.0, min(wallet, last_marked_equity))
            basis = sizing_basis(current_equity, high_water_mark)
            required, leverage, notional = position_size(
                basis,
                effective_risk_fraction,
                risk,
                LEVERAGE_CAP,
            )
            if leverage > 0 and required > available:
                notional = min(notional, available * leverage)
                required = notional / leverage
            if required <= 0 or notional <= 0 or required > available + 1e-9:
                stats["margin_rejections"] += 1
                pending_entries.pop(symbol, None)
                continue
            entry_fee = notional * FEE
            wallet -= entry_fee
            fees += entry_fee
            risk_amount = notional * risk
            if circuit.throttled:
                stats["throttled_entries"] += 1
            risk_audit.append(
                {
                    "timestamp": ts,
                    "symbol": symbol,
                    "score": item["score"],
                    "current_equity": current_equity,
                    "high_water_mark": high_water_mark,
                    "sizing_basis": basis,
                    "base_risk_fraction": base_risk_fraction,
                    "effective_risk_fraction": effective_risk_fraction,
                    "stop_distance": risk,
                    "margin": required,
                    "leverage": leverage,
                    "notional": notional,
                    "risk_amount": risk_amount,
                    "throttled": circuit.throttled,
                }
            )
            liqdist = max(0.003, 1.0 / leverage - 0.005)
'''
    source = _replace_once(
        source,
        old_entry,
        new_entry,
        "risk-based entry sizing",
    )

    source = _replace_once(
        source,
        '                "pending_stop": None,\n',
        '                "pending_stop": None,\n'
        '                "followthrough_mfe": 0.0,\n'
        '                "risk_fraction": effective_risk_fraction,\n'
        '                "risk_amount": risk_amount,\n'
        '                "sizing_basis": basis,\n',
        "R1 position fields",
    )

    liquidation_anchor = (
        '                liquidations += 1\n'
        '                positions.pop(symbol)\n'
        '                continue\n'
    )
    liquidation_replacement = (
        '                risk_amount = max(float(pos.get("risk_amount", 0.0)), 1e-12)\n'
        '                r_multiple = trades[-1]["net"] / risk_amount\n'
        '                trades[-1]["risk_fraction"] = pos.get("risk_fraction")\n'
        '                trades[-1]["risk_amount"] = risk_amount\n'
        '                trades[-1]["r_multiple"] = r_multiple\n'
        '                performance_tracker.record(pos["score"], r_multiple)\n'
        '                circuit.on_trade_close(ts, trades[-1]["net"], circuit_audit)\n'
        '                liquidations += 1\n'
        '                positions.pop(symbol)\n'
        '                continue\n'
    )
    source = _replace_once(
        source,
        liquidation_anchor,
        liquidation_replacement,
        "liquidation tracker",
    )

    old_manage = (
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    new_manage = (
        '            if direction == 1:\n'
        '                current_follow_mfe = high / pos["entry"] - 1.0\n'
        '            else:\n'
        '                current_follow_mfe = pos["entry"] / low - 1.0\n'
        '            pos["followthrough_mfe"] = max(\n'
        '                pos["followthrough_mfe"], current_follow_mfe\n'
        '            )\n'
        '            if pos["held_minutes"] == FOLLOWTHROUGH_MINUTE:\n'
        '                close_move = direction * (close / pos["entry"] - 1.0)\n'
        '                if close_move < MIN_CLOSE_MOVE and pos["followthrough_mfe"] < MIN_MFE:\n'
        '                    pos["pending_early_reason"] = "FOLLOWTHROUGH_15M"\n'
        '                    pending_early[symbol] = ts + 60_000\n'
        '            if direction == 1:\n'
        '                pos["best_close"] = max(pos["best_close"], close)\n'
    )
    source = _replace_once(
        source,
        old_manage,
        new_manage,
        "followthrough management",
    )

    old_filter = '''            if not filters.get("pass"):
                stats["external_rejections"] += 1
                reason = filters.get("reason") or ",".join(k for k, v in filters.get("checks", {}).items() if not v)
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                continue
            stats["external_approvals"] += 1
'''
    new_filter = '''            if not filters.get("pass"):
                stats["external_rejections"] += 1
                reason = filters.get("reason") or ",".join(
                    k for k, value in filters.get("checks", {}).items() if not value
                )
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                continue
            regime = regime_filter(symbol, ts, datasets[symbol])
            regime_audit.append(regime)
            if not regime.get("pass"):
                stats["external_rejections"] += 1
                stats["regime_rejections"] += 1
                reject_reasons["regime_reject"] = (
                    reject_reasons.get("regime_reject", 0) + 1
                )
                continue
            stats["external_approvals"] += 1
'''
    source = _replace_once(
        source,
        old_filter,
        new_filter,
        "causal regime gate",
    )

    old_mark = '''        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        if marked <= 0 or wallet <= 0:
            break
'''
    new_mark = '''        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak)
        last_marked_equity = marked
        high_water_mark = max(high_water_mark, marked)
        circuit.on_equity_mark(ts, marked, circuit_audit)
        if marked <= 0 or wallet <= 0:
            break
'''
    source = _replace_once(
        source,
        old_mark,
        new_mark,
        "HWM and circuit update",
    )

    source = _replace_once(
        source,
        '        "trades_detail": trades,\n',
        '        "trades_detail": trades,\n'
        '        "risk_audit": risk_audit,\n'
        '        "regime_audit": regime_audit,\n'
        '        "circuit_audit": circuit_audit,\n'
        '        "r1_high_water_mark": high_water_mark,\n'
        '        "r1_loss_streak": circuit.loss_streak,\n'
        '        "r1_throttled": circuit.throttled,\n',
        "R1 result audit",
    )

    namespace = dict(h.__dict__)
    namespace.update(
        {
            "FOLLOWTHROUGH_MINUTE": FOLLOWTHROUGH_MINUTE,
            "MIN_CLOSE_MOVE": MIN_CLOSE_MOVE,
            "MIN_MFE": MIN_MFE,
            "LEVERAGE_CAP": LEVERAGE_CAP,
            "RollingPerformanceTracker": RollingPerformanceTracker,
            "CircuitBreaker": CircuitBreaker,
            "stop_distance": stop_distance,
            "position_size": position_size,
            "sizing_basis": sizing_basis,
            "regime_filter": regime_filter,
        }
    )
    exec(compile(source, "<strategy913_r1>", "exec"), namespace)
    return namespace["_run_hybrid"]
