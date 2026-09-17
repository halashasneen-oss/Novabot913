# Strategy 913 Post-Causal Research Archive

## Authoritative frozen baseline

The only authoritative Strategy 913 causal reference remains:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Its authorized timing corrections are limited to completed 5-minute Premium and Taker
observations. OI, Crowding, Funding, thresholds, universe, sizing, leverage, entry logic,
exit logic, 15m follow-through, early failure, and trailing are unchanged.

## Research status

Post-causal optimization research is CLOSED. Research artifacts are retained only for
traceability and reproducibility. They are not part of the authoritative Strategy 913
baseline and must not be interpreted as adopted strategy changes.

### LONG-only experiment — REJECTED

Hypothesis tested: reject otherwise-approved SHORT decisions and retain LONG decisions.

In-sample / diagnostic-window results were favorable:

- May 2026: corrected baseline +25.57% vs LONG-only +40.45%.
- Aug 17-Sep 16, 2026: corrected baseline +26.70% vs LONG-only +137.56%.

The hypothesis failed the non-overlapping out-of-sample validation window
Jun 17-Jul 17, 2026:

- Corrected baseline: +11.46%, final equity 111.4614, PF 1.136.
- LONG-only: -9.13%, final equity 90.8684, PF 0.828.

Therefore LONG-only is rejected as a universal Strategy 913 rule and is NOT adopted.

## Isolation rules

- The frozen causal baseline above is the only Strategy 913 reference.
- Research modules, runners, reports, and workflows are archival/reproducibility material.
- Research workflows are manual-only and must not execute automatically on Strategy 913
  branch updates.
- No research result may modify Strategy 913 thresholds, universe, sizing, leverage,
  entry/exit logic, 15m follow-through, early failure, trailing, or the frozen timing rules.
- Any future strategy modification requires an explicit user instruction; archived research
  does not authorize a change.
