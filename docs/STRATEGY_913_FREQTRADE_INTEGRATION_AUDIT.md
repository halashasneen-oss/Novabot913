# Strategy 913 Freqtrade Integration Audit

## Status

- Result: `PASS`
- Scope: live causal producer and Freqtrade execution integration
- Canonical Strategy 913 SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Integration validation SHA: `a45e17946c13b88006f9bac10e0a98aef89215e3`
- CI run: `35391631488` — success
- Freqtrade smoke run: `35391631555` — success
- Tested Freqtrade stable release: `2026.8`
- Strategy parameters modified: `false`

The integration is implemented on a separate branch and does not modify the frozen files under
`reference/strategy913/` or replace the canonical Strategy 913 identity.

## Implemented architecture

The integration separates decision logic from exchange execution.

1. `BinanceUSDMPublicClient` reads public USD-M futures market data.
2. `Strategy913LiveEngine` reproduces the frozen causal setup and management state machine.
3. The engine writes deterministic `Strategy913Intent` records to an append-only JSONL bus.
4. `Strategy913Executor` consumes those intents inside Freqtrade.
5. Freqtrade handles orders, exchange lifecycle, persistence, wallet constraints and fills.
6. Actual entry/exit fills are written back through `JsonlExecutionBus`.
7. Strategy 913 resumes position management from the actual fill price, fill time and leverage.

## Frozen strategy behavior carried into the live core

- Fixed canonical 10-symbol universe.
- Reconstructed 4H Score >= 5 setup.
- 1m/3m/5m three-vote micro confirmation.
- Volume and micro-breakout confirmation.
- 15-minute causal retest window.
- 0.15% retest approach threshold.
- 0.25% retest failure threshold.
- 1-minute directional relaunch.
- Taker, OI, Premium, Funding, Crowding and BTC confirmation filters.
- Premium and Taker use the two authorized completed-interval timing corrections.
- OI, Crowding and Funding keep their frozen timing semantics.
- Score 5 sizing: 35% wallet margin at 50x requested leverage.
- Score >= 6 sizing: 50% wallet margin at 75x requested leverage.
- Frozen initial stop geometry.
- 0.15% break-even lock.
- Progressive 1.0R / 0.75R / 0.50R / 0.35R trailing distances.
- First-10-minute Early Failure rule.
- Minute-15 Follow-through rule.
- 720-minute maximum hold.
- No fixed take-profit.

## Safety and causality controls

- Intent decisions must equal the close of the represented completed 1-minute candle.
- Position IDs are deterministic and isolate stop/exit events to the intended position.
- Intent and fill buses are append-only and idempotent.
- Producer state is persisted atomically.
- First startup bootstraps at the latest completed minute instead of inventing retroactive entries.
- Restart catch-up processes available missing candles in order.
- A missing candle gap is rejected rather than silently skipped.
- Entry leverage is not silently reduced: if Freqtrade/Binance cannot provide the frozen requested
  leverage, the entry stake callback rejects that entry.
- The example Freqtrade configuration remains `dry_run: true` with empty credentials.

## Verification completed

Normal CI on integration SHA `a45e17946c13b88006f9bac10e0a98aef89215e3` passed:

- Ruff lint.
- Ruff format.
- Pytest.
- Strategy-core parity tests against vendored frozen reference helpers.
- Temporal Premium/Taker boundary tests.
- Execution-bus idempotency tests.
- Fill-feedback tests.
- Restart-state persistence tests.
- Early Failure and progressive trailing live-state tests.
- Freqtrade configuration contract tests.

The separate Freqtrade compatibility workflow also passed:

- installed `freqtrade==2026.8`;
- validated the dry-run configuration contract;
- loaded `Strategy913Executor` through Freqtrade's own strategy discovery command.

## Remaining final validation stage

Historical replay/simulation is deliberately performed after all engineering and compatibility
work is green. That final stage must measure the integrated behavior against the frozen canonical
reference without changing the canonical strategy or tuning parameters to the validation window.

A historical result is evidence about that tested sample only. It is not a guarantee of live
profitability or identical exchange microstructure.
