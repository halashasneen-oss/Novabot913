# Strategy 913 temporal audit and parity status

## Repository and change control

- STARTING_SHA: NONE — repository was empty before initialization.
- Initial repository commit: `d53e54735120024ccc7e9a331dbc622e83e3e8bf`.
- Target: `halashasneen-oss/Novabot913`.
- Work branch: `build/strategy-913`.
- `halashasneen-oss/NovaArb` is reference-only and must not be modified.
- No merge to `main` without explicit authorization.
- Live trading remains disabled by default.

## Pinned source provenance

Hybrid reference commit:
`66a46ab69fef6f82a7eb87afd3175b797a1effc4`

Reference files:

- `tests/tmp_hybrid_zec_score7plus_60d.py`
- `tests/tmp_zec_extreme_reference.py`
- `tests/tmp_ten_score7plus_flow.py`
- `tests/tmp_hybrid_archive_market.py`

Follow-through reference commit:
`2f3378c9e05e15a22ae7afb1e0763835c4c6a6a1`

Follow-through file:
`tests/tmp_followthrough15_may2026_top10.py`

## Time convention

The historical loop labels each 1-minute bar by its open timestamp. Close-based decisions become available only after that minute finishes. A bar labeled 12:01 therefore supports a completed-candle decision at 12:02, not at 12:01.

## Authorized temporal correction 1 — Premium

The legacy archive code selects Premium 5m rows by the candle open timestamp but consumes the final close. This can expose the close of a still-open 5-minute candle.

Corrected causal rule:

```text
premium_open_ms + 300_000 <= actual_decision_ms
```

The latest row satisfying the rule is selected. No still-open Premium interval is visible.

## Authorized temporal correction 2 — Taker

The archived Taker observation timestamp marks the beginning of the 5-minute interval represented by the ratio. Direct evidence from BTCUSDT on 2026-05-01 showed that archived values at 09:10 and 10:55 match the following 5-minute trading intervals rather than the preceding completed intervals.

Corrected causal rule:

```text
taker_interval_start_ms + 300_000 <= actual_decision_ms
```

The latest row satisfying the rule is selected. No still-open Taker interval is visible.

This correction applies only to Taker. It must not be copied to OI, Crowding, Funding, or any other metric without independent temporal evidence and explicit authorization.

## Completed temporal audit of unchanged components

No third independently proven lookahead defect was established in the inspected reference behavior.

### 3m and 5m price candles

The reference completed-index convention derives the latest usable bucket from the actual completed 1m decision boundary. A still-open 3m or 5m bucket is excluded. A bucket becomes eligible exactly at its close boundary. Regression tests lock both sides of those boundaries.

### 4H setup timing

A reconstructed 4H signal is armed only at:

```text
4h_bar_open + 4 hours
```

The current 4H candle therefore must be complete before its setup can enter the armed state. No earlier arm path was found in the inspected source.

### BTC confirmation

The frozen BTC confirmation reads the current completed 1m close and the close ten 1m bars earlier. The candidate entry remains next-open. No future BTC candle access was found.

### OI

The reference chooses the current OI row at or before the existing cutoff and the comparison row at or before the 30-minute-old cutoff. There is no independent evidence in this audit that OI shares Taker's forward-interval availability convention. Its timing is therefore preserved unchanged.

### Crowding

Crowding uses the archived top-trader ratio from the same legacy point-row selection. No independent interval proof was established for Crowding. Its timing and its intentionally non-directional rule remain unchanged.

### Funding

Funding uses `calc_time` and selects a funding observation at or before the existing cutoff. No future timestamp selection was found in the inspected implementation. Historic publication latency was not independently reconstructed, so no timing shift is authorized.

### Trailing and 15-minute Follow-through

Source inspection confirms the causal sequencing required by the frozen strategy: the active stop is checked before same-minute MFE updates, newly calculated trailing stops become active from the next minute, the Follow-through test occurs at completed held minute 15, and a failed test schedules exit for the next 1m open. Full trade-level regression is still required in the corrected historical reruns.

## Frozen components not authorized for change

The temporal corrections above do not authorize changes to:

- OI timing
- Crowding timing or its non-directional rule
- Funding timing
- thresholds
- fixed 10-symbol universe
- sizing or leverage
- entry/exit sequencing
- Early Failure
- Trailing
- 15-minute Follow-through
- Take Profit policy
- Equity Lock policy

## Historical result classification

The previously reported results are classified as **Legacy Lookahead-Contaminated Reference** until the fully corrected causal reference is established.

| Window UTC | Start | Legacy final | Trades | Wins | Losses |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-05-01 to 2026-06-01 | 100 | 176.9935 | 6 | 3 | 3 |
| 2026-08-17 to 2026-09-16 | 100 | ~584.84 | 21 | 13 | 8 |

These numbers are not optimization targets. The corrected implementation must not tune parameters to reproduce them.

## Current audit status

The pre-reference temporal audit is complete for the inspected Strategy 913 paths.

Authorized behavior changes remain limited to exactly two items:

1. Premium 5m values only after full interval completion.
2. Taker 5m values only after full interval completion.

All other inspected timing behavior remains frozen. Regression coverage now includes the unchanged 3m/5m completion boundaries, 4H arm boundary, BTC ten-bar indexing, and point-in-time selection semantics used to preserve OI/Crowding/Funding behavior.

## Next acceptance work

1. Build the corrected reference adapter with only the two authorized temporal corrections.
2. Re-run May 2026 and Aug–Sep 2026.
3. Export all trades and identify the first divergence from the legacy reference.
4. Freeze the resulting corrected causal trade sequence as the new parity target.
5. Continue M1–M10 only after causal parity is established.
