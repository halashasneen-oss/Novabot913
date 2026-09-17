# Strategy 913 Reproducibility Audit

## Status

- Step: `8`
- Result: `PASS`
- Scope: deterministic reproducibility verification only
- Canonical Strategy 913 SHA: `158fb1c45a0cf88d549e301913f43435c337d7a1`
- Reproducibility implementation HEAD: `41c381b27f39958d50f175c64360c9feb4298369`
- Reproducibility workflow run: `35258824800`
- CI run on reproducibility HEAD: `35258824801`
- Strategy parameters modified: `false`

The reproducibility runner and workflow are verification infrastructure only. They do not replace or modify the frozen Strategy 913 behavior.

## Method

Each of the five canonical historical windows was executed twice in separate Python processes.

Each independent child process:

1. stages the same frozen external reference files;
2. loads the Strategy 913 reference modules from a clean temporary directory;
3. prepares/downloads the same historical raw market bars;
4. runs the same canonical causal filter and frozen strategy logic;
5. computes a SHA-256 digest of the raw input bars;
6. computes a SHA-256 digest of the complete canonical JSON result.

For every window the parent audit requires all of the following to match exactly between run 1 and run 2:

- canonical Strategy SHA;
- test window;
- raw input-data SHA-256;
- full result SHA-256;
- summary object;
- complete result object, including trade details and pipeline statistics.

The audit also performs a recursive comparison of the complete result. `first_difference` must be `null`. Any mismatch causes the workflow job to fail.

## Results

Ten independent strategy executions were performed in total: two executions for each of five windows.

| Window | Data SHA-256, both runs | Result SHA-256, both runs | Result |
| --- | --- | --- | --- |
| 2026-04-01 -> 2026-05-01 | `4c7a255efae3eac678e5e86cf510c38e0bc2075f8c7f32775c090849b05130d6` | `789f9c10644f999b97b145f8a49c62be167235e6b04869ca27d44240a5cfda0c` | PASS |
| 2026-05-01 -> 2026-06-01 | `fd1f1401518411d43c927ac8062e2409e96d866c90a5da5caefb8a8ba64d332d` | `6eb8cda88d366c2037dd3c763a4b383c6fac7cee432ab4f04f5bceb497f164e3` | PASS |
| 2026-06-17 -> 2026-07-17 | `a9ae333794c31e627389574d3d61826cf4140a6ba35f154563669b8c796ba3bd` | `6ae704a30829299c4937fe5134625ca1dc36d3864559e2a39b9dd2749e5619b9` | PASS |
| 2026-07-17 -> 2026-08-17 | `1d46f423bc00dbc2d97252cdbdc37cad997a77482625534055d7d4e4384f7ad6` | `9c39229b566cf7ce386067e8bf42c62e805c57850b903199c24d80dfc3d7e1a2` | PASS |
| 2026-08-17 -> 2026-09-16 | `eefdfdc630c13c891b0e4be6e006f0b8cead0b615e5594cdae6ede0396ade974` | `0a805e913954a5a8a401dc962e112f7764e8e0d48e613a97c88f3d1f3acb7736` | PASS |

For all five pairs:

- `input_data_sha256_matches = true`
- `result_sha256_matches = true`
- `summary_matches_exactly = true`
- `full_result_matches_exactly = true`
- `first_difference = null`

Therefore, within these controlled historical runs, identical input data and the same frozen Strategy 913 reference produced bit-for-bit-equivalent serialized strategy results on repeated isolated execution.

## Artifacts

All artifacts below were produced by workflow run `35258824800` from reproducibility HEAD `41c381b27f39958d50f175c64360c9feb4298369`.

| Window | Artifact ID | Artifact digest |
| --- | ---: | --- |
| apr_2026 | `10514396149` | `sha256:1a6020ff6bc905c422cca12979ca5acea9a054416f68e30a19e18b4cbcd4b9c2` |
| may_2026 | `10514511953` | `sha256:f7af078e8df89bd5122d8d99d4921c3e93f231b2a9f74822b18b843131cf3025` |
| jun_jul_2026 | `10514131798` | `sha256:d2c45778ff4ce940cd283eb0c1ad8043c7281028100b88595ccd1e02517aabad` |
| jul_aug_2026 | `10514765116` | `sha256:0252fb4d980c152d1204b2da3f0a7b857804e38b66a2af33c7780bffa9eab9e9` |
| aug_sep_2026 | `10513833891` | `sha256:87ed54eb3fe825100a7000494964c67cdc4d0acfe7527cf7258407560e80dbdc` |

## Interpretation and boundary

Step 8 establishes deterministic reproducibility for the five audited historical windows under the same frozen code and matching historical input data.

This does not claim that a live exchange will reproduce historical backtest fills or microstructure. It also does not guarantee that an external historical data provider can never revise an archived file in the future. The audit directly addresses that limitation by hashing the raw input bars: within each paired execution the input hashes were identical before the result hashes were compared.

No Strategy 913 parameter, threshold, universe rule, sizing rule, leverage rule, entry rule, exit rule, timing rule, Early Failure rule, 15-minute Follow-through rule, or trailing rule was changed in Step 8.
