"""Exercise retained review findings at their public runtime boundaries."""

from datetime import datetime, timedelta, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.adapters import FlexWebServiceAdapter
from app.api.routers import ingestion as ingestion_router
from app.api.routers.ingestion import api_create_ingestion_router
from app.config import AppSettings
from app.db import IngestionSloRecord
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig
from app.ledger import snapshot_resolve_report_date_local
from app.operations import operations_build_slo_status
from test_api_ingestion import _ScopedReprocessOrchestrator, _SuccessReprocessOrchestrator
from test_jobs_ingestion_orchestrator import _RawPersistenceStub, _RepositoryStub


@pytest.mark.parametrize('phase', ['request', 'download'])
@pytest.mark.parametrize('failure', ['http_status', 'timeout', 'connection'])
def test_transport_credentials_never_reach_persisted_diagnostics(phase, failure):
    secret = 'synthetic-private-flex-token'

    def respond(request):
        if phase == 'download' and request.url.path.endswith('/SendRequest'):
            return httpx.Response(200, content=(
                '<FlexStatementResponse><Status>Success</Status>'
                '<ReferenceCode>test</ReferenceCode></FlexStatementResponse>'
            ).encode(), request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout(str(request.url), request=request)
        if failure == 'connection':
            raise httpx.ConnectError(str(request.url), request=request)
        return httpx.Response(500, request=request)

    with FlexWebServiceAdapter(token=secret, base_url='https://example.invalid', initial_wait_seconds=0) as adapter:
        adapter._http_client.close()
        adapter._http_client = httpx.Client(transport=httpx.MockTransport(respond))
        repository = _RepositoryStub()
        orchestrator = IngestionJobOrchestrator(
            ingestion_repository=repository, raw_persistence_repository=_RawPersistenceStub(),
            flex_adapter=adapter, config=IngestionOrchestratorConfig(account_id='TEST', flex_query_id='query'),
        )
        assert orchestrator.job_execute('ingestion_run').status == 'failed'
    stored = repository.finalize_calls[-1]
    assert secret not in json.dumps(stored, default=str)
    assert stored['error_message'].startswith('Flex ')
    assert 'traceback' in json.dumps(stored['diagnostics'])


@pytest.mark.parametrize(('elapsed_seconds', 'breached'), [(1800, False), (1801, True), (7200, True)])
def test_active_run_duration_uses_evaluation_time(elapsed_seconds, breached):
    now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    rows = [
        IngestionSloRecord('success', now - timedelta(days=1), now - timedelta(days=1, minutes=-2), 120000),
        IngestionSloRecord('started', now - timedelta(seconds=elapsed_seconds), None, None),
    ]
    status = operations_build_slo_status(rows, measured_at_utc=now)
    assert status.summary.duration_breached is breached
    assert status.alerting is breached
    assert status.summary.run_count == 1
    assert status.summary.success_rate == 1.0
    assert status.summary.p95_duration_ms == 120000


def test_unscoped_http_replay_resolves_business_date_on_each_request(monkeypatch):
    class Clock:
        current = datetime(2026, 9, 7, 20, 59, tzinfo=timezone.utc)

    replay = _ScopedReprocessOrchestrator()
    settings = AppSettings(_env_file=None, ibkr_flex_token='test', ibkr_flex_query_id='configured-query')
    app = FastAPI()
    app.include_router(api_create_ingestion_router(settings, _RepositoryStub(), _SuccessReprocessOrchestrator(), replay))
    monkeypatch.setattr(
        ingestion_router, 'snapshot_resolve_report_date_local',
        lambda timestamp: snapshot_resolve_report_date_local(Clock.current.isoformat()), raising=False,
    )
    with TestClient(app) as client:
        assert client.post('/ingestion/reprocess').status_code == 200
        Clock.current += timedelta(minutes=2)
        assert client.post('/ingestion/reprocess').status_code == 200
        assert client.post('/ingestion/reprocess?period_key=2026-08-20&flex_query_id=historical').status_code == 200
    assert replay.calls == [
        ('2026-09-07', 'configured-query'), ('2026-09-08', 'configured-query'), ('2026-08-20', 'historical'),
    ]
    assert replay.cleanup_calls == []
