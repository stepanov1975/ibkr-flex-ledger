# Broker Position Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make daily snapshot quantities agree with completed IBKR OpenPositions data while preserving an auditable event-derived ledger and deterministically repairing the active database from immutable raw artifacts.

**Architecture:** Canonical mapping accepts execution-level IBKR trades with stable fallback identities and maps OpenPositions instruments before snapshot construction. The snapshot service computes FIFO lots independently, then reconciles its day-level quantity and valuation against broker OpenPositions facts; deterministic reprocess selects one successful artifact per report date, rebuilds snapshots chronologically, and performs narrowly scoped legacy-date cleanup only after replay succeeds.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy Core, PostgreSQL 17, Alembic, pytest, Ruff, MyPy, Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-21-broker-position-reconciliation-design.md`

## Global Constraints

- Preserve immutable `raw_artifact` and `raw_record` rows unchanged.
- Keep `position_lot` event-derived; never invent synthetic trades or lots to force agreement.
- Store fallback trade identities in the existing `event_trade_fill.ib_exec_id` column as `FLEX_TXN:<transactionID>` or `FLEX_TRADE:<tradeID>`; add no schema migration for trade identity.
- Apply fallback identity only to `Trades:Trade` rows with `levelOfDetail=EXECUTION`; continue excluding Order, Lot, and SymbolSummary rows.
- Treat a completed artifact's OpenPositions section as authoritative for supported non-cash end-of-day quantities, including absence as zero.
- Admit non-cash OpenPositions categories, including `STK` and `OPT`; exclude `CASH` and `FX` pseudo-instruments.
- Keep optional blank broker valuation fields unavailable; reject malformed nonblank numeric values.
- Mark quantity mismatches and broker-only rows provisional while retaining reconstructable realized P&L.
- Preserve normal incremental scope behavior and exact-duplicate semantic skips.
- Select replay artifacts deterministically by actual `report_date_local`, then newest `created_at_utc` and UUID.
- Run unsupported-date cleanup only for an explicit account/period/query reprocess target and only after every selected artifact replays successfully.
- Keep canonical UPSERTs retry-safe when a replay attempt fails before completion.
- Back up and verify the active PostgreSQL database before live snapshot cleanup or repair.
- Add no third-party packages and do not issue a new live IBKR Flex request during repair.

---

## File Map

- `app/mapping/service.py`: fallback execution identity, OpenPositions instrument mapping, and numeric contract validation.
- `app/db/interfaces.py`: expanded broker-position facts, replay artifact records, cleanup records, and repository port methods.
- `app/db/__init__.py`: public exports for the new records.
- `app/db/canonical_persistence.py`: successful replay-artifact discovery and full-artifact raw reads.
- `app/db/ledger_snapshot.py`: non-cash OpenPositions reads plus scoped unsupported-snapshot discovery/deletion.
- `app/ledger/snapshot_service.py`: FIFO/broker reconciliation, broker-only snapshots, explicit functional currency, and counters.
- `app/jobs/ingestion_orchestrator.py`: functional-currency forwarding and snapshot diagnostics.
- `app/jobs/reprocess_orchestrator.py`: deterministic artifact selection, chronological mapping/snapshot replay, failure behavior, and post-success cleanup.
- `app/bootstrap.py`: supply snapshot persistence to reprocess and retain the configured functional currency.
- `app/main.py`: route explicitly scoped CLI reprocess calls through the cleanup-capable method.
- `tests/test_mapping_canonical_pipeline.py`: fallback identity and OpenPositions mapping contract tests.
- `tests/test_db_ledger_snapshot.py`: expanded broker-position and cleanup query tests.
- `tests/test_db_canonical_upsert.py`: replay-artifact eligibility and ordering tests.
- `tests/test_ledger_snapshot_service_strict.py`: broker authority, broker-only rows, option multiplier, FX, and diagnostics tests.
- `tests/test_jobs_ingestion_orchestrator.py`: explicit currency and diagnostic propagation tests.
- `tests/test_jobs_reprocess.py`: selection, chronological replay, failure, lineage, and cleanup ordering tests.
- `tests/test_end_to_end_seeded.py`: PostgreSQL ALEX regression, immutable replay, cleanup scope, and idempotence tests.
- `README.md`: user-visible reconciliation and reprocess behavior.
- `docs/operations.md`: backup, repair, and verification runbook.

The mapper, snapshot service, and replay workflow are coupled parts of one correctness boundary: fallback events repair reconstructable lots, broker authority covers unresolved history, and replay applies both to stored data. They remain one plan so no intermediate release can rewrite history without also reconciling snapshots.

---

### Task 1: Canonical Fallback Identities and OpenPositions Instruments

**Files:**
- Modify: `app/mapping/service.py:95-299`
- Test: `tests/test_mapping_canonical_pipeline.py`

**Interfaces:**
- Consumes: existing `RawRecordForMapping` and `CanonicalInstrumentUpsertRequest`.
- Produces: `CanonicalMappingService._mapping_resolve_trade_identity(raw_record: RawRecordForMapping) -> str | None`.
- Produces: `CanonicalMappingService._mapping_map_open_position_instrument(raw_record: RawRecordForMapping, account_id: str) -> CanonicalInstrumentUpsertRequest | None`.
- Preserves: nonblank `ibExecID` values exactly as supplied.

- [ ] **Step 1: Replace the old blank-`ibExecID` skip test with execution-aware identity tests**

```python
def _execution_trade(**overrides: object) -> RawRecordForMapping:
    payload: dict[str, object] = {
        "levelOfDetail": "EXECUTION",
        "transactionID": "37400900364",
        "tradeID": "9921",
        "conid": "265598",
        "buySell": "BUY",
        "quantity": "10",
        "tradePrice": "101.00",
        "currency": "USD",
        "reportDate": "2026-02-14",
        "dateTime": "2026-02-14T10:00:00+00:00",
    }
    payload.update(overrides)
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="Trades",
        source_row_ref="Trades:Trade:transactionID=37400900364",
        report_date_local=date(2026, 2, 14),
        source_payload=payload,
    )


def test_mapping_uses_transaction_id_for_execution_without_ib_exec_id() -> None:
    batch = mapping_build_canonical_batch("U_TEST", "USD", [_execution_trade()])
    assert batch.trade_fill_requests[0].ib_exec_id == "FLEX_TXN:37400900364"


def test_mapping_uses_trade_id_when_execution_transaction_id_is_blank() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_execution_trade(transactionID="")]
    )
    assert batch.trade_fill_requests[0].ib_exec_id == "FLEX_TRADE:9921"


def test_mapping_rejects_execution_without_any_stable_identity() -> None:
    with pytest.raises(MappingContractViolationError, match="stable execution identity"):
        mapping_build_canonical_batch(
            "U_TEST",
            "USD",
            [_execution_trade(transactionID="", tradeID="")],
        )


@pytest.mark.parametrize("row_tag", ["Order", "Lot", "SymbolSummary"])
def test_mapping_excludes_non_trade_rows_even_with_execution_fields(row_tag: str) -> None:
    row = replace(
        _execution_trade(),
        source_row_ref=f"Trades:{row_tag}:transactionID=37400900364",
    )
    batch = mapping_build_canonical_batch("U_TEST", "USD", [row])
    assert batch.trade_fill_requests == ()
```

Keep one test proving a `Trades:Trade` row without `levelOfDetail=EXECUTION` and without `ibExecID` is skipped, preserving compatibility with non-execution summary rows.

- [ ] **Step 2: Run the fallback identity tests and verify the old skip behavior fails**

Run: `pytest -q tests/test_mapping_canonical_pipeline.py -k 'transaction_id_for_execution or trade_id_when_execution or stable_identity or excludes_non_trade'`

Expected: FAIL because execution rows with blank `ibExecID` are currently skipped.

- [ ] **Step 3: Implement the minimum identity resolver and pass its result into trade mapping**

```python
def _mapping_resolve_trade_identity(
    self,
    raw_record: RawRecordForMapping,
) -> str | None:
    payload = raw_record.source_payload
    ib_exec_id = self._mapping_optional_value(payload, "ibExecID")
    if ib_exec_id is not None:
        return ib_exec_id
    level_of_detail = self._mapping_optional_value(payload, "levelOfDetail")
    if level_of_detail is None or level_of_detail.upper() != "EXECUTION":
        return None
    transaction_id = self._mapping_optional_value(payload, "transactionID")
    if transaction_id is not None:
        return f"FLEX_TXN:{transaction_id}"
    trade_id = self._mapping_optional_value(payload, "tradeID")
    if trade_id is not None:
        return f"FLEX_TRADE:{trade_id}"
    raise MappingContractViolationError(
        "mapping contract violation: execution row missing stable execution identity "
        f"source_row_ref={raw_record.source_row_ref}"
    )
```

Change `_mapping_map_trade_record` to accept `trade_identity: str` and assign it to `CanonicalTradeFillUpsertRequest.ib_exec_id`; do not reread `ibExecID` inside that method.

- [ ] **Step 4: Add OpenPositions instrument and validation tests**

```python
def _open_position(**overrides: object) -> RawRecordForMapping:
    payload: dict[str, object] = {
        "conid": "815232555",
        "symbol": "ALEX  260821P00010000",
        "assetCategory": "OPT",
        "currency": "USD",
        "position": "-2",
        "markPrice": "0.05",
        "costBasisMoney": "-120",
        "fifoPnlUnrealized": "110",
        "fxRateToBase": "1",
        "multiplier": "100",
        "reportDate": "20260820",
    }
    payload.update(overrides)
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="OpenPositions",
        source_row_ref="OpenPositions:OpenPosition:idx=1",
        report_date_local=date(2026, 8, 20),
        source_payload=payload,
    )


def test_mapping_open_position_upserts_option_instrument() -> None:
    batch = mapping_build_canonical_batch("U_TEST", "USD", [_open_position()])
    assert len(batch.instrument_upsert_requests) == 1
    assert batch.instrument_upsert_requests[0].conid == "815232555"
    assert batch.instrument_upsert_requests[0].asset_category == "OPT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position", ""),
        ("position", "invalid"),
        ("markPrice", "invalid"),
        ("costBasisMoney", "invalid"),
        ("fifoPnlUnrealized", "invalid"),
        ("fxRateToBase", "invalid"),
        ("multiplier", "invalid"),
    ],
)
def test_mapping_open_position_rejects_invalid_numeric_contract(
    field: str,
    value: str,
) -> None:
    with pytest.raises(MappingContractViolationError):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_open_position(**{field: value})]
        )


def test_mapping_open_position_allows_blank_optional_values() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST",
        "USD",
        [_open_position(markPrice="", costBasisMoney="", fifoPnlUnrealized="", fxRateToBase="", multiplier="")],
    )
    assert len(batch.instrument_upsert_requests) == 1


@pytest.mark.parametrize("asset_category", ["CASH", "FX"])
def test_mapping_excludes_cash_and_fx_open_positions(asset_category: str) -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_open_position(assetCategory=asset_category)]
    )
    assert batch.instrument_upsert_requests == ()
```

- [ ] **Step 5: Run the OpenPositions tests and verify they fail**

Run: `pytest -q tests/test_mapping_canonical_pipeline.py -k open_position`

Expected: FAIL because OpenPositions rows are not mapped or validated.

- [ ] **Step 6: Implement OpenPositions routing and validation**

Route only `OpenPositions:OpenPosition` rows and implement the mapper with this contract:

```python
def _mapping_map_open_position_instrument(
    self,
    raw_record: RawRecordForMapping,
    account_id: str,
) -> CanonicalInstrumentUpsertRequest | None:
    payload = raw_record.source_payload
    asset_category = self._mapping_required_value(
        payload, "assetCategory", raw_record
    ).upper()
    if asset_category in {"CASH", "FX"}:
        return None
    conid = self._mapping_required_value(payload, "conid", raw_record)
    currency = self._mapping_required_value(payload, "currency", raw_record).upper()
    self._mapping_required_decimal_value(payload, "position", raw_record)
    for optional_key in (
        "markPrice",
        "costBasisMoney",
        "fifoPnlUnrealized",
        "fxRateToBase",
        "multiplier",
    ):
        self._mapping_optional_decimal_value(payload, optional_key, raw_record)
    for positive_key in ("fxRateToBase", "multiplier"):
        parsed_value = self._mapping_optional_decimal_value(
            payload, positive_key, raw_record
        )
        if parsed_value is not None and Decimal(parsed_value) <= Decimal("0"):
            raise MappingContractViolationError(
                f"mapping contract violation: {positive_key} must be positive "
                f"source_row_ref={raw_record.source_row_ref}"
            )
    return CanonicalInstrumentUpsertRequest(
        account_id=account_id,
        conid=conid,
        symbol=self._mapping_optional_value(payload, "symbol") or conid,
        local_symbol=self._mapping_optional_value(payload, "localSymbol"),
        isin=self._mapping_optional_value(payload, "isin"),
        cusip=self._mapping_optional_value(payload, "cusip"),
        figi=self._mapping_optional_value(payload, "figi"),
        asset_category=asset_category,
        currency=currency,
        description=self._mapping_optional_value(payload, "description"),
    )
```

- [ ] **Step 7: Run the complete mapping suite**

Run: `pytest -q tests/test_mapping_canonical_pipeline.py tests/test_jobs_canonical_pipeline.py`

Expected: PASS.

- [ ] **Step 8: Commit the canonical mapping slice**

```bash
git add app/mapping/service.py tests/test_mapping_canonical_pipeline.py
git commit -m "fix: map broker execution and position identities"
```

---

### Task 2: Persisted Broker Position Read Contract

**Files:**
- Modify: `app/db/interfaces.py:830-847,974-1050`
- Modify: `app/db/__init__.py`
- Modify: `app/db/ledger_snapshot.py:309-392`
- Test: `tests/test_db_ledger_snapshot.py`

**Interfaces:**
- Produces the expanded immutable value object:

```python
@dataclass(frozen=True)
class LedgerOpenPositionValuationRecord:
    instrument_id: UUID
    asset_category: str
    currency: str
    position_qty: str
    mark_price: str | None
    cost_basis_money: str | None
    broker_unrealized_pnl: str | None
    fx_rate_to_base: str | None
    multiplier: str | None
    report_date_local: date | None
```

- Preserves: `LedgerSnapshotRepositoryPort.db_ledger_open_position_valuation_list_for_run(account_id, ingestion_run_id, instrument_ids=None)`.
- Guarantees: at most one row per instrument, selected deterministically by newest raw record ID within the artifact owner run.

- [ ] **Step 1: Add a repository mapping test for stock and option records**

```python
def test_open_position_read_includes_option_cost_fx_and_multiplier() -> None:
    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "instrument_id": instrument_id,
        "asset_category": "OPT",
        "currency": "USD",
        "position_qty": Decimal("-1"),
        "mark_price": Decimal("2.21"),
        "cost_basis_money": Decimal("-28"),
        "broker_unrealized_pnl": Decimal("-193"),
        "fx_rate_to_base": Decimal("1"),
        "multiplier": Decimal("100"),
        "report_date_local": date(2026, 8, 20),
    }])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    rows = repository.db_ledger_open_position_valuation_list_for_run(
        "U1", str(uuid4())
    )

    assert rows == [LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        asset_category="OPT",
        currency="USD",
        position_qty="-1",
        mark_price="2.21",
        cost_basis_money="-28",
        broker_unrealized_pnl="-193",
        fx_rate_to_base="1",
        multiplier="100",
        report_date_local=date(2026, 8, 20),
    )]
    query = connection.executed_queries[0]
    assert "assetCategory" in query
    assert "costBasisMoney" in query
    assert "fxRateToBase" in query
    assert "multiplier" in query
    assert "assetCategory', '') = 'STK'" not in query


def test_open_position_read_preserves_blank_optional_values_as_none() -> None:
    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "instrument_id": instrument_id,
        "asset_category": "STK",
        "currency": "USD",
        "position_qty": Decimal("5"),
        "mark_price": None,
        "cost_basis_money": None,
        "broker_unrealized_pnl": None,
        "fx_rate_to_base": None,
        "multiplier": None,
        "report_date_local": date(2026, 8, 20),
    }])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))
    row = repository.db_ledger_open_position_valuation_list_for_run(
        "U1", str(uuid4())
    )[0]
    assert row.mark_price is None
    assert row.cost_basis_money is None
    assert row.broker_unrealized_pnl is None
    assert row.fx_rate_to_base is None
    assert row.multiplier is None
```

- [ ] **Step 2: Run the focused repository test and verify the dataclass contract fails**

Run: `pytest -q tests/test_db_ledger_snapshot.py -k open_position`

Expected: FAIL because the current record lacks asset category, currency, cost, FX, and multiplier.

- [ ] **Step 3: Expand the typed record and SQL projection**

Use this parsed projection, preserve the existing optional `instrument_ids` predicate, and rank rows per instrument by `raw_record_id DESC`:

```sql
SELECT
    i.instrument_id,
    UPPER(rr.source_payload->>'assetCategory') AS asset_category,
    UPPER(rr.source_payload->>'currency') AS currency,
    (rr.source_payload->>'position')::numeric AS position_qty,
    NULLIF(rr.source_payload->>'markPrice', '')::numeric AS mark_price,
    NULLIF(rr.source_payload->>'costBasisMoney', '')::numeric AS cost_basis_money,
    NULLIF(rr.source_payload->>'fifoPnlUnrealized', '')::numeric AS broker_unrealized_pnl,
    NULLIF(rr.source_payload->>'fxRateToBase', '')::numeric AS fx_rate_to_base,
    NULLIF(rr.source_payload->>'multiplier', '')::numeric AS multiplier
FROM raw_record rr
JOIN instrument i
  ON i.account_id = rr.account_id
 AND i.conid = rr.source_payload->>'conid'
WHERE rr.account_id = :account_id
  AND rr.ingestion_run_id = CAST(:ingestion_run_id AS uuid)
  AND rr.section_name = 'OpenPositions'
  AND rr.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
  AND UPPER(rr.source_payload->>'assetCategory') NOT IN ('CASH', 'FX')
```

- [ ] **Step 4: Run repository and type checks**

Run: `pytest -q tests/test_db_ledger_snapshot.py`

Run: `mypy app/db/interfaces.py app/db/ledger_snapshot.py`

Expected: both commands PASS.

- [ ] **Step 5: Commit the broker-position read contract**

```bash
git add app/db/interfaces.py app/db/__init__.py app/db/ledger_snapshot.py tests/test_db_ledger_snapshot.py
git commit -m "feat: expose complete broker position facts"
```

---

### Task 3: Broker-Authoritative Snapshot Reconciliation

**Files:**
- Modify: `app/ledger/snapshot_service.py:24-591`
- Test: `tests/test_ledger_snapshot_service_strict.py`

**Interfaces:**
- Changes: `StockLedgerSnapshotService.ledger_snapshot_build_and_persist(account_id: str, ingestion_run_id: str | None, report_date_local: str, functional_currency: str, affected_conids: frozenset[str] | None = None, affected_currencies: frozenset[str] | None = None) -> SnapshotBuildResult`.
- Produces new `SnapshotBuildResult` integer fields with zero defaults: `broker_position_match_count`, `broker_position_mismatch_count`, `broker_only_position_count`, and `broker_absent_nonzero_fifo_count`.
- Preserves: event-derived `PositionLotUpsertRequest` output regardless of broker mismatch.

The four broker counters are disjoint: match and mismatch require both broker and canonical history, broker-only requires no canonical trade/cashflow history, and broker-absent requires nonzero FIFO quantity. An absent broker row with zero FIFO quantity increments none of them.

- [ ] **Step 1: Add compact trade and broker-record factories to the strict service test**

```python
def _trade(instrument_id: UUID, side: str, quantity: str, price: str) -> LedgerTradeFillRecord:
    return LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
        report_date_local=date(2026, 8, 20),
        side=side,
        quantity=quantity,
        price=price,
        fees="0",
        commission="0",
        functional_currency="USD",
        currency="USD",
    )


def _broker_position(
    instrument_id: UUID,
    position: str,
    *,
    asset_category: str = "STK",
    currency: str = "USD",
    mark: str | None = "12",
    cost: str | None = "1000",
    unrealized: str | None = "200",
    fx: str | None = "1",
    multiplier: str | None = "1",
) -> LedgerOpenPositionValuationRecord:
    return LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        asset_category=asset_category,
        currency=currency,
        position_qty=position,
        mark_price=mark,
        cost_basis_money=cost,
        broker_unrealized_pnl=unrealized,
        fx_rate_to_base=fx,
        multiplier=multiplier,
        report_date_local=date(2026, 8, 20),
    )
```

- [ ] **Step 2: Add failing authority and provisional-state tests**

```python
def test_snapshot_uses_broker_quantity_and_cost_when_fifo_mismatches() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "200", "11")],
        valuations=[_broker_position(instrument_id, "0", cost=None, unrealized="0")],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.cost_basis is None
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.provisional is True
    assert result.broker_position_mismatch_count == 1


def test_snapshot_treats_broker_absence_as_zero_without_synthetic_lot() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "200", "11")],
        valuations=[],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].position_qty == "0"
    assert repository.snapshot_requests.requests[0].provisional is True
    assert len(repository.position_requests.requests) == 1
    assert result.broker_absent_nonzero_fifo_count == 1


def test_snapshot_creates_broker_only_option_with_contract_valuation() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "-1",
            asset_category="OPT",
            mark="2.21",
            cost="-28",
            unrealized="-193",
            multiplier="100",
        )],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "-1"
    assert snapshot.cost_basis == "-28"
    assert snapshot.realized_pnl == "0"
    assert snapshot.unrealized_pnl == "-193"
    assert snapshot.provisional is True
    assert repository.position_requests.requests == []
    assert result.broker_only_position_count == 1
```

- [ ] **Step 3: Add match, optional-valuation, FX, and cashflow-only tests**

```python
def test_snapshot_exact_match_keeps_fifo_cost_and_uses_broker_unrealized() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "BUY", "10", "10")],
        valuations=[_broker_position(
            instrument_id, "10", mark="12", cost="100", unrealized="200"
        )],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "10"
    assert snapshot.cost_basis == "100"
    assert snapshot.unrealized_pnl == "200"
    assert snapshot.provisional is False
    assert result.broker_position_match_count == 1


def test_snapshot_preserves_missing_optional_broker_values() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id, "5", mark=None, cost=None, unrealized=None
        )],
    )
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "5"
    assert snapshot.cost_basis is None
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.provisional is True


def test_snapshot_converts_broker_cost_and_unrealized_to_functional_currency() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "10",
            currency="EUR",
            cost="100",
            unrealized="20",
            fx="1.2",
        )],
    )
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.cost_basis == "120.0"
    assert snapshot.unrealized_pnl == "24.0"


def test_snapshot_includes_instrument_cashflow_without_trade_history() -> None:
    instrument_id = uuid4()
    cashflow = LedgerCashflowRecord(
        event_cashflow_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        report_date_local=date(2026, 8, 20),
        withholding_tax="0",
        fees="0",
        functional_currency="USD",
        amount="25",
        amount_in_base="25",
        currency="USD",
    )
    repository = _RepositoryStub(trades=[], valuations=[], cashflows=[cashflow])
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.realized_pnl == "25"


def test_snapshot_option_mark_fallback_applies_contract_multiplier() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "-1",
            asset_category="OPT",
            mark="2.21",
            cost="-28",
            unrealized=None,
            multiplier="100",
        )],
    )
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].unrealized_pnl == "-193.00"
```

- [ ] **Step 4: Run the new reconciliation tests and verify they fail**

Run: `pytest -q tests/test_ledger_snapshot_service_strict.py -k 'broker or cashflow_only or contract_valuation'`

Expected: FAIL because the service currently loops only over trade instruments and writes FIFO quantity.

- [ ] **Step 5: Refactor the service around one union of instrument IDs**

Build `instrument_keys` as the sorted union of trade, instrument-linked cashflow, and OpenPositions keys. For each key:

1. Compute FIFO and reconstructable realized P&L from canonical rows.
2. Keep its open-lot requests unchanged for `position_lot` reconciliation.
3. Compare FIFO quantity with the broker row, treating broker absence as zero.
4. On exact match, keep FIFO cost and realized P&L; prefer broker unrealized converted to functional currency, then fall back to broker mark/multiplier market value minus FIFO cost.
5. On mismatch, write broker quantity, converted broker cost and unrealized values, reconstructable realized P&L, and `provisional=True`.
6. For broker-only rows, use zero realized P&L unless canonical cashflow evidence exists and emit no lot.
7. When required cost/mark/FX data is unavailable, preserve `None` cost, use zero only for the non-null P&L database columns, and set provisional.

Use the explicit `functional_currency` argument for every snapshot row and for all FX resolution. Prefer a positive OpenPositions `fxRateToBase`; otherwise use the existing exact/previous ConversionRates fallback.

- [ ] **Step 6: Return all four reconciliation counters**

```python
return SnapshotBuildResult(
    report_date_local=normalized_report_date,
    snapshot_row_count=len(snapshot_requests),
    position_lot_row_count=len(position_lot_requests),
    missing_solid_valuation_count=missing_solid_valuation_count,
    broker_position_match_count=broker_position_match_count,
    broker_position_mismatch_count=broker_position_mismatch_count,
    broker_only_position_count=broker_only_position_count,
    broker_absent_nonzero_fifo_count=broker_absent_nonzero_fifo_count,
)
```

- [ ] **Step 7: Run the full FIFO and snapshot unit suites**

Run: `pytest -q tests/test_ledger_fifo_snapshot.py tests/test_ledger_snapshot_service_strict.py`

Expected: PASS with all existing lot, fee, FX, corporate-action, scoped-build, and no-op behavior intact.

- [ ] **Step 8: Commit the reconciliation engine**

```bash
git add app/ledger/snapshot_service.py tests/test_ledger_snapshot_service_strict.py
git commit -m "fix: reconcile snapshots to broker positions"
```

---

### Task 4: Normal Ingestion Currency and Diagnostics Integration

**Files:**
- Modify: `app/jobs/ingestion_orchestrator.py:486-597`
- Test: `tests/test_jobs_ingestion_orchestrator.py`

**Interfaces:**
- Consumes: the Task 3 `functional_currency` parameter and expanded `SnapshotBuildResult`.
- Produces snapshot timeline fields: `broker_position_match_count`, `broker_position_mismatch_count`, `broker_only_position_count`, and `broker_absent_nonzero_fifo_count`.
- Preserves: duplicate skip, empty incremental scope, full fallback, and artifact-owner lineage behavior.

- [ ] **Step 1: Expand the snapshot service stub and expected result**

```python
def ledger_snapshot_build_and_persist(
    self,
    account_id: str,
    ingestion_run_id: str | None,
    report_date_local: str,
    functional_currency: str,
    affected_conids: frozenset[str] | None = None,
    affected_currencies: frozenset[str] | None = None,
) -> SnapshotBuildResult:
    self.calls.append({
        "account_id": account_id,
        "ingestion_run_id": ingestion_run_id,
        "report_date_local": report_date_local,
        "functional_currency": functional_currency,
        "affected_conids": affected_conids,
        "affected_currencies": affected_currencies,
    })
    return SnapshotBuildResult(
        report_date_local=report_date_local,
        snapshot_row_count=4,
        position_lot_row_count=2,
        missing_solid_valuation_count=1,
        broker_position_match_count=2,
        broker_position_mismatch_count=1,
        broker_only_position_count=1,
        broker_absent_nonzero_fifo_count=1,
    )
```

- [ ] **Step 2: Assert currency forwarding and diagnostics**

```python
assert snapshot_service_stub.calls[0]["functional_currency"] == "USD"
details = _completed_stage_details(repository_stub)["snapshot"]
assert details["broker_position_match_count"] == 2
assert details["broker_position_mismatch_count"] == 1
assert details["broker_only_position_count"] == 1
assert details["broker_absent_nonzero_fifo_count"] == 1
```

- [ ] **Step 3: Run the focused tests and verify signature/diagnostic failures**

Run: `pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'snapshot_stage_on_success or incremental or duplicate'`

Expected: FAIL until every real and zero-result snapshot path supplies the expanded contract.

- [ ] **Step 4: Forward configured currency and serialize every counter**

Pass `functional_currency=self._config.functional_currency` on full and incremental service calls. Include the four counter keys in `snapshot_details`; construct skipped `SnapshotBuildResult` values with zero counters, relying on their dataclass defaults.

- [ ] **Step 5: Run the complete ingestion orchestrator suite**

Run: `pytest -q tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_incremental_scope.py`

Expected: PASS.

- [ ] **Step 6: Commit normal-ingestion integration**

```bash
git add app/jobs/ingestion_orchestrator.py tests/test_jobs_ingestion_orchestrator.py
git commit -m "feat: report broker snapshot reconciliation"
```

---

### Task 5: Replay Artifact and Snapshot Cleanup Repository Contracts

**Files:**
- Modify: `app/db/interfaces.py:135-153,568-637,974-1139`
- Modify: `app/db/__init__.py`
- Modify: `app/db/canonical_persistence.py:28-186`
- Modify: `app/db/ledger_snapshot.py:625-768`
- Test: `tests/test_db_canonical_upsert.py`
- Test: `tests/test_db_ledger_snapshot.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class RawArtifactReplayCandidate:
    raw_artifact_id: UUID
    ingestion_run_id: UUID
    report_date_local: date
    created_at_utc: datetime
    open_positions_present: bool


@dataclass(frozen=True)
class SnapshotCleanupCandidate:
    report_date_local: date
    row_count: int
```

- Produces: `RawRecordReadRepositoryPort.db_raw_artifact_replay_candidate_list(account_id: str, period_key: str, flex_query_id: str) -> list[RawArtifactReplayCandidate]`.
- Produces: `LedgerSnapshotRepositoryPort.db_pnl_snapshot_daily_unsupported_list(account_id: str, period_key: str, flex_query_id: str, supported_report_dates: tuple[str, ...]) -> list[SnapshotCleanupCandidate]`.
- Produces: `LedgerSnapshotRepositoryPort.db_pnl_snapshot_daily_unsupported_delete(account_id: str, period_key: str, flex_query_id: str, supported_report_dates: tuple[str, ...]) -> int`.

- [ ] **Step 1: Add replay-candidate query tests**

```python
class _ReplayReadResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _ReplayReadResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _ReplayReadConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.executed_queries: list[str] = []

    def __enter__(self) -> _ReplayReadConnection:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def execute(self, statement: object, parameters: object) -> _ReplayReadResult:
        self.executed_queries.append(str(statement))
        return _ReplayReadResult(self._rows)


class _ReplayReadEngine:
    def __init__(self, connection: _ReplayReadConnection) -> None:
        self._connection = connection

    def connect(self) -> _ReplayReadConnection:
        return self._connection


def test_replay_candidate_query_returns_successful_artifacts_in_source_order() -> None:
    older_artifact_id = uuid.uuid4()
    older_owner_id = uuid.uuid4()
    newer_artifact_id = uuid.uuid4()
    newer_owner_id = uuid.uuid4()
    connection = _ReplayReadConnection([
        {
            "raw_artifact_id": older_artifact_id,
            "ingestion_run_id": older_owner_id,
            "report_date_local": date(2026, 2, 19),
            "created_at_utc": datetime(2026, 2, 20, tzinfo=timezone.utc),
            "open_positions_present": True,
        },
        {
            "raw_artifact_id": newer_artifact_id,
            "ingestion_run_id": newer_owner_id,
            "report_date_local": date(2026, 8, 20),
            "created_at_utc": datetime(2026, 8, 21, tzinfo=timezone.utc),
            "open_positions_present": True,
        },
    ])
    repository = SQLAlchemyCanonicalPersistenceService(_ReplayReadEngine(connection))

    candidates = repository.db_raw_artifact_replay_candidate_list(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
    )

    assert candidates == [
        RawArtifactReplayCandidate(
            raw_artifact_id=older_artifact_id,
            ingestion_run_id=older_owner_id,
            report_date_local=date(2026, 2, 19),
            created_at_utc=datetime(2026, 2, 20, tzinfo=timezone.utc),
            open_positions_present=True,
        ),
        RawArtifactReplayCandidate(
            raw_artifact_id=newer_artifact_id,
            ingestion_run_id=newer_owner_id,
            report_date_local=date(2026, 8, 20),
            created_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
            open_positions_present=True,
        ),
    ]
    query = connection.executed_queries[0]
    assert "completed_ingestion_run_id" in query
    assert "completion.status = 'success'" in query
    assert "owner.status = 'success'" in query
    assert "section_name = 'OpenPositions'" in query
```

The SQL eligibility rule must be exact: a non-null completion pointer qualifies only when its completion run is `success`; a null legacy pointer qualifies only when the immutable owner run is `success`.

- [ ] **Step 2: Run the replay-candidate test and verify the port is absent**

Run: `pytest -q tests/test_db_canonical_upsert.py -k replay_candidate`

Expected: FAIL with missing dataclass or repository method.

- [ ] **Step 3: Implement deterministic candidate discovery**

Use this query and map each row to `RawArtifactReplayCandidate`:

```sql
SELECT
    artifact.raw_artifact_id,
    artifact.ingestion_run_id,
    artifact.report_date_local,
    artifact.created_at_utc,
    EXISTS (
        SELECT 1
        FROM raw_record position_row
        WHERE position_row.raw_artifact_id = artifact.raw_artifact_id
          AND position_row.section_name = 'OpenPositions'
    ) AS open_positions_present
FROM raw_artifact artifact
JOIN ingestion_run owner
  ON owner.ingestion_run_id = artifact.ingestion_run_id
LEFT JOIN ingestion_run completion
  ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
WHERE artifact.account_id = :account_id
  AND artifact.period_key = :period_key
  AND artifact.flex_query_id = :flex_query_id
  AND artifact.report_date_local IS NOT NULL
  AND (
      (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
      OR
      (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
  )
ORDER BY artifact.report_date_local ASC,
         artifact.created_at_utc ASC,
         artifact.raw_artifact_id ASC
```

- [ ] **Step 4: Add scoped cleanup list/delete tests**

```python
def test_unsupported_snapshot_cleanup_is_account_period_query_scoped() -> None:
    connection = _ConnectionStub(rows=[{
        "report_date_local": date(2026, 2, 21),
        "row_count": 44,
    }])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    candidates = repository.db_pnl_snapshot_daily_unsupported_list(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
        supported_report_dates=("2026-02-19",),
    )

    assert candidates == [SnapshotCleanupCandidate(date(2026, 2, 21), 44)]
    query = connection.executed_queries[0]
    assert "raw_artifact" in query
    assert "account_id = :account_id" in query
    assert "period_key = :period_key" in query
    assert "flex_query_id = :flex_query_id" in query
    assert "supported_report_dates" in query
```

Extend the existing stubs with a configurable `rowcount` and add the delete assertion:

```python
class _ResultStub:
    def __init__(self, rows: list[dict], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount


class _ConnectionStub:
    def __init__(self, rows: list[dict] | None = None, rowcount: int = 0) -> None:
        self.rows = rows or []
        self.rowcount = rowcount
        self.executed_queries: list[str] = []
        self.executed_parameters: list[object] = []

    def execute(self, statement, parameters=None) -> _ResultStub:
        self.executed_queries.append(str(statement))
        self.executed_parameters.append(parameters)
        return _ResultStub(self.rows, rowcount=self.rowcount)


def test_unsupported_snapshot_delete_returns_scoped_row_count() -> None:
    connection = _ConnectionStub(rowcount=44)
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))
    deleted = repository.db_pnl_snapshot_daily_unsupported_delete(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
        supported_report_dates=("2026-02-19",),
    )
    assert deleted == 44
    assert "DELETE FROM pnl_snapshot_daily" in connection.executed_queries[0]
```

- [ ] **Step 5: Run cleanup tests and verify the methods are absent**

Run: `pytest -q tests/test_db_ledger_snapshot.py -k unsupported_snapshot_cleanup`

Expected: FAIL with missing cleanup methods.

- [ ] **Step 6: Implement list and delete with identical scope predicates**

Reject an empty supported-date tuple so an upstream selection bug can never delete every scoped snapshot. Use this CTE unchanged in both the grouped list query and delete query:

```sql
WITH scoped_owner_runs AS (
    SELECT DISTINCT artifact.ingestion_run_id
    FROM raw_artifact artifact
    WHERE artifact.account_id = :account_id
      AND artifact.period_key = :period_key
      AND artifact.flex_query_id = :flex_query_id
)
```

The list query appends:

```sql
SELECT snapshot.report_date_local, count(*) AS row_count
FROM pnl_snapshot_daily snapshot
WHERE snapshot.account_id = :account_id
  AND snapshot.ingestion_run_id IN (SELECT ingestion_run_id FROM scoped_owner_runs)
  AND NOT (snapshot.report_date_local = ANY(CAST(:supported_report_dates AS date[])))
GROUP BY snapshot.report_date_local
ORDER BY snapshot.report_date_local
```

The delete query appends the same three predicates to `DELETE FROM pnl_snapshot_daily snapshot` and returns SQLAlchemy's integer `rowcount`.

- [ ] **Step 7: Run repository tests and MyPy**

Run: `pytest -q tests/test_db_canonical_upsert.py tests/test_db_ledger_snapshot.py`

Run: `mypy app/db/interfaces.py app/db/canonical_persistence.py app/db/ledger_snapshot.py`

Expected: both commands PASS.

- [ ] **Step 8: Commit replay data access**

```bash
git add app/db/interfaces.py app/db/__init__.py app/db/canonical_persistence.py app/db/ledger_snapshot.py tests/test_db_canonical_upsert.py tests/test_db_ledger_snapshot.py
git commit -m "feat: add deterministic replay data access"
```

---

### Task 6: Chronological Snapshot Reprocess and Safe Cleanup

**Files:**
- Modify: `app/jobs/reprocess_orchestrator.py:17-275`
- Modify: `app/bootstrap.py:39-164`
- Modify: `app/main.py:64-80`
- Test: `tests/test_jobs_reprocess.py`

**Interfaces:**
- Consumes: `RawArtifactReplayCandidate`, full-artifact reads, `StockLedgerSnapshotService`, and the two cleanup repository methods.
- Produces: `job_select_replay_artifacts(candidates: list[RawArtifactReplayCandidate]) -> tuple[RawArtifactReplayCandidate, ...]`.
- Changes constructor: add required `snapshot_service: StockLedgerSnapshotService` and `snapshot_repository: LedgerSnapshotRepositoryPort` dependencies.
- Changes internal execution: `_job_reprocess_execute_with_config(config, allow_unsupported_snapshot_cleanup: bool)`.
- Preserves: `job_execute_reprocess_target(period_key, flex_query_id)` and the existing reprocess error-code mapping.

- [ ] **Step 1: Add pure deterministic selection tests**

```python
def _candidate(
    report_date: date,
    created_at: datetime,
    artifact_id: UUID,
    *,
    open_positions_present: bool = True,
) -> RawArtifactReplayCandidate:
    return RawArtifactReplayCandidate(
        raw_artifact_id=artifact_id,
        ingestion_run_id=uuid4(),
        report_date_local=report_date,
        created_at_utc=created_at,
        open_positions_present=open_positions_present,
    )


def test_reprocess_selects_newest_artifact_per_actual_report_date() -> None:
    report_date = date(2026, 2, 19)
    older = _candidate(report_date, datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=1))
    newer_low_uuid = _candidate(report_date, datetime(2026, 2, 21, tzinfo=timezone.utc), UUID(int=2))
    newer_high_uuid = _candidate(report_date, datetime(2026, 2, 21, tzinfo=timezone.utc), UUID(int=3))
    later_date = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=4))

    selected = job_select_replay_artifacts([
        later_date, newer_low_uuid, older, newer_high_uuid
    ])

    assert selected == (newer_high_uuid, later_date)


def test_reprocess_rejects_selected_artifact_without_open_positions() -> None:
    candidate = _candidate(
        date(2026, 8, 20),
        datetime(2026, 8, 21, tzinfo=timezone.utc),
        UUID(int=5),
        open_positions_present=False,
    )
    with pytest.raises(ValueError, match="OpenPositions"):
        job_select_replay_artifacts([candidate])


def test_reprocess_selection_is_empty_without_candidates() -> None:
    assert job_select_replay_artifacts([]) == ()
```

- [ ] **Step 2: Run selection tests and verify the helper is missing**

Run: `pytest -q tests/test_jobs_reprocess.py -k select`

Expected: FAIL because deterministic artifact selection does not exist.

- [ ] **Step 3: Implement the selector**

```python
def job_select_replay_artifacts(
    candidates: list[RawArtifactReplayCandidate],
) -> tuple[RawArtifactReplayCandidate, ...]:
    selected_by_date: dict[date, RawArtifactReplayCandidate] = {}
    for candidate in candidates:
        current = selected_by_date.get(candidate.report_date_local)
        if current is None or (
            candidate.created_at_utc,
            candidate.raw_artifact_id,
        ) > (
            current.created_at_utc,
            current.raw_artifact_id,
        ):
            selected_by_date[candidate.report_date_local] = candidate
    selected = tuple(
        sorted(
            selected_by_date.values(),
            key=lambda item: (
                item.report_date_local,
                item.created_at_utc,
                item.raw_artifact_id,
            ),
        )
    )
    missing = [item.raw_artifact_id for item in selected if not item.open_positions_present]
    if missing:
        raise ValueError(f"selected artifacts missing OpenPositions section: {missing}")
    return selected
```

- [ ] **Step 4: Replace period-wide replay tests with artifact-by-artifact orchestration tests**

```python
class _ArtifactRawRepository:
    def __init__(
        self,
        candidates: list[RawArtifactReplayCandidate],
        rows_by_artifact: dict[UUID, list[RawRecordForMapping]],
        operation_log: list[tuple[object, ...]],
    ) -> None:
        self.candidates = candidates
        self.rows_by_artifact = rows_by_artifact
        self.operation_log = operation_log

    def db_raw_artifact_replay_candidate_list(
        self, account_id: str, period_key: str, flex_query_id: str
    ) -> list[RawArtifactReplayCandidate]:
        return self.candidates

    def db_raw_record_list_for_artifact(
        self, raw_artifact_id: UUID
    ) -> list[RawRecordForMapping]:
        self.operation_log.append(("read_artifact", raw_artifact_id))
        return self.rows_by_artifact[raw_artifact_id]


class _SnapshotServiceStub:
    def __init__(
        self,
        operation_log: list[tuple[object, ...]],
        fail_on_call: int | None = None,
    ) -> None:
        self.operation_log = operation_log
        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, object]] = []

    def ledger_snapshot_build_and_persist(
        self,
        account_id: str,
        ingestion_run_id: str | None,
        report_date_local: str,
        functional_currency: str,
        affected_conids: frozenset[str] | None = None,
        affected_currencies: frozenset[str] | None = None,
    ) -> SnapshotBuildResult:
        self.calls.append({
            "account_id": account_id,
            "ingestion_run_id": ingestion_run_id,
            "report_date_local": report_date_local,
            "functional_currency": functional_currency,
        })
        candidate_date = date.fromisoformat(report_date_local)
        self.operation_log.append(("snapshot", candidate_date, UUID(ingestion_run_id or "")))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("snapshot failure")
        return SnapshotBuildResult(report_date_local, 1, 0, 0)


class _CleanupRepositoryStub:
    def __init__(self, operation_log: list[tuple[object, ...]]) -> None:
        self.operation_log = operation_log
        self.delete_calls: list[tuple[str, ...]] = []

    def db_pnl_snapshot_daily_unsupported_list(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> list[SnapshotCleanupCandidate]:
        self.operation_log.append(("list_cleanup", supported_report_dates))
        return [SnapshotCleanupCandidate(date(2026, 2, 21), 44)]

    def db_pnl_snapshot_daily_unsupported_delete(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> int:
        self.delete_calls.append(supported_report_dates)
        self.operation_log.append(("delete_cleanup", supported_report_dates))
        return 44


def test_reprocess_maps_and_snapshots_selected_artifacts_chronologically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_log: list[tuple[object, ...]] = []
    first = _candidate(date(2026, 2, 19), datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=1))
    second = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=2))
    rows_by_artifact = {
        first.raw_artifact_id: [_trade_row(first.ingestion_run_id, "first")],
        second.raw_artifact_id: [_trade_row(second.ingestion_run_id, "second")],
    }

    def capture_map(**kwargs: object) -> dict[str, int]:
        raw_rows = kwargs["raw_records"]
        assert isinstance(raw_rows, list)
        artifact_label = raw_rows[0].source_payload["artifact_label"]
        operation_log.append(("map", artifact_label))
        return {"instrument_upsert_count": 1, "trade_fill_count": 1,
                "cashflow_count": 0, "fx_count": 0, "corp_action_count": 0}

    monkeypatch.setattr(reprocess_module, "job_canonical_map_and_persist", capture_map)
    raw_repository = _ArtifactRawRepository(
        [second, first], rows_by_artifact, operation_log
    )
    snapshot_service = _SnapshotServiceStub(operation_log)
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    orchestrator = CanonicalReprocessOrchestrator(
        raw_read_repository=raw_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=snapshot_service,
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=_IngestionRepositoryStub(),
    )

    result = orchestrator.job_execute_reprocess_target("2026-02-20", "query")

    supported_dates = ("2026-02-19", "2026-08-20")
    assert result.status == "success"
    assert operation_log == [
        ("read_artifact", first.raw_artifact_id),
        ("map", "first"),
        ("snapshot", first.report_date_local, first.ingestion_run_id),
        ("read_artifact", second.raw_artifact_id),
        ("map", "second"),
        ("snapshot", second.report_date_local, second.ingestion_run_id),
        ("list_cleanup", supported_dates),
        ("delete_cleanup", supported_dates),
    ]
    assert [call["functional_currency"] for call in snapshot_service.calls] == ["USD", "USD"]
```

Define the raw-row helper next to the existing fixtures:

```python
def _trade_row(owner_run_id: UUID, artifact_label: str) -> RawRecordForMapping:
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=owner_run_id,
        section_name="Trades",
        source_row_ref=f"Trades:Trade:transactionID={artifact_label}",
        report_date_local=date(2026, 8, 20),
        source_payload={
            "artifact_label": artifact_label,
            "ibExecID": f"EXEC-{artifact_label}",
            "transactionID": artifact_label,
            "levelOfDetail": "EXECUTION",
            "conid": "265598",
            "buySell": "BUY",
            "quantity": "1",
            "tradePrice": "10",
            "currency": "USD",
            "reportDate": "20260820",
            "dateTime": "2026-08-20T10:00:00+00:00",
        },
    )
```

- [ ] **Step 5: Add failure and cleanup guards**

```python
def test_reprocess_failure_never_deletes_unsupported_snapshots() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log, fail_on_snapshot_call=2)
    result = harness.orchestrator.job_execute_reprocess_target("2026-02-20", "query")
    assert result.status == "failed"
    assert harness.cleanup_repository.delete_calls == []


def test_default_reprocess_does_not_cleanup_unsupported_dates() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log)
    result = harness.orchestrator.job_execute("reprocess_run")
    assert result.status == "success"
    assert harness.cleanup_repository.delete_calls == []
```

Add the shared harness and the diagnostic assertion:

```python
@dataclass(frozen=True)
class _ReprocessHarness:
    orchestrator: CanonicalReprocessOrchestrator
    cleanup_repository: _CleanupRepositoryStub
    ingestion_repository: _IngestionRepositoryStub


def _build_reprocess_harness(
    operation_log: list[tuple[object, ...]],
    fail_on_snapshot_call: int | None = None,
) -> _ReprocessHarness:
    first = _candidate(date(2026, 2, 19), datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=11))
    second = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=12))
    raw_repository = _ArtifactRawRepository(
        [first, second],
        {
            first.raw_artifact_id: [_trade_row(first.ingestion_run_id, "first")],
            second.raw_artifact_id: [_trade_row(second.ingestion_run_id, "second")],
        },
        operation_log,
    )
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    ingestion_repository = _IngestionRepositoryStub()
    orchestrator = CanonicalReprocessOrchestrator(
        raw_read_repository=raw_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log, fail_on_snapshot_call),
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=ingestion_repository,
    )
    return _ReprocessHarness(orchestrator, cleanup_repository, ingestion_repository)


def test_reprocess_records_cleanup_candidates_before_deleted_count() -> None:
    harness = _build_reprocess_harness([])
    result = harness.orchestrator.job_execute_reprocess_target("2026-02-20", "query")
    assert result.status == "success"
    diagnostics = harness.ingestion_repository.finalize_calls[0]["diagnostics"]
    cleanup_events = [event for event in diagnostics if event["stage"] == "snapshot_cleanup"]
    assert cleanup_events[0]["details"]["candidates"] == [
        {"report_date_local": "2026-02-21", "row_count": 44}
    ]
    assert cleanup_events[1]["details"]["deleted_row_count"] == 44
```

- [ ] **Step 6: Run the reprocess tests and verify the current period-wide mapper fails**

Run: `pytest -q tests/test_jobs_reprocess.py`

Expected: FAIL until replay reads and snapshots each selected artifact and defers cleanup.

- [ ] **Step 7: Implement chronological replay**

For each selected candidate:

```python
raw_rows = self._raw_read_repository.db_raw_record_list_for_artifact(
    raw_artifact_id=candidate.raw_artifact_id,
)
canonical_counts = job_canonical_map_and_persist(
    account_id=config.account_id,
    functional_currency=config.functional_currency,
    raw_records=raw_rows,
    canonical_persistence_repository=self._canonical_persistence_repository,
)
snapshot_result = self._snapshot_service.ledger_snapshot_build_and_persist(
    account_id=config.account_id,
    ingestion_run_id=str(candidate.ingestion_run_id),
    report_date_local=candidate.report_date_local.isoformat(),
    functional_currency=config.functional_currency,
)
```

Accumulate per-artifact counts in diagnostics. If any read, map, or snapshot raises, finalize the run as failed and skip both cleanup calls. After all artifacts succeed, list unsupported snapshots, append their exact dates/counts to the timeline, and delete only when `allow_unsupported_snapshot_cleanup=True`.

- [ ] **Step 8: Wire repositories and explicit CLI cleanup semantics**

Create one `SQLAlchemyLedgerSnapshotService` and `StockLedgerSnapshotService` in each bootstrap path and pass both to `CanonicalReprocessOrchestrator`. In `app/main.py`, when both reprocess flags are supplied, call:

```python
execution_result = reprocess_orchestrator.job_execute_reprocess_target(
    period_key=parsed_arguments.period_key,
    flex_query_id=parsed_arguments.flex_query_id,
)
```

Otherwise retain `job_execute("reprocess_run")`, which does not clean unsupported dates.

- [ ] **Step 9: Run orchestration, bootstrap, CLI, and API regression suites**

Run: `pytest -q tests/test_jobs_reprocess.py tests/test_jobs_ingestion_orchestrator.py tests/test_api_ingestion.py tests/test_api_health.py`

Expected: PASS.

- [ ] **Step 10: Commit replay orchestration**

```bash
git add app/jobs/reprocess_orchestrator.py app/bootstrap.py app/main.py tests/test_jobs_reprocess.py
git commit -m "feat: rebuild snapshots during canonical replay"
```

---

### Task 7: PostgreSQL Regression Coverage and Operations Documentation

**Files:**
- Modify: `tests/test_end_to_end_seeded.py`
- Modify: `README.md`
- Modify: `docs/operations.md`

**Interfaces:**
- Verifies: fallback BookTrade identities, ALEX-style lot closure, broker-only snapshots, chronological replay, immutable raw counts, scoped cleanup, and idempotent second replay.
- Documents: broker authority, provisional meaning, explicit reprocess cleanup, backup requirement, and diagnostic counters.

- [ ] **Step 1: Add an ALEX-style seeded ingestion regression**

Use a complete Flex payload containing these four execution-level `Trade` rows:

```xml
<Trade levelOfDetail="EXECUTION" ibExecID="OPEN-OPT" transactionID="1"
       conid="815232555" symbol="ALEX  260821P00010000" assetCategory="OPT"
       currency="USD" buySell="SELL" quantity="-2" tradePrice="0.60"
       reportDate="20260820" dateTime="20260820;100000" />
<Trade levelOfDetail="EXECUTION" transactionID="2" tradeID="2002"
       conid="815232555" symbol="ALEX  260821P00010000" assetCategory="OPT"
       currency="USD" buySell="BUY" quantity="2" tradePrice="0"
       reportDate="20260820" dateTime="20260820;110000" />
<Trade levelOfDetail="EXECUTION" transactionID="3" tradeID="2003"
       conid="108670127" symbol="ALEX" assetCategory="STK"
       currency="USD" buySell="BUY" quantity="200" tradePrice="10"
       reportDate="20260820" dateTime="20260820;110001" />
<Trade levelOfDetail="EXECUTION" ibExecID="CLOSE-STK" transactionID="4"
       conid="108670127" symbol="ALEX" assetCategory="STK"
       currency="USD" buySell="SELL" quantity="-200" tradePrice="11"
       reportDate="20260820" dateTime="20260820;120000" />
```

Include empty hard-required `OpenPositions`, `CashTransactions`, `CorporateActions`, `ConversionRates`, `SecuritiesInfo`, and `AccountInformation` sections. Run normal ingestion and assert:

```python
assert connection.execute(text(
    "SELECT count(*) FROM event_trade_fill WHERE account_id='U_ALEX'"
)).scalar_one() == 4
assert connection.execute(text(
    "SELECT count(*) FROM event_trade_fill WHERE account_id='U_ALEX' "
    "AND ib_exec_id IN ('FLEX_TXN:2', 'FLEX_TXN:3')"
)).scalar_one() == 2
assert connection.execute(text(
    "SELECT count(*) FROM position_lot WHERE account_id='U_ALEX' AND status='open'"
)).scalar_one() == 0
positions = connection.execute(text(
    "SELECT i.conid, s.position_qty FROM pnl_snapshot_daily s "
    "JOIN instrument i USING (instrument_id) WHERE s.account_id='U_ALEX'"
)).all()
assert set(positions) == {("815232555", Decimal("0")), ("108670127", Decimal("0"))}
```

- [ ] **Step 2: Add an end-to-end deterministic replay/cleanup test**

Seed two successful raw artifacts for one scope with different actual report dates, plus a legacy snapshot whose `ingestion_run_id` is an artifact owner but whose date is not one of those actual dates. Execute the explicit target twice and assert after both runs:

```python
assert raw_artifact_count_after == raw_artifact_count_before
assert raw_record_count_after == raw_record_count_before
assert canonical_trade_count_after_second == canonical_trade_count_after_first
assert snapshot_rows_after_second == snapshot_rows_after_first
assert unsupported_snapshot_count == 0
assert selected_snapshot_dates == {date(2026, 2, 19), date(2026, 8, 20)}
```

Seed a snapshot for a different account and assert its row and timestamp are unchanged, proving cleanup scope isolation.

- [ ] **Step 3: Run PostgreSQL end-to-end tests**

Run: `pytest -q tests/test_end_to_end_seeded.py -k 'alex or deterministic_replay_cleanup'`

Expected: PASS against the reachable disposable PostgreSQL databases created by the existing test helpers.

- [ ] **Step 4: Document the final behavior and operator workflow**

Update `README.md` to state:

- snapshot quantity comes from completed OpenPositions data;
- event-derived FIFO lots remain auditable and may temporarily disagree;
- provisional rows indicate quantity or valuation uncertainty;
- assignment/exercise BookTrade rows use namespaced stable identities;
- explicit reprocess rebuilds actual artifact dates without contacting IBKR.

Update `docs/operations.md` with the custom-format `pg_dump`, `pg_restore --list` verification, explicit per-scope reprocess commands, raw-count comparison, mismatch query, and rollback path.

- [ ] **Step 5: Run all automated quality gates**

Run: `pytest -q`

Run: `ruff check app/ tests/ --ignore=E501,W293,W291`

Run: `mypy`

Run: `mypy --strict tests/test_mapping_canonical_pipeline.py tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py tests/test_jobs_reprocess.py`

Run: `python -m pip check`

Run: `git diff --check`

Expected: every command PASS with zero warnings treated as failures by its configured policy.

- [ ] **Step 6: Commit documentation and end-to-end coverage**

```bash
git add tests/test_end_to_end_seeded.py README.md docs/operations.md
git commit -m "test: cover broker position replay repair"
```

---

### Task 8: Review, Back Up, Repair, and Verify the Active Database

**Files:**
- No source files modified.

**Interfaces:**
- Consumes: reviewed commits from Tasks 1-7 and the active Docker Compose PostgreSQL service.
- Produces: one verified custom-format dump, repaired canonical/snapshot state, and an evidence report containing before/after counts and remaining provisional reasons.

- [ ] **Step 1: Obtain a fresh code review before touching active data**

Use `superpowers:requesting-code-review` against the complete Task 1-7 diff. Resolve every correctness issue, rerun all Task 7 gates, and record the reviewed commit SHA.

- [ ] **Step 2: Create and verify a custom-format PostgreSQL dump**

Run:

```bash
repair_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T -e REPAIR_STAMP="$repair_stamp" postgres sh -eu -c '
dump="/backups/broker-position-repair-$REPAIR_STAMP.dump"
pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$dump"
pg_restore --list "$dump" >/dev/null
sha256sum "$dump" > "$dump.sha256"
printf "%s\n" "$dump"
'
```

Expected: a `/backups/broker-position-repair-<UTC timestamp>.dump` path and no `pg_dump` or `pg_restore` errors. Preserve that exact path and checksum in the handoff.

- [ ] **Step 3: Record immutable and derived before-state counts**

Run and retain this output:

```bash
docker compose exec -T postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT 'raw_artifact' AS relation, count(*) AS row_count FROM raw_artifact
UNION ALL SELECT 'raw_record', count(*) FROM raw_record
UNION ALL SELECT 'event_trade_fill', count(*) FROM event_trade_fill
UNION ALL SELECT 'position_lot', count(*) FROM position_lot
UNION ALL SELECT 'pnl_snapshot_daily', count(*) FROM pnl_snapshot_daily
ORDER BY relation;

SELECT md5(COALESCE(string_agg(
    concat_ws('|', account_id, report_date_local, instrument_id, position_qty,
              cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees,
              withholding_tax, currency, provisional, valuation_source,
              fx_source, ingestion_run_id),
    E'\n' ORDER BY account_id, report_date_local, instrument_id
), '')) AS snapshot_checksum
FROM pnl_snapshot_daily;

WITH eligible_artifact AS (
    SELECT artifact.raw_artifact_id, artifact.ingestion_run_id,
           artifact.account_id, artifact.period_key, artifact.flex_query_id,
           artifact.report_date_local, artifact.created_at_utc,
           row_number() OVER (
               PARTITION BY artifact.account_id, artifact.period_key,
                            artifact.flex_query_id, artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC,
                        artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
), selected_date AS (
    SELECT account_id, period_key, flex_query_id, report_date_local
    FROM eligible_artifact
    WHERE report_date_rank = 1
), scoped_owner AS (
    SELECT DISTINCT account_id, period_key, flex_query_id, ingestion_run_id
    FROM eligible_artifact
)
SELECT owner.period_key, owner.flex_query_id,
       snapshot.report_date_local AS unsupported_date,
       count(*) AS row_count
FROM pnl_snapshot_daily snapshot
JOIN scoped_owner owner
  ON owner.account_id = snapshot.account_id
 AND owner.ingestion_run_id = snapshot.ingestion_run_id
LEFT JOIN selected_date supported
  ON supported.account_id = owner.account_id
 AND supported.period_key = owner.period_key
 AND supported.flex_query_id = owner.flex_query_id
 AND supported.report_date_local = snapshot.report_date_local
WHERE supported.report_date_local IS NULL
GROUP BY owner.period_key, owner.flex_query_id, snapshot.report_date_local
ORDER BY owner.period_key, owner.flex_query_id, snapshot.report_date_local;
SQL
```

Store the five counts, checksum, and exact unsupported date/count rows in the task handoff before running reprocess.

- [ ] **Step 4: Deploy the reviewed code without contacting IBKR**

Run: `docker compose up -d --build app`

Run: `docker compose ps`

Run: `curl --fail --silent http://127.0.0.1:8000/health`

Expected: the app service is healthy and the health endpoint succeeds. Do not call `POST /ingestion/run`.

- [ ] **Step 5: Reprocess each stored active scope explicitly**

Run the known active scopes in chronological order:

```bash
docker compose exec -T app sh -eu -c 'python -m app.main reprocess-run --period-key 2026-02-20 --flex-query-id "$IBKR_FLEX_QUERY_ID"'
docker compose exec -T app sh -eu -c 'python -m app.main reprocess-run --period-key 2026-08-21 --flex-query-id "$IBKR_FLEX_QUERY_ID"'
```

Expected: both commands exit zero. Query the two new `reprocess` ingestion runs and retain their canonical, snapshot, selected-artifact, reconciliation-counter, and cleanup diagnostics.

- [ ] **Step 6: Verify immutable history and replay idempotence**

Rerun the Step 3 count query and require identical `raw_artifact` and `raw_record` counts. Record the new derived counts and snapshot checksum, rerun both explicit commands once, then run:

```sql
SELECT count(*) AS trade_rows,
       count(DISTINCT (account_id, ib_exec_id)) AS trade_natural_keys
FROM event_trade_fill;

SELECT md5(COALESCE(string_agg(
    concat_ws('|', account_id, report_date_local, instrument_id, position_qty,
              cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees,
              withholding_tax, currency, provisional, valuation_source,
              fx_source, ingestion_run_id),
    E'\n' ORDER BY account_id, report_date_local, instrument_id
), '')) AS snapshot_checksum
FROM pnl_snapshot_daily;
```

Require `trade_rows = trade_natural_keys` and require the second-replay snapshot checksum to equal the first-replay checksum.

- [ ] **Step 7: Verify broker quantity authority and ALEX repair**

Run:

```bash
docker compose exec -T postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
WITH latest_artifact AS (
    SELECT artifact.raw_artifact_id, artifact.account_id,
           artifact.report_date_local
    FROM raw_artifact artifact
    JOIN ingestion_run completed
      ON completed.ingestion_run_id = artifact.completed_ingestion_run_id
     AND completed.status = 'success'
    WHERE artifact.report_date_local IS NOT NULL
    ORDER BY artifact.report_date_local DESC,
             artifact.created_at_utc DESC,
             artifact.raw_artifact_id DESC
    LIMIT 1
), broker AS (
    SELECT instrument.instrument_id,
           (record.source_payload->>'position')::numeric AS position_qty
    FROM latest_artifact latest
    JOIN raw_record record
      ON record.raw_artifact_id = latest.raw_artifact_id
     AND record.section_name = 'OpenPositions'
     AND record.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
    JOIN instrument
      ON instrument.account_id = latest.account_id
     AND instrument.conid = record.source_payload->>'conid'
    WHERE UPPER(record.source_payload->>'assetCategory') NOT IN ('CASH', 'FX')
), snapshot AS (
    SELECT daily.instrument_id, daily.position_qty
    FROM latest_artifact latest
    JOIN pnl_snapshot_daily daily
      ON daily.account_id = latest.account_id
     AND daily.report_date_local = latest.report_date_local
)
SELECT 'broker_missing_snapshot' AS discrepancy, count(*) AS row_count
FROM broker LEFT JOIN snapshot USING (instrument_id)
WHERE snapshot.instrument_id IS NULL
UNION ALL
SELECT 'broker_quantity_mismatch', count(*)
FROM broker JOIN snapshot USING (instrument_id)
WHERE broker.position_qty IS DISTINCT FROM snapshot.position_qty
UNION ALL
SELECT 'nonzero_snapshot_absent_from_broker', count(*)
FROM snapshot
JOIN instrument USING (instrument_id)
LEFT JOIN broker USING (instrument_id)
WHERE broker.instrument_id IS NULL
  AND snapshot.position_qty <> 0
  AND UPPER(instrument.asset_category) NOT IN ('CASH', 'FX');

WITH latest_date AS (
    SELECT max(report_date_local) AS report_date_local FROM pnl_snapshot_daily
)
SELECT instrument.symbol, instrument.conid, daily.position_qty, daily.provisional,
       daily.valuation_source, daily.fx_source
FROM pnl_snapshot_daily daily
JOIN latest_date USING (report_date_local)
JOIN instrument USING (instrument_id)
WHERE instrument.symbol LIKE 'ALEX%'
ORDER BY instrument.conid;

WITH latest_date AS (
    SELECT max(report_date_local) AS report_date_local FROM pnl_snapshot_daily
)
SELECT daily.valuation_source, daily.fx_source,
       (daily.cost_basis IS NULL) AS cost_basis_missing,
       count(*) AS row_count
FROM pnl_snapshot_daily daily
JOIN latest_date USING (report_date_local)
WHERE daily.provisional
GROUP BY daily.valuation_source, daily.fx_source, (daily.cost_basis IS NULL)
ORDER BY daily.valuation_source, daily.fx_source, cost_basis_missing;
SQL
```

Require:

```text
broker OpenPositions rows missing snapshots = 0
snapshot quantities differing from broker quantities = 0
non-cash nonzero snapshots absent from OpenPositions = 0
ALEX stock position = 0
ALEX option position = 0
```

Also report all remaining `provisional=true` rows grouped by `valuation_source` and explain whether each is missing cost, missing mark/FX, or an unresolved event-derived lot mismatch.

- [ ] **Step 8: Verify the UI and publish the reviewed commits**

Run: `curl --fail --silent http://127.0.0.1:8000/ui >/dev/null`

Refresh the dashboard and confirm ALEX is no longer presented as an open holding and Position/currency formatting remains intact.

Run: `git status --short --branch`

Run: `git push`

Expected: the branch is clean and synchronized with `origin`; the handoff includes commit SHA, backup path/checksum, before/after counts, reprocess run IDs, and any remaining provisional discrepancies.
