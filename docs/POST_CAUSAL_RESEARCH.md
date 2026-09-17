# Strategy 913 Post-Causal Research

## Frozen baseline

The causal-correction phase is frozen at commit:

`158fb1c45a0cf88d549e301913f43435c337d7a1`

Its authorized timing corrections are limited to completed 5-minute Premium and Taker
observations. OI, Crowding, Funding, thresholds, universe, sizing, leverage, entry logic,
exit logic, 15m follow-through, early failure, and trailing remain outside the correction
scope unless separate evidence justifies a future change.

## Phase 1: diagnostics only

This phase does not optimize Strategy 913. It measures the corrected trades by symbol,
direction, exit reason, score, and UTC entry hour for each historical window and for the
combined sample.

The diagnostic report is designed to identify hypotheses for later research without
silently changing the strategy or contaminating the corrected causal reference.

Any later optimization must be tested as a separate research phase against this frozen
baseline.
