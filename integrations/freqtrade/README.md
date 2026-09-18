# Strategy 913 + Freqtrade Integration

Status: research / dry-run integration.

This directory adds a separate execution adapter. It does not modify any file under
reference/strategy913, any existing canonical documentation, or any audited runner.

Canonical Strategy 913 remains pinned to:
158fb1c45a0cf88d549e301913f43435c337d7a1

Architecture:

    Strategy 913 causal engine
            |
            | append-only causal intents (JSONL)
            v
    JsonlIntentBus
            |
            v
    Strategy913Executor (Freqtrade IStrategy)
            |
            v
    Freqtrade execution infrastructure
            |
            v
    Binance USD-M Futures

Supported intents are enter, exit and stop_update.

Every one-minute intent is rejected unless decision_ms equals candle_open_ms + 60000.
Entry intents require score and breakout_level. Exit intents require a reason. Stop updates
require an absolute stop_price. The bus also rejects a non-canonical source SHA and duplicate
execution keys.

The adapter preserves the frozen sizing and leverage requests:
- score >= 6: 50% of total wallet margin and 75x requested leverage
- score 5: 35% of total wallet margin and 50x requested leverage

Freqtrade still caps leverage to the exchange maximum. If the full requested margin is not
available, the adapter rejects the entry rather than silently shrinking it. No fixed take-profit
is added.

The example configuration keeps dry_run enabled. Do not add Binance API keys for the first
validation pass.

Typical setup:

    pip install -e .
    export NOVABOT913_INTENT_FILE=/absolute/path/to/strategy913_intents.jsonl
    freqtrade trade \
      --config integrations/freqtrade/config.dryrun.example.json \
      --strategy-path integrations/freqtrade/user_data/strategies \
      --strategy Strategy913Executor

Important boundary:

The execution adapter is implemented, but the current production package still contains only the
causal temporal helpers for Premium and Taker. The frozen Strategy 913 reference is primarily an
audited historical engine. A dedicated live causal intent producer still has to reproduce the
frozen 4H setup -> micro confirmation -> retest -> market filters -> entry/exit state machine and
pass parity tests against the canonical artifact before live trading can be considered.

Keeping the causal decision engine separate from Freqtrade is deliberate: it reduces the chance of
silently changing the already-audited timing semantics while letting Freqtrade focus on exchange
execution, persistence, order lifecycle, wallets and fills.
