# Incremental Ingestion Performance Implementation Plan

Status: Completed on 2026-08-21

This is a historical execution plan, not a current runbook or progress tracker. The
checkboxes preserve the planning format. Use `README.md`, `linting.md`, `testing.md`,
and `docs/operations.md` for current behavior and commands.

> Historical note: the unchecked boxes preserve the original planning template; do not
> execute this file as a current implementation checklist.

**Goal:** Make normal IBKR Flex ingestion deterministic and change-driven while retaining every distinct raw artifact and preserving full reprocess behavior.

**Architecture:** Exact duplicate artifacts short-circuit after identity validation; distinct artifacts retain all raw rows and canonicalize only rows changed from their immediately preceding version. Canonical instruments are batch-upserted, snapshots are rebuilt only for affected instruments/currencies, and monotonic stage durations make remaining costs observable.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core, PostgreSQL 17, Alembic, pytest, Ruff, MyPy, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-21-ingestion-performance-design.md`

## Global Constraints

- Preserve all distinct source artifacts and every raw row extracted from them.
- Treat exact duplicates under `(account_id, period_key, flex_query_id, payload_sha256)` as successful skipped runs.
- Compare each normal-ingestion raw row only with its immediately preceding version; a reverted value is changed.
- Keep all-row reads and full snapshot behavior available to deterministic reprocess callers.
- Do not delete historical raw or canonical data and do not change the external IBKR Flex query.
- Add no environment variables, third-party packages, queues, or distributed workers.
- Keep existing repository transaction boundaries and deterministic error mapping.
- Record durations with `time.perf_counter_ns()` as non-negative integer milliseconds.
- Use behavioral assertions, not wall-clock thresholds, as automated performance regression gates.

---

## File Map

- `app/adapters/flex_web_service.py`: deterministic polling schedule and adapter-stage monotonic timings.
- `app/db/interfaces.py`: changed-row, batch-instrument, and scoped-ledger port contracts.
- `app/db/canonical_persistence.py`: prior-version delta query and one-statement instrument UPSERT.
- `app/db/ledger_snapshot.py`: scoped canonical-event reads, instrument resolution, and scoped lot reconciliation.
- `app/jobs/canonical_pipeline.py`: deduplicate instrument requests and call the batch port once.
- `app/jobs/incremental_scope.py`: pure changed-row-to-snapshot-scope derivation.
- `app/jobs/ingestion_orchestrator.py`: duplicate short circuit, delta pipeline integration, fallback reason, and stage timings.
- `app/ledger/snapshot_service.py`: full-or-scoped snapshot calculation and persistence.
- `alembic/versions/20260821_04_ingestion_performance_indexes.py`: additive raw-row indexes.
- `tests/test_adapters_flex_web_service.py`: polling and timing contract tests.
- `tests/test_db_migrations.py`: migration upgrade/downgrade tests.
- `tests/test_db_canonical_upsert.py`: delta selection and batch instrument persistence tests.
- `tests/test_jobs_canonical_pipeline.py`: one-call instrument batch behavior.
- `tests/test_jobs_incremental_scope.py`: pure scope derivation and unsafe-row fallback tests.
- `tests/test_jobs_ingestion_orchestrator.py`: short-circuit, delta, fallback, and diagnostics tests.
- `tests/test_ledger_snapshot_service_strict.py`: scoped build/no-op/full compatibility tests.
- `tests/test_end_to_end_seeded.py`: PostgreSQL duplicate and distinct-artifact regression coverage.
- `README.md`: document skip and timing diagnostics.

---

### Task 1: Deterministic Polling and Adapter Timings

**Files:**
- Modify: `app/adapters/flex_web_service.py`
- Test: `tests/test_adapters_flex_web_service.py`

**Interfaces:**
- Consumes: existing `FlexWebServiceAdapter.adapter_calculate_retry_wait_seconds(retry_index: int) -> float`.
- Produces: retry index `0` returns exactly `initial_wait_seconds`; retry index `n > 0` applies jitter to `min(backoff_base_seconds * 2 ** (n - 1), backoff_max_seconds)`.
- Produces diagnostic integer fields: `request_transport_duration_ms`, `statement_polling_duration_ms`, `statement_poll_wait_duration_ms`, and existing attempt metadata.

- [ ] **Step 1: Replace the old wait expectations with fixed-first-poll and retry-jitter tests**

```python
def test_adapter_calculate_retry_wait_uses_fixed_initial_wait_then_backoff() -> None:
    adapter = FlexWebServiceAdapter(
        token="token",
        initial_wait_seconds=5,
        retry_backoff_base_seconds=10,
        retry_max_backoff_seconds=60,
        jitter_min_multiplier=0.5,
        jitter_max_multiplier=1.5,
        random_unit_interval_provider=lambda: 1.0,
    )

    assert adapter.adapter_calculate_retry_wait_seconds(0) == 5.0
    assert adapter.adapter_calculate_retry_wait_seconds(1) == 15.0
    assert adapter.adapter_calculate_retry_wait_seconds(2) == 30.0
    assert adapter.adapter_calculate_retry_wait_seconds(4) == 90.0


def test_adapter_poll_diagnostics_include_monotonic_durations(monkeypatch: pytest.MonkeyPatch) -> None:
    request_payload = (
        b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode>"
        b"<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
    )
    report_payload = b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement /></FlexStatements></FlexQueryResponse>'
    payloads = [request_payload, report_payload]
    waits: list[float] = []
    adapter = FlexWebServiceAdapter(token="token", initial_wait_seconds=5, retry_attempts=1)
    monkeypatch.setattr(adapter, "_adapter_http_get", lambda **_kwargs: payloads.pop(0))
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    result = adapter.adapter_fetch_report(query_id="query")

    request_details = next(
        event["details"] for event in result.stage_timeline
        if event["stage"] == "request" and event["status"] == "completed"
    )
    poll_details = next(
        event["details"] for event in result.stage_timeline
        if event["stage"] == "poll" and event["status"] == "completed"
    )
    assert request_details["request_transport_duration_ms"] >= 0
    assert poll_details["statement_polling_duration_ms"] >= 0
    assert poll_details["statement_poll_wait_duration_ms"] == 5_000
    assert waits == [5.0]
```

- [ ] **Step 2: Run the focused tests and verify the old behavior fails**

Run: `pytest -q tests/test_adapters_flex_web_service.py -k 'fixed_initial_wait or monotonic_durations'`

Expected: FAIL because retry index zero is jittered and the new duration keys are absent.

- [ ] **Step 3: Implement the fixed initial wait and monotonic measurements**

```python
from time import perf_counter_ns


def _duration_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)


def strategy_calculate_retry_wait_seconds(self, retry_index: int) -> float:
    if retry_index < 0:
        raise ValueError("retry_index must be >= 0")
    if retry_index == 0:
        return float(self.initial_wait_seconds)
    retry_base = min(
        self.backoff_base_seconds * (2 ** (retry_index - 1)),
        self.max_backoff_seconds,
    )
    return float(retry_base) * self.strategy_calculate_jitter_multiplier()
```

Wrap request transport and the complete statement-poll loop with `perf_counter_ns()`, accumulate the actual requested sleep seconds, and add the three exact keys above to the adapter's completed diagnostic details. Preserve server-provided minimum retry delays.

- [ ] **Step 4: Run the complete adapter suite**

Run: `pytest -q tests/test_adapters_flex_web_service.py`

Expected: PASS.

- [ ] **Step 5: Commit the polling slice**

```bash
git add app/adapters/flex_web_service.py tests/test_adapters_flex_web_service.py
git commit -m "perf: stabilize Flex polling timing"
```

---

### Task 2: Raw-row Delta Indexes and Immediate-predecessor Selection

**Files:**
- Create: `alembic/versions/20260821_04_ingestion_performance_indexes.py`
- Modify: `app/db/interfaces.py`
- Modify: `app/db/canonical_persistence.py`
- Test: `tests/test_db_migrations.py`
- Test: `tests/test_db_canonical_upsert.py`

**Interfaces:**
- Consumes: `RawRecordForCanonicalMapping` and existing `db_raw_record_list_for_run(ingestion_run_id: UUID)`.
- Produces: `RawRecordReadRepositoryPort.db_raw_record_list_changed_for_run(ingestion_run_id: UUID) -> list[RawRecordForCanonicalMapping]`.
- Preserves: all-row run and period reads for reprocessing.

- [ ] **Step 1: Add migration tests for both additive indexes**

```python
# Add inside test_migrations_apply_and_are_idempotent(), after creating inspector.
raw_record_indexes = {
    index["name"]: tuple(index["column_names"])
    for index in inspector.get_indexes("raw_record")
}
assert raw_record_indexes["ix_raw_record_run_created_id"] == (
    "ingestion_run_id", "created_at_utc", "raw_record_id"
)
assert raw_record_indexes["ix_raw_record_prior_version"] == (
    "account_id", "flex_query_id", "section_name", "source_row_ref",
    "created_at_utc", "raw_record_id",
)

# Add after verification_engine.dispose(), while DATABASE_URL still targets the test DB.
command.downgrade(alembic_config, "20260821_03")
downgraded_engine = create_engine(temp_database_url)
try:
    downgraded_names = {
        index["name"] for index in inspect(downgraded_engine).get_indexes("raw_record")
    }
    assert "ix_raw_record_run_created_id" not in downgraded_names
    assert "ix_raw_record_prior_version" not in downgraded_names
finally:
    downgraded_engine.dispose()
```

- [ ] **Step 2: Run the migration test and verify it fails**

Run: `pytest -q tests/test_db_migrations.py -k ingestion_indexes`

Expected: FAIL because revision `20260821_04` does not exist.

- [ ] **Step 3: Create the reversible migration**

```python
"""Add raw-record indexes for incremental ingestion."""

from alembic import op

revision = "20260821_04"
down_revision = "20260821_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_raw_record_run_created_id "
        "ON raw_record (ingestion_run_id, created_at_utc, raw_record_id)"
    )
    op.execute(
        "CREATE INDEX ix_raw_record_prior_version ON raw_record "
        "(account_id, flex_query_id, section_name, source_row_ref, "
        "created_at_utc DESC, raw_record_id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_raw_record_prior_version")
    op.execute("DROP INDEX ix_raw_record_run_created_id")
```

- [ ] **Step 4: Add a three-version delta selection test**

```python
def test_changed_rows_compare_with_immediate_predecessor() -> None:
    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_changed_rows_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    database_url = _upsert_build_database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _upsert_create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = database_url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(database_url)
        run_ids = [str(uuid.uuid4()) for _ in range(4)]
        artifact_ids = [str(uuid.uuid4()) for _ in range(4)]
        payloads = ['{"price":"10"}', '{"price":"11"}', '{"price":"10"}', '{"price":"10"}']
        with engine.begin() as connection:
            for index, (run_id, artifact_id, payload) in enumerate(
                zip(run_ids, artifact_ids, payloads, strict=True), start=1
            ):
                connection.execute(text(
                    "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, "
                    "period_key, flex_query_id, started_at_utc, ended_at_utc) VALUES "
                    "(CAST(:run_id AS uuid), 'U1', 'manual', 'success', '2026-08', 'query', "
                    "CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
                ), {"run_id": run_id, "created": f"2026-08-21T00:00:0{index}+00:00"})
                connection.execute(text(
                    "INSERT INTO raw_artifact (raw_artifact_id, ingestion_run_id, account_id, period_key, "
                    "flex_query_id, payload_sha256, report_date_local, source_payload, created_at_utc) VALUES "
                    "(CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), 'U1', '2026-08', 'query', :sha, "
                    "DATE '2026-08-21', CAST(:sha AS bytea), CAST(:created AS timestamptz))"
                ), {"artifact_id": artifact_id, "run_id": run_id, "sha": f"sha-{index}",
                    "created": f"2026-08-21T00:00:0{index}+00:00"})
                connection.execute(text(
                    "INSERT INTO raw_record (raw_record_id, raw_artifact_id, ingestion_run_id, account_id, "
                    "period_key, flex_query_id, payload_sha256, report_date_local, section_name, "
                    "source_row_ref, source_payload, created_at_utc) VALUES "
                    "(gen_random_uuid(), CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), 'U1', '2026-08', "
                    "'query', :sha, DATE '2026-08-21', 'Trades', 'trade-1', CAST(:payload AS jsonb), "
                    "CAST(:created AS timestamptz))"
                ), {"artifact_id": artifact_id, "run_id": run_id, "sha": f"sha-{index}",
                    "payload": payload, "created": f"2026-08-21T00:00:0{index}+00:00"})

        repository = SQLAlchemyCanonicalPersistenceService(engine)
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[0]))) == 1
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[1]))) == 1
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[2]))) == 1
        assert repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[3])) == []
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url, database_name)
```

- [ ] **Step 5: Run the delta test and verify the missing port fails**

Run: `pytest -q tests/test_db_canonical_upsert.py -k immediate_predecessor`

Expected: FAIL with `AttributeError` for `db_raw_record_list_changed_for_run`.

- [ ] **Step 6: Add the port and PostgreSQL lateral prior-version query**

```python
class RawRecordReadRepositoryPort(Protocol):
    def db_raw_record_list_changed_for_run(
        self,
        ingestion_run_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]: ...
```

Use one SQL statement with this predicate and ordering, mapping selected columns through the existing `_db_canonical_read_raw_rows` row conversion:

```sql
FROM raw_record AS current
LEFT JOIN LATERAL (
    SELECT previous.raw_record_id, previous.source_payload
    FROM raw_record AS previous
    WHERE previous.account_id = current.account_id
      AND previous.flex_query_id = current.flex_query_id
      AND previous.section_name = current.section_name
      AND previous.source_row_ref = current.source_row_ref
      AND previous.ingestion_run_id <> current.ingestion_run_id
      AND (
          previous.created_at_utc < current.created_at_utc
          OR (
              previous.created_at_utc = current.created_at_utc
              AND previous.raw_record_id < current.raw_record_id
          )
      )
    ORDER BY previous.created_at_utc DESC,
             previous.raw_record_id DESC
    LIMIT 1
) AS prior ON TRUE
WHERE current.ingestion_run_id = :ingestion_run_id
  AND (
      prior.raw_record_id IS NULL
      OR current.source_payload IS DISTINCT FROM prior.source_payload
  )
ORDER BY current.created_at_utc, current.raw_record_id
```

- [ ] **Step 7: Run migration and canonical DB tests**

Run: `pytest -q tests/test_db_migrations.py tests/test_db_canonical_upsert.py`

Expected: PASS.

- [ ] **Step 8: Commit the delta-read foundation**

```bash
git add alembic/versions/20260821_04_ingestion_performance_indexes.py app/db/interfaces.py app/db/canonical_persistence.py tests/test_db_migrations.py tests/test_db_canonical_upsert.py
git commit -m "perf: select changed raw records incrementally"
```

---

### Task 3: One-round-trip Instrument UPSERT

**Files:**
- Modify: `app/db/interfaces.py`
- Modify: `app/db/canonical_persistence.py`
- Modify: `app/jobs/canonical_pipeline.py`
- Test: `tests/test_db_canonical_upsert.py`
- Test: `tests/test_jobs_canonical_pipeline.py`

**Interfaces:**
- Consumes: `CanonicalInstrumentUpsertRequest` and `CanonicalInstrumentRecord`.
- Produces: `CanonicalPersistenceRepositoryPort.db_canonical_instrument_upsert_many(requests: list[CanonicalInstrumentUpsertRequest]) -> list[CanonicalInstrumentRecord]`.
- Preserves: single-instrument UPSERT as a compatibility wrapper over the batch method.

- [ ] **Step 1: Add pipeline and database tests for a single batch call**

```python
class _CanonicalPipelineRepositoryStub:
    def __init__(self) -> None:
        self.instrument_batch_calls = 0
        self.instrument_requests = []
        self.bulk_upsert_calls = 0

    def db_canonical_instrument_upsert_many(self, requests):
        self.instrument_batch_calls += 1
        self.instrument_requests = list(requests)
        return [
            type("InstrumentRecord", (), {
                "instrument_id": uuid4(),
                "account_id": request.account_id,
                "conid": request.conid,
            })()
            for request in requests
        ]

    def db_canonical_bulk_upsert(
        self, trade_requests, cashflow_requests, fx_requests, corp_action_requests
    ) -> None:
        self.bulk_upsert_calls += 1


# Replace the final instrument assertions in the existing
# test_jobs_canonical_pipeline_reports_unique_instrument_upsert_count test.
assert repository_stub.instrument_batch_calls == 1
assert [request.conid for request in repository_stub.instrument_requests] == ["265598"]
assert result_counts["instrument_upsert_count"] == 1


def test_batch_instrument_upsert_returns_records_by_conid() -> None:
    engine = db_create_engine(config_load_settings().database_url)
    account_id = f"U_BATCH_{uuid.uuid4().hex[:8]}"
    repository = SQLAlchemyCanonicalPersistenceService(engine)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not reachable for batch instrument integration test")

    def request(conid: str, symbol: str) -> CanonicalInstrumentUpsertRequest:
        return CanonicalInstrumentUpsertRequest(
            account_id=account_id,
            conid=conid,
            symbol=symbol,
            local_symbol=symbol,
            isin=None,
            cusip=None,
            figi=None,
            asset_category="STK",
            currency="USD",
            description=None,
        )

    try:
        records = repository.db_canonical_instrument_upsert_many([
            request("100", "AAA"), request("200", "BBB")
        ])
        assert {record.conid for record in records} == {"100", "200"}
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM instrument WHERE account_id=:account_id"), {"account_id": account_id})
        engine.dispose()
```

- [ ] **Step 2: Run focused tests and verify the batch method is absent**

Run: `pytest -q tests/test_jobs_canonical_pipeline.py tests/test_db_canonical_upsert.py -k 'deduplicated_instruments or batch_instrument'`

Expected: FAIL because the batch port and stub counters do not exist.

- [ ] **Step 3: Add the batch port and one-statement implementation**

```python
class CanonicalPersistenceRepositoryPort(Protocol):
    def db_canonical_instrument_upsert_many(
        self,
        requests: list[CanonicalInstrumentUpsertRequest],
    ) -> list[CanonicalInstrumentRecord]: ...
```

Validate every request, serialize it once, and execute one statement shaped as follows:

```sql
WITH input AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:requests_json AS jsonb)) AS value(
        account_id text, conid text, symbol text, local_symbol text,
        isin text, cusip text, figi text, asset_category text,
        currency text, description text
    )
), upserted AS (
    INSERT INTO instrument (
        account_id, conid, symbol, local_symbol, isin, cusip, figi,
        asset_category, currency, description
    )
    SELECT account_id, conid, symbol, local_symbol, isin, cusip, figi,
           asset_category, currency, description
    FROM input
    ON CONFLICT (account_id, conid) DO UPDATE SET
        symbol = EXCLUDED.symbol,
        local_symbol = EXCLUDED.local_symbol,
        isin = EXCLUDED.isin,
        cusip = EXCLUDED.cusip,
        figi = EXCLUDED.figi,
        asset_category = EXCLUDED.asset_category,
        currency = EXCLUDED.currency,
        description = EXCLUDED.description
    RETURNING instrument_id, account_id, conid
)
SELECT * FROM upserted ORDER BY conid
```

Make `db_canonical_instrument_upsert(request)` call `db_canonical_instrument_upsert_many([request])[0]`.

- [ ] **Step 4: Switch the canonical pipeline to the batch port**

```python
requests_by_conid = {request.conid: request for request in mapped_batch.instrument_requests}
records = canonical_persistence_repository.db_canonical_instrument_upsert_many(
    [requests_by_conid[conid] for conid in sorted(requests_by_conid)]
)
instrument_id_by_conid = {record.conid: record.instrument_id for record in records}
```

- [ ] **Step 5: Run canonical pipeline, persistence, and reprocess tests**

Run: `pytest -q tests/test_jobs_canonical_pipeline.py tests/test_db_canonical_upsert.py tests/test_jobs_reprocess.py`

Expected: PASS, including stable canonical origin identity and mutable correction updates.

- [ ] **Step 6: Commit the batch UPSERT**

```bash
git add app/db/interfaces.py app/db/canonical_persistence.py app/jobs/canonical_pipeline.py tests/test_db_canonical_upsert.py tests/test_jobs_canonical_pipeline.py
git commit -m "perf: batch canonical instrument upserts"
```

---

### Task 4: Pure Incremental Snapshot Scope Derivation

**Files:**
- Create: `app/jobs/incremental_scope.py`
- Create: `tests/test_jobs_incremental_scope.py`

**Interfaces:**
- Consumes: `list[RawRecordForCanonicalMapping]`.
- Produces: immutable `IncrementalSnapshotScope(conids: frozenset[str], currencies: frozenset[str], full_rebuild_reason: str | None)`.
- Produces: `job_build_incremental_snapshot_scope(rows: list[RawRecordForCanonicalMapping]) -> IncrementalSnapshotScope`.

- [ ] **Step 1: Write scope union, empty, and unsafe-row tests**

```python
from datetime import date
from uuid import uuid4

from app.db.interfaces import RawRecordForCanonicalMapping


def _raw_row(section_name: str, source_payload: dict[str, str]) -> RawRecordForCanonicalMapping:
    run_id = uuid4()
    return RawRecordForCanonicalMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=run_id,
        account_id="U1",
        period_key="2026-08",
        flex_query_id="query",
        report_date_local=date(2026, 8, 21),
        section_name=section_name,
        source_row_ref=f"{section_name}:row-1",
        source_payload=source_payload,
    )


def test_scope_unions_event_conids_and_fx_source_currencies() -> None:
    scope = job_build_incremental_snapshot_scope([
        _raw_row("Trades", {"conid": "100"}),
        _raw_row("CashTransactions", {"conid": "200"}),
        _raw_row("CorporateActions", {"conid": "100"}),
        _raw_row("OpenPositions", {"conid": "300"}),
        _raw_row("ConversionRates", {"fromCurrency": "EUR"}),
    ])
    assert scope.conids == frozenset({"100", "200", "300"})
    assert scope.currencies == frozenset({"EUR"})
    assert scope.full_rebuild_reason is None


def test_scope_requests_full_rebuild_when_relevant_key_is_missing() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("Trades", {"symbol": "AAA"})])
    assert scope.full_rebuild_reason == "unscopable_changed_row:Trades:missing_conid"


def test_scope_is_empty_for_snapshot_irrelevant_sections() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("AccountInformation", {"accountId": "U1"})])
    assert scope == IncrementalSnapshotScope(frozenset(), frozenset(), None)
```

- [ ] **Step 2: Run the new test file and verify import failure**

Run: `pytest -q tests/test_jobs_incremental_scope.py`

Expected: FAIL because `app.jobs.incremental_scope` does not exist.

- [ ] **Step 3: Implement the pure scope builder**

```python
from dataclasses import dataclass

from app.db.interfaces import RawRecordForCanonicalMapping


@dataclass(frozen=True)
class IncrementalSnapshotScope:
    conids: frozenset[str]
    currencies: frozenset[str]
    full_rebuild_reason: str | None


def job_build_incremental_snapshot_scope(
    rows: list[RawRecordForCanonicalMapping],
) -> IncrementalSnapshotScope:
    conids: set[str] = set()
    currencies: set[str] = set()
    for row in rows:
        if row.section_name in {"Trades", "CashTransactions", "CorporateActions", "OpenPositions"}:
            conid = str(row.source_payload.get("conid", "")).strip()
            if not conid:
                return IncrementalSnapshotScope(
                    frozenset(), frozenset(),
                    f"unscopable_changed_row:{row.section_name}:missing_conid",
                )
            conids.add(conid)
        elif row.section_name == "ConversionRates":
            currency = str(row.source_payload.get("fromCurrency", "")).strip().upper()
            if not currency:
                return IncrementalSnapshotScope(
                    frozenset(), frozenset(),
                    "unscopable_changed_row:ConversionRates:missing_fromCurrency",
                )
            currencies.add(currency)
    return IncrementalSnapshotScope(frozenset(conids), frozenset(currencies), None)
```

- [ ] **Step 4: Run the pure scope tests**

Run: `pytest -q tests/test_jobs_incremental_scope.py`

Expected: PASS.

- [ ] **Step 5: Commit the scope model**

```bash
git add app/jobs/incremental_scope.py tests/test_jobs_incremental_scope.py
git commit -m "feat: derive incremental snapshot scope"
```

---

### Task 5: Scoped Snapshot Repository and Service

**Files:**
- Modify: `app/db/interfaces.py`
- Modify: `app/db/ledger_snapshot.py`
- Modify: `app/ledger/snapshot_service.py`
- Test: `tests/test_ledger_snapshot_service_strict.py`

**Interfaces:**
- Consumes: affected conids/currencies from Task 4.
- Produces: `ledger_snapshot_build_and_persist(account_id: str, ingestion_run_id: str | None, report_date_local: str, affected_conids: frozenset[str] | None = None, affected_currencies: frozenset[str] | None = None) -> SnapshotBuildResult`.
- Produces: `db_ledger_instrument_ids_for_scope(account_id: str, conids: tuple[str, ...], currencies: tuple[str, ...]) -> list[str]`.
- Produces: `db_ledger_instrument_currency_list(instrument_ids: tuple[str, ...]) -> list[str]` so scoped FX reads cover instrument and event currencies without loading the full FX table.
- Extends ledger read methods with `instrument_ids: tuple[str, ...] | None = None`; extends FX reads with `currencies: tuple[str, ...] | None = None`; extends lot reconciliation with `instrument_ids: tuple[str, ...] | None = None`.
- Meaning: `None` is full scope and an empty resolved scope is a successful no-op.

- [ ] **Step 1: Add service tests for scoped, empty, and full-compatible builds**

```python
# Extend the existing _RepositoryStub constructor with these parameters/state.
def __init__(
    self,
    trades,
    valuations,
    fx_rates=None,
    cashflows=None,
    corporate_actions=None,
    scope_ids=None,
    instrument_currencies=None,
) -> None:
    self._scope_ids = list(scope_ids or [])
    self._instrument_currencies = list(instrument_currencies or [])
    self.trade_instrument_ids = None
    self.cashflow_instrument_ids = None
    self.corp_action_instrument_ids = None
    self.open_position_instrument_ids = None
    self.reconciled_instrument_ids = None
    self.fx_currencies = None
    self.read_call_count = 0
    # Retain the existing assignments for trades, valuations, FX, captures, and counters.


def db_ledger_instrument_ids_for_scope(self, account_id, conids, currencies):
    self.read_call_count += 1
    return self._scope_ids


def db_ledger_instrument_currency_list(self, instrument_ids):
    self.read_call_count += 1
    return self._instrument_currencies


def db_ledger_trade_fill_list_for_account(
    self, account_id, through_report_date_local=None, instrument_ids=None
):
    self.read_call_count += 1
    self.trade_instrument_ids = instrument_ids
    return self._trades


def db_ledger_cashflow_list_for_account(
    self, account_id, through_report_date_local=None, instrument_ids=None
):
    self.read_call_count += 1
    self.cashflow_instrument_ids = instrument_ids
    return self._cashflows


def db_ledger_corporate_action_list_for_account(
    self, account_id, through_report_date_local, instrument_ids=None
):
    self.read_call_count += 1
    self.corp_action_instrument_ids = instrument_ids
    return self._corporate_actions


def db_ledger_open_position_valuation_list_for_run(
    self, account_id, ingestion_run_id, instrument_ids=None
):
    self.read_call_count += 1
    self.open_position_instrument_ids = instrument_ids
    return self._valuations


def db_ledger_fx_rate_list_for_account(
    self, account_id, through_report_date_local, currencies=None
):
    self.read_call_count += 1
    self.fx_currencies = currencies
    return self._fx_rates


def db_position_lot_reconcile_open(
    self, account_id, closed_at_utc, requests, instrument_ids=None
):
    self.reconciled_instrument_ids = instrument_ids
    self.reconcile_call_count += 1
    self.position_requests.requests = requests


def test_snapshot_build_limits_reads_and_writes_to_resolved_scope() -> None:
    repository = _RepositoryStub(
        trades=[],
        valuations=[],
        scope_ids=[
            "00000000-0000-0000-0000-000000000010",
            "00000000-0000-0000-0000-000000000020",
        ],
        instrument_currencies=["GBP", "USD"],
    )
    service = StockLedgerSnapshotService(repository=repository)

    service.ledger_snapshot_build_and_persist(
        account_id="U1",
        ingestion_run_id="00000000-0000-0000-0000-000000000001",
        report_date_local="2026-08-21",
        affected_conids=frozenset({"100"}),
        affected_currencies=frozenset({"EUR"}),
    )

    expected = (
        "00000000-0000-0000-0000-000000000010",
        "00000000-0000-0000-0000-000000000020",
    )
    assert repository.trade_instrument_ids == expected
    assert repository.cashflow_instrument_ids == expected
    assert repository.corp_action_instrument_ids == expected
    assert repository.open_position_instrument_ids == expected
    assert repository.reconciled_instrument_ids == expected
    assert repository.fx_currencies == ("EUR", "GBP", "USD")
    assert all(request.instrument_id in expected for request in repository.snapshot_requests)


def test_snapshot_build_empty_scope_is_noop() -> None:
    repository = _RepositoryStub(trades=[], valuations=[], scope_ids=[])
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U1", "00000000-0000-0000-0000-000000000001", "2026-08-21", frozenset(), frozenset()
    )
    assert result.snapshot_row_count == 0
    assert repository.read_call_count == 0


def test_snapshot_build_none_scope_retains_full_reads() -> None:
    repository = _RepositoryStub(trades=[], valuations=[])
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U1", "00000000-0000-0000-0000-000000000001", "2026-08-21"
    )
    assert repository.trade_instrument_ids is None
```

- [ ] **Step 2: Run strict snapshot tests and verify signature failure**

Run: `pytest -q tests/test_ledger_snapshot_service_strict.py -k 'resolved_scope or empty_scope or none_scope'`

Expected: FAIL because scoped arguments and repository methods are missing.

- [ ] **Step 3: Extend the repository protocols and SQL filters**

Add the exact signatures from the Interfaces block. For each event query, append the following only when `instrument_ids is not None`:

```python
if instrument_ids is not None:
    statement = statement.where(event_table.c.instrument_id.in_(instrument_ids))
```

Resolve scope with a single instrument query:

```sql
SELECT instrument_id
FROM instrument
WHERE account_id = :account_id
  AND (conid = ANY(:conids) OR currency = ANY(:currencies))
ORDER BY instrument_id
```

Read the selected instruments' currencies with:

```sql
SELECT DISTINCT currency
FROM instrument
WHERE instrument_id = ANY(CAST(:instrument_ids AS uuid[]))
ORDER BY currency
```

For conversion-rate reads, add `event_fx.currency = ANY(:currencies)` when currencies are supplied. For lot reconciliation, add `position_lot.instrument_id = ANY(:instrument_ids)` to both the close-existing and replacement scope.

- [ ] **Step 4: Implement the service full/scoped branch**

```python
is_full_build = affected_conids is None and affected_currencies is None
if not is_full_build and not affected_conids and not affected_currencies:
    return SnapshotBuildResult(
        report_date_local=normalized_report_date,
        snapshot_row_count=0,
        position_lot_row_count=0,
        missing_solid_valuation_count=0,
    )

instrument_ids: tuple[str, ...] | None = None
if not is_full_build:
    instrument_ids = tuple(self._repository.db_ledger_instrument_ids_for_scope(
        account_id=account_id,
        conids=tuple(sorted(affected_conids or ())),
        currencies=tuple(sorted(affected_currencies or ())),
    ))
    if not instrument_ids:
        return SnapshotBuildResult(
            report_date_local=normalized_report_date,
            snapshot_row_count=0,
            position_lot_row_count=0,
            missing_solid_valuation_count=0,
        )
```

Pass `instrument_ids` to trade, cashflow, corporate-action, open-position, and reconciliation calls; pass sorted affected currencies to the FX call. Keep every existing calculation unchanged and filter snapshot UPSERT requests to `instrument_ids` before persistence.

Build the exact scoped FX currency union only after the scoped trade and cashflow reads:

```python
fx_currencies: tuple[str, ...] | None = None
if instrument_ids is not None:
    required_currencies = set(affected_currencies or ())
    required_currencies.update(self._repository.db_ledger_instrument_currency_list(instrument_ids))
    required_currencies.update(row.currency for row in trade_rows)
    required_currencies.update(row.functional_currency for row in trade_rows)
    required_currencies.update(row.currency for row in cashflow_rows)
    required_currencies.update(row.functional_currency for row in cashflow_rows)
    fx_currencies = tuple(sorted(required_currencies))
```

Pass `fx_currencies` to the FX read. In full mode pass `None`, preserving the existing complete-history query.

- [ ] **Step 5: Run ledger repository and snapshot suites**

Run: `pytest -q tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py`

Expected: PASS and unrelated instrument requests never reach persistence during a scoped build.

- [ ] **Step 6: Commit scoped snapshots**

```bash
git add app/db/interfaces.py app/db/ledger_snapshot.py app/ledger/snapshot_service.py tests/test_ledger_snapshot_service_strict.py
git commit -m "perf: scope ledger snapshot rebuilds"
```

---

### Task 6: Ingestion Short-circuit, Delta Integration, and Orchestrator Timings

**Files:**
- Modify: `app/jobs/ingestion_orchestrator.py`
- Modify: `tests/test_jobs_ingestion_orchestrator.py`

**Interfaces:**
- Consumes: `db_raw_record_list_changed_for_run` from Task 2, batch pipeline from Task 3, `job_build_incremental_snapshot_scope` from Task 4, and scoped snapshot call from Task 5.
- Produces exact-duplicate skip diagnostics: `raw_persistence_skip_reason="exact_duplicate_artifact"`, `canonical_skip_reason="exact_duplicate_artifact"`, `snapshot_skip_reason="exact_duplicate_artifact"`.
- Produces integer duration fields: `preflight_duration_ms`, `xml_extraction_duration_ms`, `artifact_persistence_duration_ms`, `raw_persistence_duration_ms`, `canonical_raw_read_duration_ms`, `canonical_duration_ms`, and `snapshot_duration_ms`.
- Produces `snapshot_scope_mode` equal to `incremental`, `full_fallback`, or `skipped`, plus `snapshot_full_rebuild_reason` when fallback occurs.

- [ ] **Step 1: Add an exact-duplicate call-suppression test**

```python
def _raw_row(section_name: str, source_payload: dict[str, str]) -> RawRecordForCanonicalMapping:
    run_id = uuid4()
    return RawRecordForCanonicalMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=run_id,
        account_id="U1",
        period_key="2026-08",
        flex_query_id="query",
        report_date_local=date(2026, 8, 21),
        section_name=section_name,
        source_row_ref=f"{section_name}:row-1",
        source_payload=source_payload,
    )


def _build_orchestrator(
    ingestion_repository: _RepositoryStub,
    raw: _RawPersistenceStub | None = None,
    canonical: _CanonicalRepositoryStub | None = None,
    snapshot: _SnapshotServiceStub | None = None,
) -> IngestionJobOrchestrator:
    payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    return IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        raw_persistence_repository=raw or _RawPersistenceStub(),
        flex_adapter=_AdapterStub(payload),
        config=IngestionOrchestratorConfig(account_id="U1", flex_query_id="query"),
        canonical_repository=canonical or _CanonicalRepositoryStub(),
        snapshot_service=snapshot or _SnapshotServiceStub(),
    )


def _completed_stage_details(repository: _RepositoryStub) -> dict[str, dict[str, object]]:
    diagnostics = repository.finalize_calls[-1]["diagnostics"]
    assert isinstance(diagnostics, list)
    return {
        str(event["stage"]): event["details"]
        for event in diagnostics
        if event.get("status") == "completed" and isinstance(event.get("details"), dict)
    }


def test_exact_duplicate_skips_raw_canonical_and_snapshot_work() -> None:
    repository = _RepositoryStub()
    raw = _RawPersistenceStub(artifact_deduplicated=True)
    canonical = _CanonicalRepositoryStub()
    snapshot = _SnapshotServiceStub()
    orchestrator = _build_orchestrator(repository, raw=raw, canonical=canonical, snapshot=snapshot)

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    assert raw.raw_insert_calls == 0
    assert canonical.changed_read_calls == 0
    assert canonical.bulk_upsert_calls == 0
    assert snapshot.build_calls == 0
    details = _completed_stage_details(repository)
    assert details["persist"]["raw_persistence_skip_reason"] == "exact_duplicate_artifact"
    assert details["canonical_mapping"]["canonical_skip_reason"] == "exact_duplicate_artifact"
    assert details["snapshot"]["snapshot_skip_reason"] == "exact_duplicate_artifact"
```

- [ ] **Step 2: Add delta-scope and unsafe-fallback tests**

```python
def test_distinct_artifact_reads_changed_rows_and_passes_incremental_scope() -> None:
    repository = _RepositoryStub()
    canonical = _CanonicalRepositoryStub(changed_rows=[
        _raw_row("Trades", {"conid": "100"}),
        _raw_row("ConversionRates", {"fromCurrency": "EUR"}),
    ])
    snapshot = _SnapshotServiceStub()
    _build_orchestrator(repository, canonical=canonical, snapshot=snapshot).job_execute("ingestion_run")
    assert canonical.changed_read_calls == 1
    assert snapshot.affected_conids == frozenset({"100"})
    assert snapshot.affected_currencies == frozenset({"EUR"})
    assert _completed_stage_details(repository)["snapshot"]["snapshot_scope_mode"] == "incremental"


def test_unscopable_changed_row_falls_back_to_full_snapshot() -> None:
    repository = _RepositoryStub()
    canonical = _CanonicalRepositoryStub(changed_rows=[_raw_row("Trades", {"symbol": "AAA"})])
    snapshot = _SnapshotServiceStub()
    _build_orchestrator(repository, canonical=canonical, snapshot=snapshot).job_execute("ingestion_run")
    assert snapshot.affected_conids is None
    assert snapshot.affected_currencies is None
    details = _completed_stage_details(repository)["snapshot"]
    assert details["snapshot_scope_mode"] == "full_fallback"
    assert details["snapshot_full_rebuild_reason"] == "unscopable_changed_row:Trades:missing_conid"
```

- [ ] **Step 3: Add one diagnostics-contract test covering all duration fields**

```python
def test_ingestion_completed_stages_include_all_monotonic_durations() -> None:
    repository = _RepositoryStub()
    _build_orchestrator(repository).job_execute("ingestion_run")
    details = _completed_stage_details(repository)
    expected = {
        "preflight": "preflight_duration_ms",
        "xml_extraction": "xml_extraction_duration_ms",
        "persist": "raw_persistence_duration_ms",
        "canonical_mapping": "canonical_duration_ms",
        "snapshot": "snapshot_duration_ms",
    }
    for stage, key in expected.items():
        assert isinstance(details[stage][key], int)
        assert details[stage][key] >= 0
    assert details["persist"]["artifact_persistence_duration_ms"] >= 0
    assert details["canonical_mapping"]["canonical_raw_read_duration_ms"] >= 0
```

- [ ] **Step 4: Run focused orchestrator tests and verify failures**

Run: `pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'exact_duplicate or incremental_scope or full_snapshot or monotonic_durations'`

Expected: FAIL because the orchestrator still inserts duplicate raw rows, calls the all-row read, performs a full snapshot, and uses wall-clock stage timing.

- [ ] **Step 5: Implement exact-duplicate short-circuit and delta selection**

```python
if artifact_result.deduplicated:
    raw_record_result = RawRecordPersistResult(
        inserted_count=0,
        deduplicated_count=len(extracted_rows),
    )
    duplicate_skip_reason = "exact_duplicate_artifact"
else:
    raw_record_result = self._raw_persistence_repository.db_raw_record_insert_many(raw_requests)
    duplicate_skip_reason = None

if duplicate_skip_reason is None:
    raw_read_started_ns = perf_counter_ns()
    canonical_raw_rows = self._canonical_repository.db_raw_record_list_changed_for_run(ingestion_run_id)
    canonical_raw_read_duration_ms = _duration_ms(raw_read_started_ns)
else:
    canonical_raw_rows = []
    canonical_raw_read_duration_ms = 0
```

Do not construct `raw_requests` until after the artifact result is known. Emit completed raw, canonical, and snapshot events even when skipped so the API timeline remains complete.

- [ ] **Step 6: Integrate incremental/full-fallback snapshot calls**

```python
scope = job_build_incremental_snapshot_scope(canonical_raw_rows)
if scope.full_rebuild_reason is not None:
    snapshot_result = self._snapshot_service.ledger_snapshot_build_and_persist(
        account_id, ingestion_run_id, report_date_local
    )
    snapshot_scope_mode = "full_fallback"
elif not scope.conids and not scope.currencies:
    snapshot_result = SnapshotBuildResult(
        report_date_local=report_date_local,
        snapshot_row_count=0,
        position_lot_row_count=0,
        missing_solid_valuation_count=0,
    )
    snapshot_scope_mode = "skipped"
else:
    snapshot_result = self._snapshot_service.ledger_snapshot_build_and_persist(
        account_id,
        ingestion_run_id,
        report_date_local,
        affected_conids=scope.conids,
        affected_currencies=scope.currencies,
    )
    snapshot_scope_mode = "incremental"
```

For exact duplicates, do not call the scope builder or snapshot service; use `snapshot_scope_mode="skipped"` and the exact duplicate reason.

- [ ] **Step 7: Replace stage wall-clock subtraction with a monotonic helper**

```python
from time import perf_counter_ns


def _duration_ms(started_ns: int) -> int:
    return max(0, (perf_counter_ns() - started_ns) // 1_000_000)
```

Start a separate counter immediately before preflight, XML extraction, artifact UPSERT, raw insert, changed-row read, mapping/persistence, and snapshot build. Retain the ingestion-run UTC timestamps for persisted start/end metadata, but never subtract them for the new duration fields.

- [ ] **Step 8: Run orchestrator and reprocess suites**

Run: `pytest -q tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_reprocess.py`

Expected: PASS; reprocess continues to call the existing complete-period raw read and full canonical pipeline.

- [ ] **Step 9: Commit orchestrator integration**

```bash
git add app/jobs/ingestion_orchestrator.py tests/test_jobs_ingestion_orchestrator.py
git commit -m "perf: skip duplicate and unchanged ingestion work"
```

---

### Task 7: End-to-end Regression, Documentation, and Operational Verification

**Files:**
- Modify: `tests/test_end_to_end_seeded.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: complete Tasks 1–6.
- Produces: end-to-end guarantees for exact duplicates, distinct raw retention, changed-only canonical work, scoped snapshots, and documented diagnostics.

- [ ] **Step 1: Extend the seeded PostgreSQL scenario with duplicate and corrected-artifact assertions**

```python
class _SeededAdapter:
    def __init__(self, payload_bytes: bytes = _SEEDED_PAYLOAD) -> None:
        self.payload_bytes = payload_bytes

    def adapter_source_name(self) -> str:
        return "seeded-test"

    def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
        assert query_id == "seeded-query"
        return AdapterFetchResult(
            run_reference="seeded-report",
            payload_bytes=self.payload_bytes,
            stage_timeline=[{"stage": "request", "status": "completed"}],
        )


# In test_seeded_ingestion_reaches_reports_reconciliation_and_provenance(),
# retain one adapter instance when constructing the orchestrator.
seeded_adapter = _SeededAdapter()
# Pass flex_adapter=seeded_adapter to IngestionJobOrchestrator.
first_result = orchestrator.job_execute("ingestion_run")
duplicate_result = orchestrator.job_execute("ingestion_run")
seeded_adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="111"')
corrected_result = orchestrator.job_execute("ingestion_run")
assert [first_result.status, duplicate_result.status, corrected_result.status] == ["success"] * 3

with engine.connect() as connection:
    runs = connection.execute(text(
        "SELECT ingestion_run_id, diagnostics FROM ingestion_run "
        "WHERE account_id='SEEDED_ACCOUNT' ORDER BY started_at_utc, ingestion_run_id"
    )).mappings().all()
    raw_counts = [
        connection.execute(text(
            "SELECT count(*) FROM raw_record WHERE ingestion_run_id=:run_id"
        ), {"run_id": run["ingestion_run_id"]}).scalar_one()
        for run in runs
    ]
    assert connection.execute(text("SELECT count(*) FROM raw_artifact")).scalar_one() == 2
    assert raw_counts[0] > 0
    assert raw_counts[1] == 0
    assert raw_counts[2] == raw_counts[0]
    corrected_price = connection.execute(text(
        "SELECT price FROM event_trade_fill WHERE account_id='SEEDED_ACCOUNT' AND ib_exec_id='SEED-EXEC-1'"
    )).scalar_one()
    assert corrected_price == Decimal("111")

def completed_details(run, stage: str) -> dict[str, object]:
    return next(
        event["details"] for event in run["diagnostics"]
        if event["stage"] == stage and event["status"] == "completed"
    )

assert completed_details(runs[1], "canonical_mapping")["canonical_skip_reason"] == "exact_duplicate_artifact"
assert completed_details(runs[2], "canonical_mapping")["canonical_input_row_count"] == 1
assert completed_details(runs[2], "snapshot")["snapshot_scope_mode"] == "incremental"
```

- [ ] **Step 2: Run the seeded test and adjust only its existing fixture helpers where required**

Run: `pytest -q tests/test_end_to_end_seeded.py -k duplicate_skips_semantic_work`

Expected before fixture adjustment: FAIL at the first missing helper or assertion; after adding the concrete fixture parameters/count queries shown above: PASS.

- [ ] **Step 3: Document observable behavior and diagnostic field names**

Add this concise section to `README.md`:

```markdown
### Incremental ingestion diagnostics

Normal ingestion keeps every distinct Flex artifact and its raw rows. An exact
duplicate artifact completes successfully while skipping raw-row insertion,
canonical mapping, and snapshot rebuilding. A distinct artifact canonicalizes
only rows changed from their immediately preceding source version and rebuilds
snapshots only for affected instruments and FX source currencies. Explicit
reprocess commands remain full replays.

Run-detail diagnostics include request transport, polling, cumulative poll wait,
preflight, XML extraction, artifact persistence, raw persistence, canonical raw
read, canonical mapping/persistence, snapshot, and total run durations in integer
milliseconds. Skipped and full-fallback stages include their reason.
```

- [ ] **Step 4: Run static checks and the full automated suite**

Run:

```bash
ruff check .
ruff format --check .
mypy app tests
python -m pip check
pytest -q
```

Expected: all commands exit zero.

- [ ] **Step 5: Verify migration upgrade and live service health against the preserved database**

Run:

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /tmp/stock_app_before_20260821_04.dump
docker compose up -d --build
docker compose exec -T app alembic current
docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
docker compose ps
```

Expected: Alembic reports `20260821_04 (head)`, health returns success, and app/database containers are healthy. The dump at `/tmp/stock_app_before_20260821_04.dump` is the rollback artifact.

- [ ] **Step 6: Run an isolated before/after behavioral benchmark**

Use the existing restored database copy and the ingestion benchmark harness from the investigation. Record, without asserting thresholds:

```text
distinct artifact: total_ms, raw_inserted_count, canonical_input_row_count,
instrument_batch_calls, snapshot_scope_count
exact duplicate: total_ms, raw_inserted_count=0, canonical_input_row_count=0,
snapshot_upserted_count=0
```

Expected: a distinct artifact retains every extracted raw row, instruments use one batch call, and an exact duplicate performs none of the three expensive semantic stages.

- [ ] **Step 7: Commit the final verification slice**

```bash
git add tests/test_end_to_end_seeded.py README.md
git commit -m "test: verify incremental ingestion end to end"
```

- [ ] **Step 8: Review the branch and push only after verification**

Run:

```bash
git status --short
git log --oneline origin/main..HEAD
git push origin main
```

Expected: worktree is clean before the push and the remote accepts all implementation commits.
