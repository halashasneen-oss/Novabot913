# Strategy 913 Canonical Reference

## Canonical status

This document identifies the one approved Strategy 913 causal reference.

- Strategy: `Strategy 913`
- Status: `CANONICAL / FROZEN`
- Canonical commit SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Canonical commit message: `fix: format causal runner and isolate Premium cache`
- Corrected-reference workflow run: `35240653406`
- Workflow conclusion: `success`
- Artifact name: `corrected-causal-reference`
- Artifact ID: `10506375028`
- Artifact digest: `sha256:77ace4c097aecbcf3a6a550d34427ad521b0553692f4e4266eaf80474e57c814`

The canonical identity is the exact commit SHA above together with the successful corrected-reference artifact and digest above. A later branch HEAD does not replace the canonical Strategy 913 reference merely because it contains documentation, diagnostics, tests, archived research, or workflow infrastructure.

## Authorized causal corrections

Only the following proven lookahead corrections are part of the canonical reference:

1. Premium 5-minute final-close data is available only after the represented 5-minute interval has fully completed.
2. Taker 5-minute observations are available only after the represented 5-minute interval has fully completed.

The actual decision time for a historical 1-minute candle labeled by its open timestamp is the label timestamp plus 60 seconds.

## Frozen behavior

The canonical reference does not authorize changes to:

- OI timing
- Crowding timing
- Funding timing
- thresholds
- universe
- sizing
- leverage
- entry logic
- exit logic
- 15-minute follow-through
- early failure
- trailing

Any change to these items would constitute a different strategy version unless explicitly authorized and separately frozen.

## Research exclusion

Post-causal diagnostics and LONG-only experiment files created after the canonical commit are not part of canonical Strategy 913 behavior.

The LONG-only experiment is `REJECTED / NOT ADOPTED`. Its scripts, tests, reports, and manual-only workflows are retained only for auditability and reproducibility and must not be interpreted as Strategy 913 rules.

## Interpretation rule

When there is any ambiguity between branch HEAD content and the frozen strategy, the canonical commit SHA and its corrected-reference artifact defined in this document are authoritative for Strategy 913 behavior.
