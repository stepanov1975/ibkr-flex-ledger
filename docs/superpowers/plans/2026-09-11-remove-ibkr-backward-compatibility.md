> Historical record. Preserved for design rationale and implementation evidence;
> it does not define current behavior or an active work queue. See the
> [documentation index](../../README.md).
> Commands, line numbers, checkboxes, and validation results describe the recorded work, not the current release.

# Remove newly added IBKR backward compatibility

The application has one installation. The user requested careful removal of backward compatibility introduced by the preceding reliability work. This supersedes that plan's legacy-version accommodations.

Read-only inspection confirmed that the installation remains at migration `20260910_15`; the new `20260911_16` migration has not been applied. Its failed runs are timeouts and own no raw artifacts. Existing successful artifacts include reports without explicit completion pointers, so preserve the existing successful-source contract and actual stored history. No production writes or deployment are part of this task.

1. Remove the `semantic_atomic` version flag, its unapplied migration, and the failed-history skip branch. Require the current repository guard/transaction methods; remove old-interface fallbacks. Require a held account guard when starting a run. Verify lifecycle, recovery, duplicate-skip and migration tests.
2. Enforce execution consistency in ingestion and replay, validating original/latest report values before provenance overlay can hide a conflict. Remove the validation-bypass parameter. Preserve ordinary replay, source provenance and supported IBKR report variations. Verify harmless derived-value refreshes and rejected inconsistent replay.
3. Remove the legacy test fixtures added in the previous work. Rewrite affected tests for current atomic rollback, conflict rejection, and valid replay; retain meaningful read-model and correction coverage. Independent test-file groups may be updated in parallel after runtime contracts are fixed.
4. Update current operator guidance, run the complete suite on temporary PostgreSQL, run Ruff/MyPy and review the removal. Keep the feature branch and stored production data intact.

Real-history verification found an additional required adjustment: enabling replay validation runs the identity query against 274,183 stored raw rows. PostgreSQL can inline its normalized raw-identity expressions into a pairwise join; the first replay spent over 100 seconds in that query. Materializing those normalized identities once measured 1.707 seconds for all 833 execution identities on the isolated copy. Verify the original query after `ANALYZE`, then apply this one-query change if the difference remains; retain all validation rules and confirm the existing consistency suite plus successful replay of every stored scope.

Completed verification:

- Removed the new version flag/migration, failed-history branch, old-interface fallbacks, replay-validation bypass and legacy test fixtures. The schema head remains `20260910_15`.
- After `ANALYZE`, the original identity query exceeded a 10-second timeout. The final materialized query returned the same identity evidence in 1.727 seconds. No validation rule changed.
- Final full suite: **727 passed in 88.24 seconds**. Focused final-query checks: **20 passed**. Ruff, MyPy (73 source files), and whitespace checks passed. Independent final-code review found no open issues and independently passed 46 orchestration tests.
- All **18 stored report scopes** replayed successfully against a fresh temporary copy of the installation. Counts stayed unchanged: 19 raw artifacts, 274,183 raw records, 833 trade fills, 340 cashflows, 18,993 FX events and 3 corporate actions.
- Production was read only; no deployment, migration or data repair was performed. The isolated copy and test databases were removed with their temporary container after verification.
