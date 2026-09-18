# Strategy 913 — Early Profit Protection OOS Report

Date: 2026-09-18

## Scope

This report evaluates the Early Profit Protection candidates selected after the five-window
protection sweep. It is research-only and does not modify or adopt a new canonical Strategy 913.

Canonical strategy SHA:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Selection windows used by the sweep:

- 2026-04-01 to 2026-05-01
- 2026-05-01 to 2026-06-01
- 2026-06-17 to 2026-07-17
- 2026-07-17 to 2026-08-17
- 2026-08-17 to 2026-09-16

Holdout windows in this report:

- January 2026
- February 2026
- March 2026

The holdout windows were not used to choose the three protection candidates.

## Candidates

| Candidate | Protect trigger | Initial protected stop | Break-even trigger |
|---|---:|---:|---:|
| Robust | 0.50R | -0.10R | 0.80R |
| High return | 0.50R | -0.20R | 1.20R |
| Original protection | 0.50R | -0.25R | 1.00R |

## Selection-window context

Across the five selection windows, the Robust candidate had:

- Average independent-window return: +47.74%
- Median independent-window return: +85.67%
- Maximum drawdown across a window: 48.66%
- Aggregate win rate across 47 trades: 55.32%
- Largest winning trade share of total positive PnL: 13.67%

The High Return candidate had:

- Average independent-window return: +50.68%
- Median independent-window return: +73.21%
- Maximum drawdown across a window: 58.25%
- Aggregate win rate across 47 trades: 48.94%
- Largest winning trade share of total positive PnL: 11.99%

The Original Protection candidate had:

- Average independent-window return: +43.40%
- Median independent-window return: +68.95%
- Maximum drawdown across a window: 59.27%
- Aggregate win rate across 47 trades: 48.94%
- Largest winning trade share of total positive PnL: 13.01%

## Out-of-sample results

### January 2026

| Variant | Return | Max DD | Profit Factor | Win Rate | Trades |
|---|---:|---:|---:|---:|---:|
| Strategy 913 baseline | -82.65% | 83.25% | 0.127 | 9.09% | 11 |
| Robust | -82.65% | 83.25% | 0.127 | 9.09% | 11 |
| High return | -82.65% | 83.25% | 0.127 | 9.09% | 11 |
| Original protection | -82.65% | 83.25% | 0.127 | 9.09% | 11 |

The protection logic did not change the January result. The losing trades generally exited
before the tested protection levels could improve the outcome.

### February 2026

| Variant | Return | Max DD | Profit Factor | Win Rate | Trades |
|---|---:|---:|---:|---:|---:|
| Strategy 913 baseline | -91.77% | 91.77% | 0.064 | 21.43% | 14 |
| Robust | -90.20% | 90.20% | 0.051 | 28.57% | 14 |
| High return | -89.74% | 89.74% | 0.080 | 21.43% | 14 |
| Original protection | -90.20% | 90.20% | 0.051 | 28.57% | 14 |

Protection improved the final result slightly, but all candidates remained severely negative.

### March 2026

| Variant | Return | Max DD | Profit Factor | Win Rate | Trades |
|---|---:|---:|---:|---:|---:|
| Strategy 913 baseline | -62.43% | 64.84% | 0.027 | 16.67% | 6 |
| Robust | -62.43% | 64.84% | 0.027 | 16.67% | 6 |
| High return | -62.43% | 64.84% | 0.027 | 16.67% | 6 |
| Original protection | -62.43% | 64.84% | 0.027 | 16.67% | 6 |

The protection logic again produced no material change.

## Decision

Early Profit Protection remains a useful trade-management improvement inside the April to
September selection windows, but this holdout test does not support adopting it as a sufficient
upgrade to Strategy 913.

The January to March holdout shows that Strategy 913 can enter strongly adverse regimes where
most losses occur before the protection thresholds become relevant. Therefore:

- No protection candidate is adopted.
- Canonical Strategy 913 remains unchanged.
- Staged entry remains disabled.
- Re-entry remains disabled.
- No new threshold, filter, universe, sizing, leverage, entry, or exit change is authorized by
  this report.

## Next research question

The next justified step is diagnosis, not another parameter sweep.

For the January to March losing trades, determine causally:

1. Which exit reason dominated each loss.
2. The maximum favorable excursion reached before exit.
3. Whether the trade ever reached 0.4R, 0.5R, 0.8R, 1.0R, or 1.2R.
4. Whether the failure was entry-quality/regime-related rather than post-entry protection.
5. Whether any existing Strategy 913 signal component has a documented causal difference
   between the failing holdout regime and the stronger April to September regime.

No change should be made from that diagnosis unless an independent causal mechanism is shown.

GitHub Actions OOS run: `35348002157`
