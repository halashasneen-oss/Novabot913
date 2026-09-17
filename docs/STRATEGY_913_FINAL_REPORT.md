# Strategy 913 — Final Validation Report

## Step status

- Step: `9`
- Result: `COMPLETED`
- Scope: final consolidation/reporting only
- Canonical Strategy 913 SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Source branch HEAD reviewed before this report: `39ef1dbf431fb14edae0959c2d89fc288335089a`
- Source-HEAD CI run: `35261248541`
- Source-HEAD CI conclusion: `success`
- Strategy parameters modified by Step 9: `false`
- Strategy logic modified by Step 9: `false`

This report consolidates the accepted Strategy 913 evidence produced by the preceding validation steps. It does not optimize, tune, modify, or replace the frozen canonical strategy.

## 1. Canonical identity and change-control boundary

The only authoritative Strategy 913 implementation remains:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Canonical corrected-reference evidence:

- Workflow run: `35240653406`
- Artifact: `corrected-causal-reference`
- Artifact ID: `10506375028`
- Artifact digest: `sha256:77ace4c097aecbcf3a6a550d34427ad521b0553692f4e4266eaf80474e57c814`

A later branch HEAD may contain documentation, audit tooling, tests, or archived research. None of those later commits replace the canonical Strategy 913 behavior unless a new strategy version is explicitly authorized and separately frozen.

The following remain frozen and unchanged:

- OI timing
- Crowding timing and its existing rule
- Funding timing
- thresholds
- fixed universe
- sizing
- leverage
- entry logic
- exit logic
- Early Failure
- 15-minute Follow-through
- trailing

## 2. Causality and temporal integrity

Two proven temporal defects were corrected and only those two corrections are authorized in the canonical reference:

1. Premium 5-minute final-close data is available only after its represented five-minute interval has fully completed.
2. Taker 5-minute data is available only after its represented five-minute interval has fully completed.

Historical 1-minute candles are labeled by open timestamp. A close-based decision therefore becomes available at `open_timestamp + 60 seconds`.

The temporal audit found no independent evidence authorizing the same five-minute completion shift for OI, Crowding, or Funding, so those components remain unchanged.

The completed temporal audit also verified the intended causal sequencing for:

- 3-minute and 5-minute completed price buckets;
- 4-hour setup activation only after the 4-hour candle completes;
- BTC confirmation using completed historical bars;
- next-open entry behavior;
- trailing-stop activation only from the following minute;
- completed minute-15 Follow-through evaluation followed by next-open execution when triggered.

## 3. Accepted canonical historical results

Every window below is an independent test beginning with `100 USDT`. The returns are not a single continuous compounded equity curve and must not be added together as if capital rolled from one window to the next.

| Window UTC | Final equity | Return | Trades | Wins | Losses | Win rate | Profit factor | Fees | Max drawdown | Liquidations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-04-01 -> 2026-05-01 | 58.7301 | -41.27% | 2 | 0 | 2 | 0.00% | 0.000 | 6.7668 | 41.66% | 0 |
| 2026-05-01 -> 2026-06-01 | 125.5690 | +25.57% | 9 | 3 | 6 | 33.33% | 1.270 | 28.1534 | 47.10% | 0 |
| 2026-06-17 -> 2026-07-17 | 111.4614 | +11.46% | 10 | 5 | 5 | 50.00% | 1.136 | 27.9978 | 73.13% | 0 |
| 2026-07-17 -> 2026-08-17 | 64.3664 | -35.63% | 3 | 0 | 3 | 0.00% | 0.000 | 8.4617 | 37.18% | 0 |
| 2026-08-17 -> 2026-09-16 | 126.6958 | +26.70% | 22 | 11 | 11 | 50.00% | 1.057 | 134.7059 | 67.35% | 0 |

Aggregate count/cost observations across the independent windows:

- Total audited trades: `46`
- Wins: `19`
- Losses: `27`
- Pooled trade win rate: `41.30%`
- Total reported fees: `206.0856 USDT`
- Liquidations: `0`
- Exit counts:
  - `TRAIL_STOP`: 17
  - `STOP`: 12
  - `EARLY_FAILURE`: 10
  - `FOLLOWTHROUGH_15M`: 7
- Highest max drawdown observed in one accepted window: `73.13%`

No combined profit factor or compounded return is asserted because these accepted tests were independent portfolio runs.

The accepted historical coverage contains a gap from `2026-06-01` to `2026-06-17`. No result is inferred for that period.

## 4. Trade-level causality and accounting audit

The canonical trade audit completed successfully for all `46/46` trades in the five accepted windows.

Authoritative audit workflow:

- Workflow run: `35255064158`
- Audit-runner HEAD: `8720112dc938a6090a765517fb19030e57636d0d`
- Result: `PASS`

The audit independently verified, per applicable trade:

- existence of a causal approved decision before entry;
- next-1-minute-open entry timing;
- completed Taker and Premium interval availability;
- no future OI, Crowding, Funding, or BTC input use under the frozen rules;
- entry price plus frozen slippage;
- margin, leverage, and notional calculations;
- entry and exit fees;
- gross and net directional PnL;
- exit timing semantics for STOP, TRAIL_STOP, EARLY_FAILURE, and FOLLOWTHROUGH_15M;
- final equity, return, profit factor, wins/losses, fees, and liquidation consistency at portfolio level.

No audited trade or portfolio accounting invariant failed in the authoritative run.

## 5. Execution and risk-constraint audit

Step 7 execution/risk verification completed with `PASS` across all 46 historical trades.

Authoritative workflow:

- Workflow run: `35257145466`
- Audit implementation HEAD: `d6e656e1f2a5f661b771b8ed25ad003a23a11ea9`

Frozen execution/risk rules verified include:

- fee: `0.0005` of notional per side;
- slippage: `0.0002` per side;
- Score 5: `35%` wallet margin at `50x`;
- Score >= 6: `50%` wallet margin at `75x`;
- notional = margin × leverage;
- initial stop distance: `0.825%` at 50x and `0.60%` at 75x;
- break-even lock: `0.15%`;
- progressive trailing thresholds/distances;
- trailing changes activate on the following minute and only tighten;
- Early Failure first-10-minute logic with next-open execution;
- minute-15 Follow-through logic with next-open execution;
- maximum holding time: `720 minutes / 12 hours`;
- no unauthorized exit reason.

Observed maximum concurrent positions in each accepted historical window was `1`.

### Frozen account-risk observation

The simulator is internally consistent, but its frozen leverage/sizing profile is highly aggressive:

- Score 5 ordinary initial-stop loss is approximately `16.5%` of equity after the modeled fees/slippage assumptions.
- Score >= 6 ordinary initial-stop loss is approximately `27.0%` of equity under the same assumptions.

These magnitudes explain why a relatively small stop distance in price terms can create a large account-level drawdown. This is consistent with the historical maximum drawdowns reaching `73.13%`.

This is an observation, not a parameter change.

## 6. Reproducibility

Step 8 established deterministic reproducibility for all five accepted historical windows.

Authoritative workflow:

- Workflow run: `35258824800`
- Reproducibility implementation HEAD: `41c381b27f39958d50f175c64360c9feb4298369`
- Result: `PASS`

Each window was executed twice in separate Python processes, for ten independent executions total. Every paired run matched exactly on:

- canonical strategy SHA;
- window;
- raw input-data SHA-256;
- complete result SHA-256;
- summary object;
- complete result object, including trades and pipeline statistics;
- recursive comparison with `first_difference = null`.

Result hashes:

| Window | Result SHA-256 |
| --- | --- |
| Apr 2026 | `789f9c10644f999b97b145f8a49c62be167235e6b04869ca27d44240a5cfda0c` |
| May 2026 | `6eb8cda88d366c2037dd3c763a4b383c6fac7cee432ab4f04f5bceb497f164e3` |
| Jun 17–Jul 17 2026 | `6ae704a30829299c4937fe5134625ca1dc36d3864559e2a39b9dd2749e5619b9` |
| Jul 17–Aug 17 2026 | `9c39229b566cf7ce386067e8bf42c62e805c57850b903199c24d80dfc3d7e1a2` |
| Aug 17–Sep 16 2026 | `0a805e913954a5a8a401dc962e112f7764e8e0d48e613a97c88f3d1f3acb7736` |

Therefore, in the controlled historical environment, the same frozen strategy and the same hashed input data produced bit-for-bit-equivalent serialized results on repeated isolated execution.

## 7. Archived research is not Strategy 913

Post-causal optimization research is closed and excluded from the canonical baseline.

The LONG-only hypothesis was tested separately. It improved two diagnostic/in-sample windows but failed the non-overlapping Jun 17–Jul 17 out-of-sample validation:

- canonical baseline: `+11.46%`, final equity `111.4614`, PF `1.136`;
- LONG-only: `-9.13%`, final equity `90.8684`, PF `0.828`.

The LONG-only variant is therefore `REJECTED / NOT ADOPTED` and must not be interpreted as Strategy 913 behavior.

No archived research result authorizes any strategy modification.

## 8. What has been established

The available evidence supports the following statements about the frozen historical simulator:

1. The two proven Premium/Taker lookahead defects were corrected causally.
2. No additional timing change was authorized without independent evidence.
3. The accepted historical results are internally consistent with the frozen strategy rules.
4. All 46 accepted trades passed the implemented causality, timing, and accounting audit.
5. All 46 accepted trade paths passed the execution and risk-rule replay audit.
6. The five accepted historical windows are deterministically reproducible when the frozen code and identical hashed inputs are used.
7. The strategy's historical profitability is not uniform: three accepted windows were profitable and two were materially negative.
8. Account-level drawdown risk is high under the frozen sizing/leverage profile.

## 9. What has not been established

This validation does not establish that:

- Strategy 913 will be profitable in future markets;
- live fills will equal historical simulated fills;
- exchange microstructure, spread, latency, liquidation mechanics, or order-book impact are modeled perfectly;
- the strategy is safe for unrestricted live capital;
- the uncovered June 1–17 historical gap has any particular performance;
- the five accepted windows constitute a statistically large sample.

The simulator uses simplified market/execution assumptions, including modeled fees/slippage, simplified margin/liquidation behavior, and minute-OHLC management semantics. Those are model-risk limitations rather than detected internal-consistency defects.

## 10. Final Step-9 conclusion

Strategy 913 now has a documented canonical causal identity, accepted historical performance record, complete 46-trade causality/accounting audit, complete execution/risk-rule audit, and deterministic five-window reproducibility evidence.

The validation evidence is internally consistent and reproducible, but it also records substantial performance instability and very high drawdowns. Step 9 therefore closes the **final reporting/consolidation** work without changing the strategy and without converting historical validation into a claim of future profitability or live-execution equivalence.

Step 10 final freeze/release handling is intentionally not performed by this report.

## Evidence index

Primary repository records:

- `docs/STRATEGY_913_CANONICAL_REFERENCE.md`
- `docs/PARITY_REPORT.md`
- `docs/STRATEGY_913_HISTORICAL_RESULTS.md`
- `docs/STRATEGY_913_TRADE_AUDIT.md`
- `docs/STRATEGY_913_EXECUTION_RISK_AUDIT.md`
- `docs/STRATEGY_913_REPRODUCIBILITY_AUDIT.md`
- `docs/POST_CAUSAL_RESEARCH.md`

Primary workflow runs:

- Corrected canonical reference: `35240653406`
- Canonical trade audit: `35255064158`
- Execution/risk audit: `35257145466`
- Reproducibility audit: `35258824800`
