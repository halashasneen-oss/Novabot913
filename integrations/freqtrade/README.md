# Strategy 913 + Freqtrade Integration

Status: causal live producer + Freqtrade dry-run execution integration.

The integration is isolated from the frozen canonical implementation. It does not edit
`reference/strategy913/`, canonical documentation, or audited historical runners.

Canonical Strategy 913 remains pinned to:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

## Architecture

    Binance USD-M public market data
                  |
                  v
    Strategy913LiveEngine
    4H setup -> micro confirmation -> retest -> market filters
                  |
                  | Strategy913Intent (append-only JSONL)
                  v
    Strategy913Executor (Freqtrade IStrategy)
                  |
                  v
    Freqtrade order / wallet / persistence layer
                  |
                  v
    Binance USD-M Futures
                  |
                  | actual entry/exit fills
                  v
    JsonlExecutionBus
                  |
                  +----------------------> Strategy913LiveEngine

The two-way bridge is intentional. Strategy 913 owns the trading decision and frozen risk rules.
Freqtrade owns exchange execution. Actual Freqtrade fill price, time and leverage are returned to
the Strategy 913 engine before live position management continues.

## Frozen behavior preserved

The live core carries the canonical 10-symbol universe:

- DOGEUSDT
- BTCUSDT
- XRPUSDT
- TIAUSDT
- 1000PEPEUSDT
- DOTUSDT
- UNIUSDT
- SUIUSDT
- WIFUSDT
- ETCUSDT

It preserves the frozen 4H setup, Score, 1m/3m/5m micro confirmation, retest, market filters,
Early Failure, 15-minute Follow-through, progressive trailing and 12-hour maximum hold.

Authorized temporal corrections remain limited to Premium and Taker 5-minute observations being
available only after the represented five-minute interval is complete. OI, Crowding and Funding
retain the frozen timing semantics.

Sizing and requested leverage remain unchanged:

- Score 5: 35% of wallet margin, 50x.
- Score >= 6: 50% of wallet margin, 75x.

If Freqtrade/Binance cannot provide the exact requested leverage, the adapter rejects the entry
instead of silently changing Strategy 913. If the full requested stake does not fit the available
stake constraint, the entry is also rejected.

No fixed take-profit is added.

## Causal and restart protections

- Every one-minute intent requires `decision_ms == candle_open_ms + 60000`.
- Position IDs are deterministic and scope all stop/exit events to the intended trade.
- Intent and execution buses are append-only and idempotent.
- The first producer start bootstraps at the latest closed minute without retroactive entries.
- After a restart, the producer replays available missing closed candles in order.
- A missing temporal gap is rejected instead of silently skipped.
- Live state is written atomically to JSON after processing.
- Freqtrade returns actual fills to the live engine through the execution bus.
- The example Freqtrade configuration keeps `dry_run: true` and contains no credentials.

## State files

Defaults can be overridden with environment variables:

    NOVABOT913_INTENT_FILE=user_data/data/strategy913_intents.jsonl
    NOVABOT913_EXECUTION_FILE=user_data/data/strategy913_execution.jsonl
    NOVABOT913_STATE_FILE=user_data/data/strategy913_live_state.json

The producer and Freqtrade process must point to the same intent and execution files.

## Dry-run setup

Install Novabot913:

    python -m pip install -e .

Install the tested Freqtrade stable release:

    python -m pip install "freqtrade==2026.8"

Start the signal producer:

    python scripts/run_live_signal_producer.py

In another process, start Freqtrade:

    freqtrade trade \
      --config integrations/freqtrade/config.dryrun.example.json \
      --strategy-path integrations/freqtrade/user_data/strategies \
      --strategy Strategy913Executor

Do not change `dry_run` to `false` during integration validation.

## Validation boundary

Passing unit tests and loading successfully in Freqtrade verifies the software contract, callback
compatibility and frozen-rule parity checks implemented in this branch. It does not prove live
profitability and does not make historical candle execution identical to exchange microstructure.

Historical simulation/parity replay is intentionally kept as the final validation stage after the
engineering integration is green.
