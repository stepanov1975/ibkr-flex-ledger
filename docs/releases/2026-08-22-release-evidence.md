# 2026-08-22 release evidence

## Release scope

- Published reviewed baseline: `0011a749e3b19afa50cb26c6e6c7941362ccf094`
- Operations implementation branch evidence tip: `013060b7bbdceddd539ae50f888b971e54fc8e34`
- Compose project: `stock_app`
- Account scope: `DEFAULT_ACCOUNT`
- Flex query scope: `1402923`
- Active database migration: `20260822_07` (single Alembic head/current)

No alert destination was configured and no live alert notification was sent during release
verification.

## Backup evidence

The repair workflow began only after a verified custom-format rollback dump was created:

- archive: `/backups/broker-position-repair-20260822T092939Z.dump`
- SHA-256: `f13fcbba549a412e2434ff15babecfb7a928898edd665b4da1c75a9277ab570e`
- verification: `pg_restore --list` succeeded

A verified physical base backup was taken immediately before migration:

- archive: `/backups/base/20260822T104027Z.tar.gz`
- SHA-256: `e541e5d4dc1e36c78453b54dc45e93751792e8ff3ad1cf9851333ef9b67e9402`
- size: `16,189,626` bytes
- catalog status: `verified`

A second verified base backup captured the migrated `_07` release state:

- archive: `/backups/base/20260822T110214Z.tar.gz`
- SHA-256: `cc9f9959f44c704008b07bc5b787180a9ef2108b5493ddd6a245167489a63ac9`
- catalog status: `verified`

Both physical backups completed `pg_basebackup`, `pg_verifybackup`, archive checksum
creation, and catalog recording. Independent `sha256sum --check` verification succeeded.

## Migration evidence

The database was upgraded transactionally from `20260822_06` to `20260822_07`. Verification
reported exactly one Alembic head and one current revision. The live
`alert_delivery_state` table has seven expected columns, the composite primary key
`(account_id, channel, destination_fingerprint)`, and a channel constraint limited to
`webhook` and `email`. Its initial row count was zero.

## Immutable replay evidence

Replay used stored raw artifacts only; it did not contact IBKR. Raw persistence counts
remained unchanged at three artifacts and 46,027 raw records.

| Replay scope | First run ID | Idempotency run ID | Selected broker positions | Snapshot checksum |
| --- | --- | --- | ---: | --- |
| Period `2026-02-20`, report date `2026-02-19` | `41df138c-02d9-4ecb-82fe-3a213737bbbe` | `49b29611-8aaf-44a0-b472-9264e0c4fd62` | 85 | `0ff98faf60ea745dd026e07af1aa7e01` |
| Period `2026-08-21`, report date `2026-08-20` | `d876490c-6117-4e08-99d0-ef13c82a3fe5` | `6ee04cb8-4246-4af9-8764-43200334d8a4` | 105 | `0900d6b377e9aa4d42a3ec6cc117e549` |

Both selected artifact sets were nonempty and contained `OpenPositions`. The second replay
of each scope produced the same snapshot checksum and the same zero-discrepancy result.

Final counts for both scopes were zero for:

- broker position missing from the snapshot;
- broker/snapshot quantity mismatch; and
- nonzero snapshot position absent from the broker report.

## Remaining provisional rows

The remaining provisional states were explained rather than silently treated as repair
failures:

| Report date | Count | Reason | FX source | Cost basis |
| --- | ---: | --- | --- | --- |
| `2026-02-19` | 1 | `openpositions_mark_price` | `base_currency` | present |
| `2026-08-20` | 1 | `broker_position_absent` | `base_currency` | missing |
| `2026-08-20` | 3 | `openpositions_unrealized_pnl` | `base_currency` | present |

## Measured recovery objectives

### RPO

A transactional logical WAL probe was emitted and `pg_switch_wal()` forced the containing
segment to archive. The archive advanced from the preceding segment to
`00000001000000010000001D` with zero archiver failures.

- probe started: `2026-08-22T11:02:04.273Z`
- archive observed: `2026-08-22T11:02:04.853Z`
- measured archive completion: **0.580 seconds**
- archive lag at verification query: **0.092 seconds**
- target: **900 seconds (15 minutes)**
- result: **pass**

This measurement demonstrates WAL archive freshness for the release probe. It complements,
but does not replace, a full point-in-time recovery exercise.

### RTO

The weekly restore drill restored the post-migration backup into a temporary isolated Docker
volume, started PostgreSQL, and queried migration, ingestion-run, and snapshot data.

- drill evidence: `/stock_app/var/restore-drills/20260822T110223Z.json`
- restored backup: `20260822T110214Z.tar.gz`
- measured elapsed time: **5 seconds**
- target: **14,400 seconds (4 hours)**
- result: **pass**

The temporary restore container and volume were removed automatically.

## Quality and operations gates

- Focused operations/alert suite: 52 passed.
- Full suite: 275 passed, 21 environment-dependent skips.
- Ruff: passed with zero errors.
- MyPy: passed with zero errors.
- Shell syntax and Compose expansion: passed.
- All ten systemd service/timer definitions: verified using temporary worktree-path copies
  while preserving assertions that installed units target `/stock_app`.
- Whole-branch code review: approved after verified SMTP TLS and lock-safe manual execution
  fixes.
