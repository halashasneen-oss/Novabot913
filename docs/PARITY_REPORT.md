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

Premium and Taker completion policies are implemented as isolated causal selection helpers with boundary, midnight, missing-data, and future-value-invariance regression tests.

The rest of the temporal audit must continue before declaring the corrected causal reference complete. If another independently proven lookahead defect is found, implementation must stop before changing that component and request explicit authorization.

## Next acceptance work

1. Complete temporal audit of OI, Crowding, Funding, 3m/5m/4H bars, BTC confirmation, trailing, and follow-through.
2. Build the corrected reference adapter with only the two authorized temporal corrections.
3. Re-run May 2026 and Aug–Sep 2026.
4. Export all trades and identify the first divergence from the legacy reference.
5. Freeze the resulting corrected causal trade sequence as the new parity target.
6. Continue M1–M10 only after causal parity is established.
