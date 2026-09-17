# Strategy 913 — Final Freeze Record

## Status

- Step: `10`
- Result: `FINAL FREEZE / READY FOR USER REVIEW`
- Frozen review branch: `frozen/strategy-913-causal-validated`
- Final validation report commit inherited by this branch: `7a525e66ff4885a1d661aaae4919e327ea484592`
- Canonical Strategy 913 logic SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Strategy parameters modified by Step 10: `false`
- Strategy logic modified by Step 10: `false`

This branch is the review snapshot for the completed Strategy 913 validation package. It contains the frozen canonical strategy reference, the accepted audit/reporting infrastructure, the final validation report, and this freeze record.

## Canonical behavior

The authoritative Strategy 913 behavior remains the canonical logic SHA above. Only the two proven causal corrections are authorized:

1. Premium 5-minute final-close data is usable only after the represented interval has fully completed.
2. Taker 5-minute data is usable only after the represented interval has fully completed.

The following remain unchanged and frozen:

- OI timing
- Crowding timing and rule
- Funding timing
- thresholds
- fixed universe
- sizing
- leverage
- entry logic
- exit logic
- Early Failure
- 15-minute Follow-through
- trailing

## Validation package included in the snapshot

Primary records:

- `docs/STRATEGY_913_CANONICAL_REFERENCE.md`
- `docs/PARITY_REPORT.md`
- `docs/STRATEGY_913_HISTORICAL_RESULTS.md`
- `docs/STRATEGY_913_TRADE_AUDIT.md`
- `docs/STRATEGY_913_EXECUTION_RISK_AUDIT.md`
- `docs/STRATEGY_913_REPRODUCIBILITY_AUDIT.md`
- `docs/POST_CAUSAL_RESEARCH.md`
- `docs/STRATEGY_913_FINAL_REPORT.md`
- `docs/STRATEGY_913_FINAL_FREEZE.md`

Primary successful workflow evidence:

- Corrected causal reference: `35240653406`
- Canonical trade audit: `35255064158`
- Execution/risk audit: `35257145466`
- Reproducibility audit: `35258824800`
- Final-report CI: `35261536056`

## Accepted validation status

The frozen package records:

- 5 accepted historical windows;
- 46 total accepted trades;
- 46/46 causality/accounting trade-audit PASS;
- 46/46 execution/risk replay PASS;
- deterministic reproducibility PASS across 10 isolated executions;
- no liquidations in the accepted windows;
- LONG-only research variant rejected and not adopted.

The same package also preserves the adverse evidence rather than hiding it:

- 3 accepted windows were profitable and 2 were materially negative;
- highest observed per-window max drawdown was `73.13%`;
- the current sizing/leverage profile is aggressive;
- the accepted historical record contains an uncovered gap from `2026-06-01` to `2026-06-17`;
- historical validation does not establish future profitability or live-execution equivalence.

## Freeze rule

For review purposes, this branch is the frozen Strategy 913 validation snapshot.

Any commit made later on another branch is not part of this frozen review snapshot unless the user explicitly authorizes a new Strategy 913 version and a new freeze is performed.

The branch should not be moved, rebased, or used as a development branch without explicit user authorization. Future development should occur elsewhere.

## Technical boundary

This freeze is a repository reference and change-control convention. It does not claim that GitHub branch-protection rules are enabled. The exact commit currently pointed to by the frozen branch is the authoritative review snapshot for Step 10.

## Review entry point

Start review with:

`docs/STRATEGY_913_FINAL_REPORT.md`

Then use this freeze record to confirm that the material being reviewed belongs to the final Strategy 913 snapshot.
