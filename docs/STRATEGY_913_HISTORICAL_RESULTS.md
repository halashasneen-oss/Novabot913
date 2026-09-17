# Strategy 913 — Canonical Historical Results

This report consolidates the accepted historical results for the frozen causal Strategy 913.

## Canonical strategy identity

- Canonical strategy SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Strategy parameters modified for this report: **No**
- Authorized temporal corrections only:
  - Premium 5m final close is usable only after the represented interval is complete.
  - Taker 5m observation is usable only after the represented interval is complete.
- OI timing: unchanged.
- Crowding timing: unchanged.
- Funding timing: unchanged.
- Universe, thresholds, sizing, leverage, entry/exit, 15m follow-through, early failure, and trailing logic: unchanged.

## Important interpretation rule

Every historical window below is an independent run starting from **100 USDT**. Final equity and returns must therefore be read per window. They are not a single continuously compounded equity curve, and the returns must not be added together as though capital rolled from one window into the next.

## Consolidated results

| Window | Start Equity | Final Equity | Return | Trades | Wins | Losses | Win Rate | Profit Factor | Fees | Max Drawdown | Liquidations | Exit Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-04-01 → 2026-05-01 | 100.00 | 58.7301 | -41.27% | 2 | 0 | 2 | 0.00% | 0.000 | 6.7668 | 41.66% | 0 | EARLY_FAILURE 1; STOP 1 |
| 2026-05-01 → 2026-06-01 | 100.00 | 125.5690 | +25.57% | 9 | 3 | 6 | 33.33% | 1.270 | 28.1534 | 47.10% | 0 | EARLY_FAILURE 4; FOLLOWTHROUGH_15M 1; STOP 1; TRAIL_STOP 3 |
| 2026-06-17 → 2026-07-17 | 100.00 | 111.4614 | +11.46% | 10 | 5 | 5 | 50.00% | 1.136 | 27.9978 | 73.13% | 0 | EARLY_FAILURE 1; FOLLOWTHROUGH_15M 1; STOP 3; TRAIL_STOP 5 |
| 2026-07-17 → 2026-08-17 | 100.00 | 64.3664 | -35.63% | 3 | 0 | 3 | 0.00% | 0.000 | 8.4617 | 37.18% | 0 | EARLY_FAILURE 1; FOLLOWTHROUGH_15M 1; STOP 1 |
| 2026-08-17 → 2026-09-16 | 100.00 | 126.6958 | +26.70% | 22 | 11 | 11 | 50.00% | 1.057 | 134.7059 | 67.35% | 0 | EARLY_FAILURE 3; FOLLOWTHROUGH_15M 4; STOP 6; TRAIL_STOP 9 |

## Aggregate counts across the independent windows

These values are safe to aggregate because they are counts or directly reported costs, not compounded equity statistics.

- Total trades: **46**
- Total wins: **19**
- Total losses: **27**
- Pooled trade win rate: **41.30%**
- Total reported fees: **206.0856 USDT**
- Total liquidations: **0**
- Exit counts:
  - EARLY_FAILURE: **10**
  - FOLLOWTHROUGH_15M: **7**
  - STOP: **12**
  - TRAIL_STOP: **17**
- Highest observed max drawdown in an individual window: **73.13%**

No combined Profit Factor or compounded return is reported here because the accepted windows were executed as separate 100 USDT runs and the necessary cross-window gross-profit/gross-loss accounting is not represented as one continuous portfolio path.

## Source records

### Corrected causal reference

Covers:
- 2026-05-01 → 2026-06-01
- 2026-08-17 → 2026-09-16

Source:
- Workflow run: `35240653406`
- Artifact: `corrected-causal-reference`
- Artifact ID: `10506375028`
- Digest: `sha256:77ace4c097aecbcf3a6a550d34427ad521b0553692f4e4266eaf80474e57c814`
- Head SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`

### June–July canonical baseline

Covers:
- 2026-06-17 → 2026-07-17

This result is the **baseline** side of the archived LONG-only OOS run. The rejected LONG-only variant is not included in this report and is not part of Strategy 913.

Source:
- Workflow run: `35249530808`
- Artifact: `post-causal-long-only-oos`
- Artifact ID: `10508438212`
- Digest: `sha256:5e6eb1fde7b2b6a182e4feac20b57ebbcec54479166b2e9b4d95cdc2e0234520`
- Baseline causal SHA embedded in result: `158fb1c45a0cf88d549e301913f43435c337d7a1`

### Additional canonical historical windows

Covers:
- 2026-04-01 → 2026-05-01
- 2026-07-17 → 2026-08-17

Source:
- Workflow run: `35252319404`
- Artifact: `additional-canonical-windows`
- Artifact ID: `10511146946`
- Digest: `sha256:45dee4d24f91174bea57667046ec4a2e66c29e6505832eb58ab7f6fcf767117a`
- Canonical strategy SHA embedded in result: `158fb1c45a0cf88d549e301913f43435c337d7a1`

## Coverage note

The accepted windows currently cover:
- April 1–May 1
- May 1–June 1
- June 17–July 17
- July 17–August 17
- August 17–September 16

There is currently an uncovered historical gap from **June 1 to June 17, 2026**. This report does not infer results for that gap.

## Status

This document is a reporting consolidation only. It does not alter Strategy 913, does not adopt any research variant, and does not change the canonical strategy SHA.
