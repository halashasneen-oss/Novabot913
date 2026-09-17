# Strategy 913 — Canonical Trade Audit

## Status

**COMPLETED / PASS**

This document records the execution, causality, and accounting audit of the frozen
Strategy 913 canonical baseline.

- Canonical strategy SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Authoritative audit workflow run: `35255064158`
- Audit runner HEAD: `8720112dc938a6090a765517fb19030e57636d0d`
- Strategy parameters modified: **false**
- Strategy logic modified: **false**

The audit tooling is external to the frozen strategy logic. Its purpose is to replay
Strategy 913 and independently verify trade timing, causal data availability, and
accounting invariants.

## Coverage

All five accepted historical baseline windows were audited:

| Window | Trades audited | Result |
| --- | ---: | --- |
| 2026-04-01 to 2026-05-01 UTC | 2 | PASS |
| 2026-05-01 to 2026-06-01 UTC | 9 | PASS |
| 2026-06-17 to 2026-07-17 UTC | 10 | PASS |
| 2026-07-17 to 2026-08-17 UTC | 3 | PASS |
| 2026-08-17 to 2026-09-16 UTC | 22 | PASS |
| **Total** | **46** | **46/46 audited** |

The audit program exits non-zero if any per-trade or portfolio-level check fails.
All five matrix jobs completed successfully, so no audited trade or portfolio check
failed in the authoritative run.

## Causal timing checks

For every executed trade, the audit verifies:

1. A matching approved causal market-filter decision exists before entry.
2. The actual decision boundary equals the next 1-minute entry boundary.
3. Taker data was from a fully completed 5-minute interval at decision time.
4. Premium data was from a fully completed 5-minute interval at decision time.
5. The frozen legacy OI/Crowding metric row was not from the future.
6. The Funding observation was not from the future.
7. The BTC confirmation bar used by the decision was completed before entry.

The audit preserves the canonical timing policy: only Premium and Taker use the
authorized 5-minute completion correction. It does not apply that completion shift
to OI, Crowding, or Funding.

## Entry and exit timing checks

The audit independently reconstructs the engine timing convention:

- An approved signal is entered on the **next 1-minute bar open**.
- Entry price must equal that bar open after the frozen slippage adjustment.
- STOP and TRAIL_STOP exits use their existing same-bar execution semantics.
- EARLY_FAILURE and FOLLOWTHROUGH_15M decisions schedule execution on the **next
  minute open**.
- `held_minutes`, reconstructed entry timestamp, and exit timestamp must agree with
  the applicable exit convention.

An early audit-tool version treated every exit type as if it shared the STOP/TRAIL
`held_minutes` convention. That produced false timing mismatches for next-open exits.
The audit tool was corrected to reflect the engine's actual EARLY_FAILURE and
FOLLOWTHROUGH_15M next-open behavior. This was an audit-tool correction only; no
Strategy 913 rule, parameter, trade, or historical result was changed.

## Accounting checks

For every trade, the audit independently verifies:

- executed entry price against minute open plus frozen slippage;
- notional from recorded margin and leverage;
- entry and exit fees from the frozen fee rate;
- gross directional PnL;
- net PnL after both sides of fees.

At the portfolio/window level, the audit verifies:

- trade count;
- wins and losses;
- total fees;
- final equity reconstructed from the trade ledger;
- return percentage from start and final equity;
- profit factor reconstructed from winning and losing net PnL;
- zero reported liquidations.

## Authoritative artifacts

All artifacts below were produced by workflow run `35255064158` at audit-runner HEAD
`8720112dc938a6090a765517fb19030e57636d0d`.

| Window | Artifact ID | SHA-256 digest |
| --- | ---: | --- |
| Apr 2026 | `10511574876` | `7da3eac46f68f69efd8ba6a18749d9d61ad0b587a8591188853ad45d2fea52bf` |
| May 2026 | `10512364597` | `ef9f07d99522b3e56fccb17c1a1371ced119a031b18e1abc8189294f1bb58ddb` |
| Jun 17–Jul 17 2026 | `10512354701` | `da34311d4fc50d246e2ecccb9de26074ba3c316d0084e54478fa0ace50fea16c` |
| Jul 17–Aug 17 2026 | `10511894563` | `2fb00b57e33df7b0d90cc0caf7d165709c71533f88d0614458aecea0f874d027` |
| Aug 17–Sep 16 2026 | `10512966070` | `3227cf4dca1076f1b898b2b33d82f1394e79138e7395035b25c9975d72a256c8` |

## Audit conclusion

Across the 46 trades in the five accepted historical windows, the canonical Strategy
913 replay passed the implemented causality, entry/exit timing, slippage, fee, trade
PnL, final-equity, return, profit-factor, win/loss, and liquidation consistency
checks.

This audit does **not** change the previously observed profitability or drawdown
results. It verifies the internal timing and accounting consistency of those results.
The frozen strategy remains the canonical SHA above.
