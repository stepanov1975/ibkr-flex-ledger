# Outbound SLO Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver deduplicated ingestion-SLO alert and recovery transitions through independently optional webhook and SMTP channels on a 15-minute systemd schedule.

**Architecture:** A shared status builder keeps `/operations/slo` and the evaluator consistent. The evaluator compares current status with per-account/channel/destination state in PostgreSQL, sends only state edges, and advances each channel only after successful delivery. Standard-library webhook and SMTP adapters keep external I/O separate from transition logic.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, SQLAlchemy text queries, Alembic, PostgreSQL 17, `urllib.request`, `smtplib`, pytest, systemd timers.

**Spec:** `docs/superpowers/specs/2026-08-22-outbound-slo-alerts-design.md`

## Global Constraints

- Preserve the existing success-rate, duration, and consecutive-failure thresholds.
- Webhook and SMTP channels are independently optional; `alerts-evaluate` fails when neither is configured.
- Never log or persist webhook URLs, SMTP passwords, or recipient addresses.
- Persist only a SHA-256 destination fingerprint.
- Initial healthy evaluation establishes a baseline without sending a recovery.
- Advance one channel's state only after its successful delivery; continue evaluating other channels after a failure.
- Use no new runtime dependencies.
- Keep all SQL inside `app/db`.
- Use test-first implementation and commit after every task.

---

### Task 1: Shared operations SLO status

**Files:**
- Create: `app/operations/slo_status.py`
- Modify: `app/operations/__init__.py`
- Modify: `app/api/routers/operations.py`
- Create: `tests/test_operations_slo_status.py`

**Interfaces:**
- Consumes: `analytics_ingestion_slo_summary(rows: list[IngestionSloRecord]) -> IngestionSloSummary`
- Produces: `OperationsSloStatus`, `operations_build_slo_status(rows, measured_at_utc)`, `OperationsSloStatus.api_payload()`

- [ ] **Step 1: Write failing shared-status tests**

Create `tests/test_operations_slo_status.py` with fixed UTC rows and assert the exact reason order and API-compatible values:

```python
from datetime import datetime, timedelta, timezone

from app.db import IngestionSloRecord
from app.operations import operations_build_slo_status


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _row(status: str, minutes: int) -> IngestionSloRecord:
    return IngestionSloRecord(
        status=status,
        started_at_utc=NOW - timedelta(minutes=minutes),
        ended_at_utc=NOW,
        duration_ms=minutes * 60_000,
    )


def test_operations_slo_status_reports_all_active_reasons() -> None:
    status = operations_build_slo_status(
        [_row("success", 2), _row("failed", 31), _row("failed", 1)],
        measured_at_utc=NOW,
    )

    assert status.alerting is True
    assert status.reason_codes == (
        "success_rate_below_threshold",
        "duration_above_threshold",
        "consecutive_failures",
    )
    assert status.api_payload()["measured_at_utc"] == NOW.isoformat()
    assert status.api_payload()["owner"] == "app owner"


def test_operations_slo_status_reports_healthy_empty_window() -> None:
    status = operations_build_slo_status([], measured_at_utc=NOW)

    assert status.alerting is False
    assert status.reason_codes == ()
    assert status.api_payload()["success_rate"] is None
```

- [ ] **Step 2: Run the new tests and confirm the import fails**

Run: `/stock_app/.venv/bin/pytest -q tests/test_operations_slo_status.py`

Expected: collection fails because `operations_build_slo_status` is not exported.

- [ ] **Step 3: Implement the status builder**

Create `app/operations/slo_status.py` with this public shape:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.analytics import IngestionSloSummary, analytics_ingestion_slo_summary
from app.db import IngestionSloRecord


@dataclass(frozen=True)
class OperationsSloStatus:
    measured_at_utc: datetime
    summary: IngestionSloSummary
    reason_codes: tuple[str, ...]

    @property
    def alerting(self) -> bool:
        return bool(self.reason_codes)

    def api_payload(self) -> dict[str, Any]:
        summary = self.summary
        return {
            "measured_at_utc": self.measured_at_utc.isoformat(),
            "window_days": 30,
            "run_count": summary.run_count,
            "success_count": summary.success_count,
            "success_rate": summary.success_rate,
            "success_target": summary.success_target,
            "success_alert_threshold": summary.success_alert_threshold,
            "success_breached": summary.success_breached,
            "p95_duration_ms": summary.p95_duration_ms,
            "p95_target_ms": summary.p95_target_ms,
            "duration_alert_threshold_ms": summary.duration_alert_threshold_ms,
            "duration_breached": summary.duration_breached,
            "consecutive_failure_alert": summary.consecutive_failure_alert,
            "alerting": self.alerting,
            "reason_codes": list(self.reason_codes),
            "owner": "app owner",
        }


def operations_build_slo_status(
    rows: list[IngestionSloRecord],
    measured_at_utc: datetime,
) -> OperationsSloStatus:
    summary = analytics_ingestion_slo_summary(rows)
    reasons = []
    if summary.success_breached:
        reasons.append("success_rate_below_threshold")
    if summary.duration_breached:
        reasons.append("duration_above_threshold")
    if summary.consecutive_failure_alert:
        reasons.append("consecutive_failures")
    return OperationsSloStatus(measured_at_utc, summary, tuple(reasons))
```

Replace the ellipsis with the existing route's exact payload keys and values. Export both names from `app/operations/__init__.py`. Change the route to call the builder and return `status.api_payload()`.

- [ ] **Step 4: Verify focused and existing analytics tests**

Run: `/stock_app/.venv/bin/pytest -q tests/test_operations_slo_status.py tests/test_analytics_slo.py`

Expected: all tests pass and the existing thresholds remain unchanged.

- [ ] **Step 5: Commit the shared status contract**

```bash
git add app/operations/slo_status.py app/operations/__init__.py app/api/routers/operations.py tests/test_operations_slo_status.py
git commit -m "Share operations SLO status calculation"
```

---

### Task 2: Alert delivery configuration

**Files:**
- Modify: `app/config/settings.py`
- Create: `tests/test_config_alerts.py`

**Interfaces:**
- Produces settings fields `alert_webhook_url`, `alert_delivery_timeout_seconds`, `alert_smtp_host`, `alert_smtp_port`, `alert_smtp_starttls`, `alert_smtp_username`, `alert_smtp_password`, `alert_email_from`, and `alert_email_to`
- Produces: `AppSettings.alert_email_recipients() -> tuple[str, ...]`

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_config_alerts.py`:

```python
import pytest
from pydantic import ValidationError

from app.config import AppSettings


BASE = {"ibkr_flex_token": "token", "ibkr_flex_query_id": "query"}


def test_alert_channels_are_disabled_by_default() -> None:
    settings = AppSettings(**BASE)
    assert settings.alert_webhook_url is None
    assert settings.alert_smtp_host is None
    assert settings.alert_email_recipients() == ()


def test_complete_email_configuration_parses_recipients() -> None:
    settings = AppSettings(
        **BASE,
        alert_smtp_host="smtp.example.test",
        alert_smtp_username="user",
        alert_smtp_password="secret",
        alert_email_from="alerts@example.test",
        alert_email_to="one@example.test, two@example.test",
    )
    assert settings.alert_email_recipients() == (
        "one@example.test",
        "two@example.test",
    )


@pytest.mark.parametrize(
    "override",
    [
        {"alert_webhook_url": "ftp://example.test/hook"},
        {"alert_smtp_host": "smtp.example.test"},
        {"alert_smtp_username": "user"},
        {"alert_smtp_password": "secret"},
    ],
)
def test_partial_or_unsafe_alert_configuration_is_rejected(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**BASE, **override)
```

- [ ] **Step 2: Run the configuration tests and confirm missing fields fail**

Run: `/stock_app/.venv/bin/pytest -q tests/test_config_alerts.py`

Expected: failures report missing alert settings attributes.

- [ ] **Step 3: Add settings and cross-field validation**

Use nullable strings with whitespace stripping and blank-to-`None` conversion, port bounds `1..65535`, and timeout bounds `>0` and `<=120`. Mark destinations, username, and password with `Field(repr=False)`; use `SecretStr` for the webhook URL and SMTP password, unwrapping them only while constructing adapters. Add a Pydantic `model_validator(mode="after")` that enforces:

```python
email_values = (self.alert_smtp_host, self.alert_email_from, self.alert_email_to)
if any(email_values) and not all(email_values):
    raise ValueError("SMTP host, email sender, and email recipients must be configured together")
if bool(self.alert_smtp_username) != bool(self.alert_smtp_password):
    raise ValueError("SMTP username and password must be configured together")
webhook_url = None if self.alert_webhook_url is None else self.alert_webhook_url.get_secret_value()
if webhook_url is not None and not webhook_url.startswith(("http://", "https://")):
    raise ValueError("alert webhook URL must use HTTP or HTTPS")
```

Implement `alert_email_recipients()` by splitting on commas, stripping each entry, and removing empty entries. Reject a configured `alert_email_to` that produces no recipients.

- [ ] **Step 4: Run configuration and existing API tests**

Run: `/stock_app/.venv/bin/pytest -q tests/test_config_alerts.py tests/test_api_health.py tests/test_api_ingestion.py tests/test_api_snapshot.py tests/test_api_portfolio_reports.py`

Expected: all pass; existing `AppSettings(...)` call sites require no alert arguments.

- [ ] **Step 5: Commit alert configuration**

```bash
git add app/config/settings.py tests/test_config_alerts.py
git commit -m "Add outbound alert configuration"
```

---

### Task 3: Durable per-channel alert state

**Files:**
- Create: `alembic/versions/20260822_07_alert_delivery_state.py`
- Create: `app/db/operations_alerts.py`
- Create: `app/db/operations_alert_interfaces.py`
- Modify: `app/db/__init__.py`
- Create: `tests/test_db_operations_alerts.py`
- Create: `tests/test_migration_alert_delivery_state.py`

**Interfaces:**
- Produces: `AlertDeliveryStateRecord(account_id, channel, destination_fingerprint, alerting, transition_anchor_utc, last_delivered_at_utc, updated_at_utc)`
- Produces protocol methods `db_alert_delivery_state_get(account_id, channel, destination_fingerprint)` and `db_alert_delivery_state_upsert(record)`
- Produces: `SQLAlchemyOperationsAlertService`

- [ ] **Step 1: Write failing state-repository tests**

Use the lightweight engine/connection stubs from `tests/test_db_query_templates.py` as a local pattern. Assert that `get` binds all three identity values and that `upsert` uses the full conflict key and updates only mutable state:

```python
def test_alert_state_get_uses_full_channel_identity() -> None:
    service = SQLAlchemyOperationsAlertService(engine=engine_stub)
    service.db_alert_delivery_state_get("U_TEST", "webhook", "a" * 64)
    assert "account_id=:account_id" in connection.executed_queries[0]
    assert "channel=:channel" in connection.executed_queries[0]
    assert "destination_fingerprint=:destination_fingerprint" in connection.executed_queries[0]


def test_alert_state_upsert_advances_transition_fields() -> None:
    service.db_alert_delivery_state_upsert(record)
    query = connection.executed_queries[0]
    assert "ON CONFLICT (account_id, channel, destination_fingerprint)" in query
    assert "alerting=EXCLUDED.alerting" in query
    assert "transition_anchor_utc=EXCLUDED.transition_anchor_utc" in query
    assert "last_delivered_at_utc=EXCLUDED.last_delivered_at_utc" in query
```

Also test that unsupported channel values raise `ValueError` before SQL executes and SQLAlchemy failures become `RuntimeError` without including identity values.

- [ ] **Step 2: Run repository tests and confirm imports fail**

Run: `/stock_app/.venv/bin/pytest -q tests/test_db_operations_alerts.py`

Expected: collection fails because the repository types do not exist.

- [ ] **Step 3: Implement the typed repository boundary**

Define the frozen record and protocol in `app/db/operations_alert_interfaces.py`. Implement fixed `SELECT` and `INSERT ... ON CONFLICT ... DO UPDATE` text queries in `app/db/operations_alerts.py`; use `engine.connect()` for reads and `engine.begin()` for upserts. Accept only `webhook` and `email`. Export the record, protocol, and SQLAlchemy service from `app/db/__init__.py`.

- [ ] **Step 4: Add the migration and migration-shape test**

Create revision `20260822_07` with `down_revision = "20260822_06"`. The upgrade creates:

```python
op.create_table(
    "alert_delivery_state",
    sa.Column("account_id", sa.Text(), nullable=False),
    sa.Column("channel", sa.Text(), nullable=False),
    sa.Column("destination_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("alerting", sa.Boolean(), nullable=False),
    sa.Column("transition_anchor_utc", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_delivered_at_utc", sa.DateTime(timezone=True), nullable=True),
    sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("channel IN ('webhook', 'email')", name="ck_alert_delivery_state_channel"),
    sa.PrimaryKeyConstraint(
        "account_id",
        "channel",
        "destination_fingerprint",
        name="pk_alert_delivery_state",
    ),
)
```

Downgrade drops only `alert_delivery_state`. In `tests/test_migration_alert_delivery_state.py`, import the module and monkeypatch `op.create_table`/`op.drop_table` to assert the table name, primary-key columns, check constraint, and downgrade target.

- [ ] **Step 5: Verify repository and migration tests**

Run: `/stock_app/.venv/bin/pytest -q tests/test_db_operations_alerts.py tests/test_migration_alert_delivery_state.py tests/test_db_query_templates.py`

Expected: all pass.

- [ ] **Step 6: Commit durable alert state**

```bash
git add alembic/versions/20260822_07_alert_delivery_state.py app/db/operations_alerts.py app/db/operations_alert_interfaces.py app/db/__init__.py tests/test_db_operations_alerts.py tests/test_migration_alert_delivery_state.py
git commit -m "Persist outbound alert delivery state"
```

---

### Task 4: Transition evaluator and deduplication

**Files:**
- Create: `app/operations/alerts.py`
- Modify: `app/operations/__init__.py`
- Create: `tests/test_operations_alerts.py`

**Interfaces:**
- Consumes: `OperationsSloStatus`, `AlertDeliveryStateRepositoryPort`
- Produces: `AlertTransition`, `AlertSenderPort`, `AlertEvaluationResult`, `AlertDeliveryError`, `operations_evaluate_slo_alerts(...)`

- [ ] **Step 1: Write failing transition tests with in-memory fakes**

Create fakes that key state by `(account_id, channel, destination_fingerprint)` and senders that append transitions or raise. Cover these exact cases:

```python
def test_initial_healthy_state_records_baseline_without_delivery() -> None:
    result = operations_evaluate_slo_alerts("U_TEST", healthy, repository, [webhook], NOW)
    assert result.delivered_channels == ()
    assert webhook.transitions == []
    assert repository.only_state().alerting is False
    assert repository.only_state().last_delivered_at_utc is None


def test_alert_is_sent_once_then_recovery_is_sent_once() -> None:
    first = operations_evaluate_slo_alerts("U_TEST", alerting, repository, [webhook], NOW)
    duplicate = operations_evaluate_slo_alerts("U_TEST", alerting, repository, [webhook], LATER)
    recovery = operations_evaluate_slo_alerts("U_TEST", healthy, repository, [webhook], RECOVERY_TIME)
    assert first.delivered_channels == ("webhook",)
    assert duplicate.delivered_channels == ()
    assert [item.event_type for item in webhook.transitions] == ["alert", "recovery"]


def test_failed_channel_retries_stable_event_while_successful_channel_stays_deduplicated() -> None:
    result = operations_evaluate_slo_alerts("U_TEST", alerting, repository, [webhook, email], NOW)
    assert result.failed_channels == ("email",)
    first_email_event_id = email.attempted[0].event_id
    retry = operations_evaluate_slo_alerts("U_TEST", alerting, repository, [webhook, email], LATER)
    assert retry.delivered_channels == ("email",)
    assert len(webhook.transitions) == 1
    assert email.attempted[1].event_id == first_email_event_id
```

Add a fourth test that performs alert → recovery → alert and asserts the second alert event ID differs from the first alert event ID.

- [ ] **Step 2: Run evaluator tests and confirm imports fail**

Run: `/stock_app/.venv/bin/pytest -q tests/test_operations_alerts.py`

Expected: collection fails because alert evaluator types do not exist.

- [ ] **Step 3: Implement transition types and evaluation**

Define:

```python
@dataclass(frozen=True)
class AlertTransition:
    event_id: str
    event_type: Literal["alert", "recovery"]
    account_id: str
    status: OperationsSloStatus

    def payload(self) -> dict[str, object]:
        summary = self.status.summary
        return {
            "schema_version": "1",
            "event_id": self.event_id,
            "event_type": self.event_type,
            "account_id": self.account_id,
            "measured_at_utc": self.status.measured_at_utc.isoformat(),
            "window_days": 30,
            "run_count": summary.run_count,
            "success_count": summary.success_count,
            "success_rate": summary.success_rate,
            "success_target": summary.success_target,
            "success_alert_threshold": summary.success_alert_threshold,
            "p95_duration_ms": summary.p95_duration_ms,
            "p95_target_ms": summary.p95_target_ms,
            "duration_alert_threshold_ms": summary.duration_alert_threshold_ms,
            "consecutive_failure_alert": summary.consecutive_failure_alert,
            "reason_codes": list(self.status.reason_codes),
        }


class AlertSenderPort(Protocol):
    channel: Literal["webhook", "email"]
    destination_fingerprint: str
    def send(self, transition: AlertTransition) -> None:
        pass


@dataclass(frozen=True)
class AlertEvaluationResult:
    delivered_channels: tuple[str, ...]
    failed_channels: tuple[str, ...]
```

Implement `operations_evaluate_slo_alerts(account_id: str, status: OperationsSloStatus, state_repository: AlertDeliveryStateRepositoryPort, senders: Sequence[AlertSenderPort], evaluated_at_utc: datetime) -> AlertEvaluationResult`. Implement the event ID as SHA-256 over a delimiter-separated UTF-8 string containing account, channel, destination fingerprint, target state, and `transition_anchor_utc.isoformat()`; use the literal `initial` anchor when no state exists.

For each sender: read its state; establish a healthy baseline when absent and healthy; skip equal states; otherwise send and then upsert the new state with `transition_anchor_utc=now`, `last_delivered_at_utc=now`, and `updated_at_utc=now`. Catch delivery or state-write exceptions per channel, retain no sensitive exception text in the result, continue to the next sender, and return failed channel names.

- [ ] **Step 4: Run evaluator and shared-status tests**

Run: `/stock_app/.venv/bin/pytest -q tests/test_operations_alerts.py tests/test_operations_slo_status.py`

Expected: all pass.

- [ ] **Step 5: Commit evaluator behavior**

```bash
git add app/operations/alerts.py app/operations/__init__.py tests/test_operations_alerts.py
git commit -m "Evaluate deduplicated SLO alert transitions"
```

---

### Task 5: Webhook and SMTP adapters

**Files:**
- Create: `app/adapters/alert_delivery.py`
- Modify: `app/adapters/__init__.py`
- Create: `tests/test_adapters_alert_delivery.py`

**Interfaces:**
- Consumes: `AlertTransition`, `AlertSenderPort`
- Produces: `WebhookAlertSender` and `SmtpAlertSender`

- [ ] **Step 1: Write failing webhook-adapter tests**

Monkeypatch `urllib.request.urlopen` with a context-manager fake. Instantiate the sender with `https://hooks.example.test/secret`, send a fixed transition, and assert:

```python
request = captured_request
assert request.full_url == "https://hooks.example.test/secret"
assert request.get_header("Content-type") == "application/json"
assert request.get_header("Idempotency-key") == transition.event_id
assert json.loads(request.data) == transition.payload()
assert captured_timeout == 10.0
assert "hooks.example.test" not in sender.destination_fingerprint
assert len(sender.destination_fingerprint) == 64
```

Add tests that a non-2xx fake response and `urllib.error.URLError` raise `AlertDeliveryError("webhook delivery failed")` without including the URL.

- [ ] **Step 2: Write failing SMTP-adapter tests**

Monkeypatch `smtplib.SMTP` with a fake recording constructor arguments and calls. Assert port/timeout, `starttls()`, `login()`, and `send_message()` are called; recipients are supplied; subject distinguishes alert from recovery; body includes account, reason codes, metrics, and event ID. Add one test with TLS/auth disabled and one SMTP exception test expecting `AlertDeliveryError("email delivery failed")` without server or recipient values.

- [ ] **Step 3: Run adapter tests and confirm imports fail**

Run: `/stock_app/.venv/bin/pytest -q tests/test_adapters_alert_delivery.py`

Expected: collection fails because delivery adapters do not exist.

- [ ] **Step 4: Implement standard-library delivery adapters**

`WebhookAlertSender.send()` must serialize `transition.payload()` with sorted keys, build a POST `urllib.request.Request`, and require response status in `200..299`. Wrap all external exceptions in the fixed sanitized `AlertDeliveryError` message.

`SmtpAlertSender.send()` must create `email.message.EmailMessage`, set From/To/Subject, render the specified plain-text fields, call STARTTLS before optional login, and send via `smtplib.SMTP(host, port, timeout=timeout)`. Compute fingerprints from normalized routing strings using SHA-256; never expose raw routing values through `repr` fields, exceptions, or logs.

- [ ] **Step 5: Verify adapter and evaluator tests**

Run: `/stock_app/.venv/bin/pytest -q tests/test_adapters_alert_delivery.py tests/test_operations_alerts.py`

Expected: all pass.

- [ ] **Step 6: Commit outbound adapters**

```bash
git add app/adapters/alert_delivery.py app/adapters/__init__.py tests/test_adapters_alert_delivery.py
git commit -m "Deliver SLO alerts by webhook and email"
```

---

### Task 6: Evaluator bootstrap and CLI

**Files:**
- Modify: `app/bootstrap.py`
- Modify: `app/main.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Create: `tests/test_main_alerts.py`

**Interfaces:**
- Produces: `bootstrap_evaluate_slo_alerts() -> AlertEvaluationResult`
- Produces CLI: `python -m app.main alerts-evaluate`

- [ ] **Step 1: Write failing CLI tests**

Monkeypatch `sys.argv` and `main_module.bootstrap_evaluate_slo_alerts`. Assert success for no failures and `SystemExit(1)` when `failed_channels` is non-empty. Add a test where bootstrap raises a fixed no-channel configuration error and assert the CLI exits nonzero without printing secret configuration values.

```python
def test_alerts_evaluate_cli_exits_nonzero_when_delivery_failed(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["stock-app", "alerts-evaluate"])
    monkeypatch.setattr(
        main_module,
        "bootstrap_evaluate_slo_alerts",
        lambda: AlertEvaluationResult((), ("email",)),
    )
    with pytest.raises(SystemExit) as raised:
        main_module.main()
    assert raised.value.code == 1
```

- [ ] **Step 2: Run CLI tests and confirm the command is rejected**

Run: `/stock_app/.venv/bin/pytest -q tests/test_main_alerts.py`

Expected: failure because `alerts-evaluate` is not an argparse choice.

- [ ] **Step 3: Wire settings, repositories, senders, and status**

Implement `bootstrap_evaluate_slo_alerts()` to:

1. load settings and create one engine;
2. build `SQLAlchemyPortfolioService` and `SQLAlchemyOperationsAlertService`;
3. construct a webhook sender when `alert_webhook_url` is set;
4. construct an SMTP sender when the complete email configuration is set;
5. raise `AlertConfigurationError("no outbound alert channel is configured")` when the list is empty;
6. query scheduled SLO rows since `now - timedelta(days=30)`;
7. build shared status; and
8. call `operations_evaluate_slo_alerts(settings.account_id, status, state_repository, senders, now)`.

Add `alerts-evaluate` to argparse. Print a safe result containing channel names only; exit `1` when `failed_channels` is non-empty.

- [ ] **Step 4: Pass optional settings into the app container**

Add every alert variable from Task 2 to `docker-compose.yml` using empty/default substitutions, for example:

```yaml
ALERT_WEBHOOK_URL: ${ALERT_WEBHOOK_URL:-}
ALERT_DELIVERY_TIMEOUT_SECONDS: ${ALERT_DELIVERY_TIMEOUT_SECONDS:-10}
ALERT_SMTP_HOST: ${ALERT_SMTP_HOST:-}
ALERT_SMTP_PORT: ${ALERT_SMTP_PORT:-587}
ALERT_SMTP_STARTTLS: ${ALERT_SMTP_STARTTLS:-true}
ALERT_SMTP_USERNAME: ${ALERT_SMTP_USERNAME:-}
ALERT_SMTP_PASSWORD: ${ALERT_SMTP_PASSWORD:-}
ALERT_EMAIL_FROM: ${ALERT_EMAIL_FROM:-}
ALERT_EMAIL_TO: ${ALERT_EMAIL_TO:-}
```

Document the same variables as commented examples in `.env.example`; do not provide real destinations or credentials.

- [ ] **Step 5: Verify CLI, config, and Compose expansion**

Run:

```bash
/stock_app/.venv/bin/pytest -q tests/test_main_alerts.py tests/test_config_alerts.py
docker compose --env-file .env.example config --quiet
```

Expected: tests pass and Compose config validates.

- [ ] **Step 6: Commit evaluator entrypoint**

```bash
git add app/bootstrap.py app/main.py docker-compose.yml .env.example tests/test_main_alerts.py
git commit -m "Add scheduled alert evaluator command"
```

---

### Task 7: systemd alert schedule

**Files:**
- Modify: `scripts/run_scheduled_job.sh`
- Create: `deploy/systemd/ibkr-flex-ledger-alerts.service`
- Create: `deploy/systemd/ibkr-flex-ledger-alerts.timer`
- Modify: `deploy/systemd/README.md`
- Modify: `docs/operations.md`
- Modify: `tests/test_operations_scheduling.py`

**Interfaces:**
- Consumes CLI: `python -m app.main alerts-evaluate`
- Produces scheduler job: `run_scheduled_job.sh alerts`

- [ ] **Step 1: Extend scheduling tests first**

Add `alerts` to the launcher parameterization with exact expected Docker arguments ending in `app.main`, `alerts-evaluate`. Change the unit count assertion from 8 to 10 and assert the alert timer contains:

```python
assert "OnCalendar=*:0/15" in alert_timer
assert "Persistent=true" in alert_timer
assert "RandomizedDelaySec=1m" in alert_timer
```

- [ ] **Step 2: Run scheduler tests and confirm missing route/units fail**

Run: `/stock_app/.venv/bin/pytest -q tests/test_operations_scheduling.py`

Expected: the launcher rejects `alerts` and only eight units exist.

- [ ] **Step 3: Add launcher route and systemd units**

Add the launcher case:

```sh
alerts)
    lock_name=alerts.lock
    set -- docker compose --project-name stock_app --env-file .env \
        --file docker-compose.yml exec -T app python -m app.main alerts-evaluate
    ;;
```

Update usage text. Add a oneshot service matching the existing units, with `ExecStart=/stock_app/scripts/run_scheduled_job.sh alerts` and a 5-minute timeout. Add a persistent timer using `OnCalendar=*:0/15`, `RandomizedDelaySec=1m`, and `WantedBy=timers.target`.

- [ ] **Step 4: Document activation and troubleshooting**

Add the alert timer to installation/enable commands and schedule table in `deploy/systemd/README.md`. In `docs/operations.md`, document channel environment variables, transition-only semantics, the initial healthy baseline, per-channel retry behavior, manual invocation, journal inspection, and the rare SMTP crash-after-send duplicate limitation.

- [ ] **Step 5: Verify launcher, shell, and units**

Run:

```bash
sh -n scripts/run_scheduled_job.sh
/stock_app/.venv/bin/pytest -q tests/test_operations_scheduling.py
systemd-analyze verify deploy/systemd/ibkr-flex-ledger-*.*
```

Expected: shell syntax, all scheduler tests, and all ten units verify. If the isolated worktree makes `/stock_app/scripts/run_scheduled_job.sh` unavailable to direct verification, rely on the tested worktree-path substitution already implemented in `test_systemd_scheduler_units_verify` rather than weakening the deployment-path assertions.

- [ ] **Step 6: Commit alert scheduling**

```bash
git add scripts/run_scheduled_job.sh deploy/systemd docs/operations.md tests/test_operations_scheduling.py
git commit -m "Schedule outbound SLO alert evaluation"
```

---

### Task 8: End-to-end verification and migration evidence

**Files:**
- Modify only if a verification failure reveals a defect in files from Tasks 1–7.

**Interfaces:**
- Verifies the complete subsystem; produces no new runtime interface.

- [ ] **Step 1: Run all alert and scheduling tests together**

```bash
/stock_app/.venv/bin/pytest -q \
  tests/test_operations_slo_status.py \
  tests/test_config_alerts.py \
  tests/test_db_operations_alerts.py \
  tests/test_migration_alert_delivery_state.py \
  tests/test_operations_alerts.py \
  tests/test_adapters_alert_delivery.py \
  tests/test_main_alerts.py \
  tests/test_operations_scheduling.py
```

Expected: all pass.

- [ ] **Step 2: Run the full quality gate with safe dummy credentials**

```bash
IBKR_FLEX_TOKEN=test IBKR_FLEX_QUERY_ID=test /stock_app/.venv/bin/pytest -q
/stock_app/.venv/bin/ruff check app tests
/stock_app/.venv/bin/mypy
git diff --check
```

Expected: pytest passes with only documented integration skips; Ruff, MyPy, and whitespace checks pass.

- [ ] **Step 3: Verify the real migration chain without configuring delivery**

Against the active local Compose database after creating the task-required backup:

```bash
docker compose --project-name stock_app --env-file /stock_app/.env --file /stock_app/docker-compose.yml exec -T app alembic heads
docker compose --project-name stock_app --env-file /stock_app/.env --file /stock_app/docker-compose.yml exec -T app alembic upgrade head
docker compose --project-name stock_app --env-file /stock_app/.env --file /stock_app/docker-compose.yml exec -T app alembic current
```

Expected: one head/current revision, `20260822_07`. Do not invoke `alerts-evaluate` until at least one real delivery channel has been intentionally configured.

- [ ] **Step 4: Review changed files against the approved spec**

Check every spec section against the diff: shared calculation, opt-in config, per-destination state, edge transitions, stable webhook idempotency, independent retries, sanitized failures, payload parity, 15-minute timer, tests, and documentation. Correct only gaps in this feature and rerun the relevant focused test before the full gates.

- [ ] **Step 5: Commit any verification-only correction**

If Step 4 required changes:

```bash
git add app/adapters/alert_delivery.py app/api/routers/operations.py app/bootstrap.py app/config/settings.py app/db app/main.py app/operations alembic/versions/20260822_07_alert_delivery_state.py deploy/systemd docker-compose.yml docs/operations.md scripts/run_scheduled_job.sh tests
git commit -m "Fix outbound alert verification findings"
```

If no correction was required, do not create an empty commit.
