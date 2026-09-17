# Strategy 913 Execution & Risk Constraint Audit

## Status

- Step: `7`
- Result: `PASS`
- Scope: execution and risk-constraint verification only
- Canonical Strategy 913 SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Audit implementation HEAD: `d6e656e1f2a5f661b771b8ed25ad003a23a11ea9`
- Audit workflow run: `35257145466`
- CI run on audit HEAD: `35257145432`
- Strategy parameters modified: `false`

The audit files added after the canonical commit are verification infrastructure only. They do not replace or modify the frozen Strategy 913 behavior.

## Frozen rules verified

The audit asserts the exact simulator rules currently frozen for Strategy 913:

- Fee: `0.0005` of notional on each side.
- Slippage: `0.0002` on each side.
- Score 5 sizing: `35%` of current wallet, `50x` leverage.
- Score >= 6 sizing: `50%` of current wallet, `75x` leverage.
- Required margin must fit the currently available wallet after already-active position margin.
- Notional equals `margin * leverage`.
- Initial stop risk:
  - `50x`: `0.825%` price distance.
  - `75x`: `0.60%` price distance.
- Initial stop is inside the modeled liquidation boundary.
- Break-even lock: `0.15%`.
- Progressive trailing distance:
  - MFE >= `1.5R`: `1.00R`.
  - MFE >= `2.5R`: `0.75R`.
  - MFE >= `4R`: `0.50R`.
  - MFE >= `6R`: `0.35R`.
- A newly calculated trailing stop is applied on the following minute and may only tighten, never loosen.
- Early Failure is evaluated only during the first 10 managed minutes and requires two consecutive wrong-side closes with failed 1-minute directional confirmation; the resulting exit is on the next 1-minute open.
- 15-minute Follow-through is evaluated from the completed minute-15 bar. It schedules a next-open exit only when both directional close move is below `0.30%` and cumulative favorable excursion is below `0.77%`.
- Maximum holding period: `720` minutes / `12h`.
- Runtime exit reasons are restricted to the frozen simulator exit set.

## Dynamic replay verification

The audit does not only inspect constants. For every executed trade it reconstructs the account path and replays management minute by minute from entry through exit.

For each entry it verifies:

- wallet value used by sizing;
- expected margin fraction;
- leverage selected from score;
- available margin after active positions;
- notional calculation;
- immediate entry-fee deduction.

For each managed trade it independently replays:

- initial stop and modeled liquidation geometry;
- pending trailing-stop application on the next minute;
- monotonic stop tightening;
- liquidation-gap and stop precedence;
- STOP versus TRAIL_STOP classification;
- Early Failure state and next-open execution;
- minute-15 Follow-through state and next-open execution;
- 12-hour maximum-hold rule;
- exit timestamp, exit reason, exit price and slippage.

The account replay also verifies the reconstructed final wallet, observed maximum concurrent positions, and that no positions remain open at the end of each test window.

## Five-window result

| Window | Trades | Final equity | Return | Observed max concurrent | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 2026-04-01 -> 2026-05-01 | 2 | 58.7301 | -41.27% | 1 | PASS |
| 2026-05-01 -> 2026-06-01 | 9 | 125.5690 | +25.57% | 1 | PASS |
| 2026-06-17 -> 2026-07-17 | 10 | 111.4614 | +11.46% | 1 | PASS |
| 2026-07-17 -> 2026-08-17 | 3 | 64.3664 | -35.63% | 1 | PASS |
| 2026-08-17 -> 2026-09-16 | 22 | 126.6958 | +26.70% | 1 | PASS |
| **Total audited trades** | **46** | - | - | - | **PASS** |

All 46 trade-path replays passed. No liquidation occurred in the five audited windows.

Combined audited exit counts:

- `TRAIL_STOP`: 17
- `STOP`: 12
- `EARLY_FAILURE`: 10
- `FOLLOWTHROUGH_15M`: 7

No unauthorized exit reason appeared.

## Artifacts

All five artifacts below were produced by workflow run `35257145466` from audit HEAD `d6e656e1f2a5f661b771b8ed25ad003a23a11ea9`.

| Window | Artifact ID | Digest |
| --- | ---: | --- |
| apr_2026 | `10513407915` | `sha256:15cf7ee0bb8b576bc411488269b9de4f4161f53c532d2d04314e8962afd31047` |
| may_2026 | `10513707965` | `sha256:c67a5ed772da4d5eba11f6aecf8d28c00e860c960fe7577b3c58e545c1a7ec8a` |
| jun_jul_2026 | `10512998493` | `sha256:4f0227b608d7e716b71f34714b96805c729f9efe623bb2d68589219bd4466feb` |
| jul_aug_2026 | `10513707527` | `sha256:51835468b8b1638007e1b3bc1eadc67462db2060d4127eccaf2b2ce4824c2144` |
| aug_sep_2026 | `10513398861` | `sha256:f2d7f15f3616ca6546158dabc39a607f2d76e6ebdaf04250583da74c1cde775b` |

## Frozen risk-exposure observation

The execution is internally consistent, but the frozen sizing/leverage profile is aggressive by design. This is an observation from the existing formulas, **not** an execution defect and **not** a parameter change.

Approximate equity impact of one ordinary initial-stop loss, assuming no worse gap and including the configured entry/exit fees and slippage, is:

- Score 5: about **16.5% of equity**.
  - Margin fraction: `35%`.
  - Leverage: `50x`.
  - Notional-to-equity: about `17.5x`.
  - Initial stop price distance: `0.825%`.
- Score >= 6: about **27.0% of equity**.
  - Margin fraction: `50%`.
  - Leverage: `75x`.
  - Notional-to-equity: about `37.5x`.
  - Initial stop price distance: `0.60%`.

Therefore a visually small price stop does not mean a small account-level loss. The leverage and margin fractions magnify the stop into a large equity event, which is consistent with the very large historical drawdowns seen in the canonical windows.

This milestone does not alter those values. Any future change to sizing, leverage, or account-level risk must be treated as a separate isolated hypothesis and validated independently.

## Interpretation

Step 7 verifies that the frozen Strategy 913 simulator executes its documented sizing, leverage, margin, stop, trailing, Early Failure and 15-minute Follow-through rules consistently for all 46 trades in the five audited windows.

This is not a claim that the simulator exactly reproduces live exchange microstructure. The underlying research simulator still uses simplified margin/liquidation and order-book execution assumptions, and minute OHLC management uses adverse stop precedence. Step 7 therefore closes the internal execution/risk-consistency question for the frozen simulator; it does not remove live-execution/model-risk limitations.
