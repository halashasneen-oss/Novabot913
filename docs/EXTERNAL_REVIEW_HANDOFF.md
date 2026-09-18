# Strategy 913 — External Review Handoff

Date: 2026-09-18

## Purpose

This branch is a review bundle for independent technical review. It is not an adopted production
upgrade and it does not redefine the canonical Strategy 913.

## Canonical causal reference

The frozen corrected causal Strategy 913 identity remains:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Authorized temporal corrections are limited to the documented Premium and Taker timing fixes.
No other timing rule should be assumed corrected without independent evidence.

## What this review branch contains

- Canonical/reference Strategy 913 source material under `reference/strategy913/`
- Temporal causality implementation and audits under `src/novabot913/`
- Unit tests under `tests/`
- Corrected-reference, trade-audit, execution-risk and reproducibility runners under `scripts/`
- Strategy 913 ablation runner
- Early Profit Protection parameter sweep runner and workflow
- Early Profit Protection out-of-sample runner, workflow and report
- Continuous YTD protect-only runner and workflow
- Pinned market-data snapshots used for the current-day YTD replay under `research_data/`
- Historical and audit documentation under `docs/`

## Current YTD research result

Continuous account simulation from 2026-01-01 through 2026-09-18, starting from 100 USDT:

Baseline Strategy 913:
- Final equity: 0.1805 USDT
- Return: -99.82%
- Max drawdown: 99.92%
- Profit factor: 0.128
- Win rate: 31.76%
- Trades: 85

Strategy 913 + Early Profit Protection:
- Final equity: 0.9443 USDT
- Return: -99.06%
- Max drawdown: 99.79%
- Profit factor: 0.144
- Win rate: 37.21%
- Trades: 86

Protection rule used in that YTD replay:
- Activate at +0.5R MFE
- First protective stop: -0.25R on the next minute
- Break-even at +1.0R MFE with +0.15% lock
- Canonical progressive trailing from +1.5R onward
- No staged entry
- No re-entry

GitHub Actions YTD run:
`35350989120`

## Important review questions

Independent reviewers should verify, rather than assume:

1. Causality of every market/metrics observation at decision time.
2. Position sizing and compounding behavior under a continuous account.
3. Fee, spread, slippage and funding accounting.
4. Stop and trailing execution semantics, especially same-bar / next-minute behavior.
5. Risk concentration and whether a single trade can consume an excessive fraction of equity.
6. Whether the early-failure and 15-minute follow-through logic is appropriate across regimes.
7. Whether entry quality/regime selection is the dominant failure mode before profit protection activates.
8. Whether the backtest engine remains internally consistent as equity approaches very small values.
9. Reproducibility of the YTD result from the pinned data policy and current snapshot.
10. Whether any copied research harness diverges from the canonical Strategy 913 behavior.

## Status

- Research only.
- Live trading remains disabled by default.
- No Early Profit Protection candidate is adopted.
- Staged entry is not adopted.
- Re-entry is not adopted.
- Canonical Strategy 913 remains unchanged.
