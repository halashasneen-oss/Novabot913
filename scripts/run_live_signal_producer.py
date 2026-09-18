from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from novabot913.binance_public import BinanceUSDMPublicClient
from novabot913.execution_bus import JsonlExecutionBus
from novabot913.live_engine import JsonLiveStateStore
from novabot913.signal_bus import JsonlIntentBus
from novabot913.strategy_core import CANONICAL_UNIVERSE, MINUTE_MS

DEFAULT_DATA_DIR = Path("user_data/data")


def _path_from_env(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else fallback


def _latest_closed_open(now_ms: int) -> int:
    return (now_ms // MINUTE_MS) * MINUTE_MS - MINUTE_MS


def _apply_new_execution_events(engine, execution_bus: JsonlExecutionBus) -> int:
    applied = 0
    for event in execution_bus.read_all():
        before = len(engine.processed_event_ids)
        engine.apply_execution_event(event)
        if len(engine.processed_event_ids) > before:
            applied += 1
    return applied


def run_cycle(
    *,
    state_store: JsonLiveStateStore,
    intent_bus: JsonlIntentBus,
    execution_bus: JsonlExecutionBus,
    client: BinanceUSDMPublicClient,
    now_ms: int,
) -> dict[str, int]:
    engine = state_store.load()
    fills = _apply_new_execution_events(engine, execution_bus)
    latest_open = _latest_closed_open(now_ms)
    emitted = 0
    processed = 0
    bootstrapped = 0

    for symbol in CANONICAL_UNIVERSE:
        decision_ms = latest_open + MINUTE_MS
        one_minute = client.one_minute_bars(
            symbol,
            decision_ms=decision_ms,
            limit=500,
        )
        four_hour = client.four_hour_bars(
            symbol,
            decision_ms=decision_ms,
            limit=40,
        )
        if not one_minute or one_minute[-1][0] != latest_open:
            raise RuntimeError(f"latest closed 1m candle is unavailable for {symbol}")

        state = engine.states[symbol]
        if state.last_processed_ms < 0:
            state.last_processed_ms = latest_open
            bootstrapped += 1
            continue

        missing = [
            bar
            for bar in one_minute
            if state.last_processed_ms < bar[0] <= latest_open
        ]
        if missing and missing[0][0] != state.last_processed_ms + MINUTE_MS:
            raise RuntimeError(
                f"catch-up history gap for {symbol}; refusing to skip causal candles"
            )

        index_by_ts = {bar[0]: index for index, bar in enumerate(one_minute)}
        for bar in missing:
            index = index_by_ts[bar[0]]
            history = one_minute[: index + 1]
            intents = engine.process_closed_candle(
                symbol,
                history,
                four_hour,
                client.market_filter,
            )
            processed += 1
            for intent in intents:
                if intent_bus.append_once(intent):
                    emitted += 1
            state_store.save(engine)

    state_store.save(engine)
    return {
        "fills_applied": fills,
        "candles_processed": processed,
        "intents_emitted": emitted,
        "symbols_bootstrapped": bootstrapped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the causal live Strategy 913 signal producer."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")

    intent_path = _path_from_env(
        "NOVABOT913_INTENT_FILE",
        DEFAULT_DATA_DIR / "strategy913_intents.jsonl",
    )
    execution_path = _path_from_env(
        "NOVABOT913_EXECUTION_FILE",
        DEFAULT_DATA_DIR / "strategy913_execution.jsonl",
    )
    state_path = _path_from_env(
        "NOVABOT913_STATE_FILE",
        DEFAULT_DATA_DIR / "strategy913_live_state.json",
    )

    state_store = JsonLiveStateStore(state_path)
    intent_bus = JsonlIntentBus(intent_path)
    execution_bus = JsonlExecutionBus(execution_path)
    client = BinanceUSDMPublicClient()

    while True:
        result = run_cycle(
            state_store=state_store,
            intent_bus=intent_bus,
            execution_bus=execution_bus,
            client=client,
            now_ms=time.time_ns() // 1_000_000,
        )
        print(
            "LIVE_913 "
            f"fills={result['fills_applied']} "
            f"candles={result['candles_processed']} "
            f"intents={result['intents_emitted']} "
            f"bootstrapped={result['symbols_bootstrapped']}",
            flush=True,
        )
        if args.once:
            break
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
