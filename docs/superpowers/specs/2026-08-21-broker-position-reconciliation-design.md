# Broker Position Reconciliation Design

Date: 2026-08-21
Status: Implemented and verified on 2026-08-22
Repository: `/stock_app`

This is the approved historical design record. Current user-facing behavior is
documented in `README.md`; current repair commands and safety requirements are in
`docs/operations.md`. The active-database section below records the one-time repair
acceptance criteria and is not a reusable broker-position-count requirement.

## Problem

The daily P&L projection treats the canonical trade ledger as the source of
position quantity and uses `OpenPositions` only as a conditional mark-price
source. This produces false positions when IBKR emits economically real
assignment, exercise, expiration, transfer, or book transactions without an
`ibExecID`.

The live 2026-08-20 report demonstrates the failure:

- ALEX option assignment rows have stable transaction identifiers but blank
  `ibExecID` values, so the canonical mapper skips the closing option trade and
  opening stock trade.
- The later executable stock sale remains canonical, producing a false ALEX
  position of `-200` even though ALEX is absent from the report's
  `OpenPositions` section.
- The latest projection has 28 non-cash discrepancies: 24 stale nonzero
  positions absent from `OpenPositions`, two quantity mismatches, and two
  broker positions without snapshots.

This is an accounting-pipeline defect. UI formatting is not the cause.

## Goals

1. Preserve an auditable transaction ledger while accepting valid
   execution-level IBKR rows that lack `ibExecID`.
2. Treat a completed report's `OpenPositions` section as the authoritative
   end-of-day quantity assertion for supported non-cash instruments.
3. Show the broker quantity immediately when reconstructed FIFO quantity
   disagrees, and mark affected financial values provisional.
4. Include stocks, options, and broker-only positions in daily snapshots.
5. Replay immutable raw artifacts deterministically to repair canonical events,
   lots that can be reconstructed, and daily snapshots.
6. Preserve raw artifacts and raw rows unchanged, and back up the active
   database before repair.

## Non-Goals

- Inventing synthetic economic trades solely to force FIFO to match the broker.
- Treating currency/FX pseudo-instruments as `OpenPositions` holdings.
- Replacing IBKR-derived history with manually entered positions.
- Changing the ingestion schedule or issuing another live Flex request as part
  of repair.
- Making a provisional mismatch appear fully reconciled.

## Accounting Authority

The application maintains two related views:

- The canonical event ledger and `position_lot` table are the auditable
  reconstruction from identifiable economic events.
- `pnl_snapshot_daily.position_qty` is the broker-asserted end-of-day position
  when the report contains the required `OpenPositions` section.

For each supported non-cash instrument in the snapshot scope:

1. If an `OpenPositions` row exists, its position is authoritative.
2. If the instrument is absent from the complete `OpenPositions` section, its
   broker-asserted position is zero.
3. If FIFO quantity equals broker quantity, retain the existing FIFO cost and
   realized calculation and compute economic unrealized P&L from the broker
   mark.
4. If quantities differ, write the broker position. Use broker
   `costBasisMoney` and `fifoPnlUnrealized`, converted by `fxRateToBase` where
   required, as the best available cost/unrealized values. Retain only
   reconstructable realized values, set `provisional=true`, and emit mismatch
   diagnostics.
5. If a broker position has no canonical trades, create a provisional snapshot
   from the broker position facts. Use any reconstructable canonical realized
   value; when no such evidence exists, use zero realized P&L. Do not create a
   synthetic `position_lot`.
6. If the broker says zero but FIFO remains nonzero, write a zero-position
   provisional snapshot. Preserve the event-derived lot discrepancy unless a
   newly canonicalized real closing event resolves it.

`position_lot` remains event-derived. Reprocessing assignment/exercise events
will repair ALEX-style lots naturally; unresolved broker-only or missing-event
positions remain visible as provisional snapshot discrepancies rather than
fabricated lots.

## Canonical Trade Identity

The mapper continues to accept ordinary execution rows with a nonblank
`ibExecID`.

For a raw `Trades:Trade` row whose `levelOfDetail` is `EXECUTION` and whose
`ibExecID` is blank:

1. Use `transactionID` when nonblank, producing the namespaced identity
   `FLEX_TXN:<transactionID>`.
2. Otherwise use `tradeID` when nonblank, producing
   `FLEX_TRADE:<tradeID>`.
3. If neither stable identifier exists, raise a mapping contract violation.

The existing `event_trade_fill.ib_exec_id` natural-key column stores this
canonical execution identity. Namespacing prevents collision with genuine IBKR
execution IDs and avoids an unnecessary schema migration. The raw payload and
source raw-record FK continue to preserve the exact upstream identifiers.

Order, Lot, and SymbolSummary rows remain excluded. Only row tag `Trade` plus
`levelOfDetail=EXECUTION` can take the fallback identity path. This prevents
multiple raw representations of the same economic event from becoming
duplicate canonical fills.

## OpenPositions Mapping and Persistence

Canonical mapping will produce instrument upsert requests from valid
`OpenPositions` rows so a broker-only holding has an instrument record even
when no canonical trade exists.

The ledger OpenPositions record and query will include:

- instrument ID;
- asset category and instrument currency;
- position quantity;
- mark price;
- `costBasisMoney`;
- `fifoPnlUnrealized`;
- `fxRateToBase`;
- multiplier when present;
- report date.

The query will no longer filter to `assetCategory='STK'`. It will admit all
supported non-cash instrument categories, including options. Rows remain keyed
by the instrument matched through account and conid. Position quantity is
required. Blank optional mark, cost, unrealized, FX, or multiplier fields remain
unavailable rather than becoming zero; malformed nonblank numeric fields fail
closed.

## Snapshot Data Flow

Normal ingestion continues to persist immutable raw data, map changed canonical
rows, derive incremental scope, and then build snapshots.

For a full build, the snapshot service processes the union of:

- instruments with canonical trade or cashflow history; and
- instruments present in the artifact's OpenPositions rows.

For an incremental build, the same reconciliation applies only to the resolved
instrument scope. A closing trade supplies scope even when the instrument is
absent from OpenPositions; a new broker-only holding supplies scope through its
changed OpenPositions row.

The snapshot service receives the configured functional currency explicitly so
broker-only rows do not depend on the existence of a prior trade. Existing
callers retain USD as their explicit configured value; no hidden inference is
introduced.

Snapshot-stage diagnostics add:

- `broker_position_match_count`;
- `broker_position_mismatch_count`;
- `broker_only_position_count`;
- `broker_absent_nonzero_fifo_count`.

Existing incremental, full-fallback, and duplicate-skip diagnostics remain.

## Deterministic Reprocess

The current reprocess workflow maps canonical rows but does not rebuild
snapshots. It will be extended to replay snapshot state.

For one account, period key, and Flex query:

1. List candidate raw artifacts and their immutable owner/completion run state.
2. Group by non-null artifact `report_date_local`.
3. Select the newest successfully processed artifact per report date using
   `created_at_utc`, then UUID as the deterministic tie-break. For legacy
   pre-completion-marker artifacts, a successful immutable owner run qualifies.
4. Replay selected artifacts chronologically.
5. Map canonical rows from each selected artifact using full-artifact reads.
6. Build a full snapshot using the selected artifact's raw owner run ID for
   OpenPositions lineage and the artifact's actual report date.
7. Persist reprocess-run diagnostics separately from snapshot provenance.

Each selected artifact must contain the required completed `OpenPositions`
section. Missing required sections fail replay before unsupported-date cleanup.

Canonical UPSERT natural keys keep replay idempotent. Repeated reprocess runs
must produce the same canonical identities and snapshot values.

## Repairing Existing Snapshot Dates

Legacy runs created snapshot rows under runtime-local dates rather than the
artifact's actual report date. During an explicit full reprocess target only,
the application may replace those unsupported rows:

- Determine the set of selected artifact report dates.
- Determine ingestion run IDs belonging to artifacts in the reprocess scope.
- Delete snapshot rows whose `ingestion_run_id` belongs to that scope but whose
  date is not represented by any selected source artifact.
- Never delete immutable raw rows, artifacts, canonical events, or snapshots
  outside the explicit account/period/query scope.

The delete and replacement snapshot writes must occur after a verified backup.
The operator-facing repair report lists the exact unsupported dates and row
counts before mutation.

## Failure Handling

- An execution-level trade with no strong identity fails canonical mapping.
- A missing or malformed OpenPositions quantity, or a malformed nonblank mark,
  cost basis, unrealized, FX, or multiplier value, fails the affected run rather
  than coercing an arbitrary value.
- An OpenPositions/FIFO mismatch does not fail ingestion. Broker quantity wins,
  the snapshot is provisional, and diagnostics make the discrepancy explicit.
- Reprocess fails as a unit when an artifact cannot be mapped or snapshotted;
  it does not report success after a partial replay. Canonical UPSERTs from a
  failed attempt may remain and are safe to retry, but unsupported-date cleanup
  runs only after every selected artifact has replayed successfully.
- Existing single-active-run protection remains in force.

## Tests

Test-driven implementation will cover:

1. Fallback canonical identity for execution-level assignment/exercise
   `BookTrade` rows.
2. Rejection of execution-level rows without any stable identity.
3. Continued exclusion of Order, Lot, and SymbolSummary rows.
4. ALEX-style short put sale, assignment closing the option and acquiring
   shares, then share sale, ending with zero option and stock positions.
5. OpenPositions stock and option parsing, including cost, unrealized P&L, FX,
   and multiplier fields.
6. Exact FIFO/broker matches remaining non-provisional.
7. Quantity mismatch, broker-absent nonzero FIFO, and broker-only position
   snapshots using broker quantity and provisional status.
8. Cash/FX pseudo-instruments excluded from OpenPositions reconciliation.
9. Deterministic artifact selection, chronological replay, snapshot rebuild,
   and unsupported-date cleanup.
10. PostgreSQL end-to-end replay with immutable raw preservation and idempotent
    second replay.

The final gates are the full pytest suite, Ruff, configured MyPy, strict MyPy
for changed tests, dependency integrity, and whitespace validation.

## Active Database Repair

After implementation and review:

1. Create and validate a new custom-format PostgreSQL backup.
2. Record current table counts and the full latest broker/snapshot discrepancy
   report.
3. Deploy the reviewed code without issuing a live IBKR request.
4. Reprocess each distinct stored period/query scope from immutable raw data.
5. Verify raw artifact and raw-row counts are unchanged.
6. Compare the latest snapshot against all 105 current OpenPositions rows.
7. Require zero missing snapshots and zero broker quantity mismatches.
8. Verify ALEX stock and option positions are zero.
9. Report remaining provisional rows and their explicit mismatch reasons.
10. Keep the backup path in the handoff.

## Success Criteria

- ALEX stock and expired option no longer appear as open positions.
- Latest non-cash snapshot quantities equal the latest broker OpenPositions
  assertion, treating absence as zero for scoped reconstructed instruments.
- Broker-only holdings receive snapshots.
- Unreconciled financial values are visibly provisional, never silently final.
- Assignment/exercise events with stable fallback identities are canonical and
  idempotent.
- Raw history remains immutable.
- Reprocess is deterministic and repairs supported historical snapshot dates.
- The live application remains healthy after repair.
