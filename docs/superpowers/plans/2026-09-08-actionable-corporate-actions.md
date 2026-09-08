# Actionable corporate actions implementation plan

Goal: Replace acknowledgement-only controls with split correction preview/apply, keep unsupported actions explicitly blocked, and automatically handle explicit broker split ratios.

Scope: Existing corporate-action queue and split accounting. No merger, spinoff, cash matching, or identifier remapping engine is added. Raw broker records remain immutable.

1. Classification and replay: test explicit broker SPLIT N FOR M descriptions, malformed ratios, corrected-source provenance, and durable manual ratios. Persist a manual ratio and its source record on the case; invalidate it if the broker payload changes. Use the same classification logic in the ledger reader.
2. Correction transaction: accept positive new/old share quantities for a uniquely identified split. Preview by recalculating affected stored snapshots and FIFO lots inside a transaction that rolls back. Apply recalculates the same preview, checks its fingerprint, and commits the ratio, case state, lots and snapshots together. Serialize against ingestion with its existing account lock. Reject unsupported cases, stale previews and absent snapshots.
3. UI: show Enter split ratio / Preview changes / Apply correction / Cancel, with before-and-after snapshot values and open FIFO lots. Invalidate a preview when input changes. Show Accounting support required without completion buttons for unsupported actions. Retain applied-correction history.
4. Verify: PostgreSQL regressions prove preview rollback, atomic application, replay persistence, source-change invalidation, stale-preview rejection, ingestion exclusion, and unsupported/invalid inputs. Execute shipped JavaScript for UI transitions. Run full pytest, Ruff, MyPy, review the diff, and redeploy after checks pass.

The user has authorized implementation and previously authorized deployment; proceed in this task without another approval cycle. Do not apply a manually entered ratio to live financial records on the user's behalf.

Completed validation: 420 pytest tests pass against PostgreSQL, Ruff passes, MyPy passes (66 runtime files). Review findings for prior-date provisional flags, reverse-ratio precision across multiple fills, and mismatched security identities are covered by regressions and fixed. Deployed with the additive split-correction migration; live health/UI checks pass. The SNEX 3-for-2 preview rebuilt nine snapshots in a rolled-back transaction and a before/after database fingerprint confirmed no changes were saved. No live correction was applied.
