# Outbound SLO Alerts Design

## Scope

Add scheduled outbound delivery for the ingestion alert state already exposed by
`GET /operations/slo`. The evaluator sends edge-triggered alert and recovery
notifications through independently optional generic webhooks and SMTP email.
It does not add new SLO thresholds, an alert-management UI, escalation policies,
or third-party provider integrations.

## Runtime flow

A new `alerts-evaluate` CLI command will:

1. load the configured account and alert channels;
2. read the same 30-day scheduled-ingestion window used by `/operations/slo`;
3. calculate the existing success-rate, duration, and consecutive-failure signals;
4. compare the resulting `alerting` state with durable state for each configured
   channel and destination;
5. deliver only required state transitions; and
6. exit unsuccessfully if any required channel delivery fails.

The API route and evaluator will share a small SLO-status builder so thresholds,
reason codes, and displayed metrics cannot drift. The existing analytics calculation
remains the source of threshold behavior.

The systemd launcher will expose an `alerts` job protected by its own non-blocking
lock. A persistent timer will evaluate alerts every 15 minutes with a randomized
delay. This lock prevents overlapping evaluations on the supported single-host
Docker Compose deployment.

## Delivery configuration

Both channels are opt-in and configured entirely through environment variables.

Webhook configuration:

- `ALERT_WEBHOOK_URL`
- `ALERT_DELIVERY_TIMEOUT_SECONDS` (default 10 seconds)

The URL must use HTTP or HTTPS. Delivery is an HTTP POST containing JSON. Requests
include `Content-Type: application/json` and an `Idempotency-Key` header.

Email configuration:

- `ALERT_SMTP_HOST`
- `ALERT_SMTP_PORT` (default 587)
- `ALERT_SMTP_STARTTLS` (default true)
- `ALERT_SMTP_USERNAME` and `ALERT_SMTP_PASSWORD` (both set or both absent)
- `ALERT_EMAIL_FROM`
- `ALERT_EMAIL_TO` (comma-separated recipients)
- `ALERT_DELIVERY_TIMEOUT_SECONDS`

Email is enabled only when host, sender, and recipients are all present. Partial
email configuration or one-sided SMTP authentication fails validation. The evaluator
fails clearly when no channel is enabled, while the API can continue operating with
delivery disabled. Secrets and destination values are never logged or persisted.

## Durable transition state

An Alembic migration will add `alert_delivery_state` with:

- `account_id`;
- `channel` (`webhook` or `email`);
- `destination_fingerprint`, a SHA-256 digest of normalized non-secret routing input;
- `alerting`, the last successfully established state;
- `transition_anchor_utc`, used to create a stable identifier for the next transition;
- `last_delivered_at_utc`, nullable for an initial healthy baseline; and
- `updated_at_utc`.

The primary key is `(account_id, channel, destination_fingerprint)`. Changing a
destination therefore establishes independent state without retaining the destination
itself.

When no row exists and the current state is healthy, the evaluator records a healthy
baseline and sends nothing. When no row exists and the current state is alerting, it
sends an initial alert. For existing rows it sends only when `alerting` changes. A
successful send updates that channel's row; a failed send does not. Channels are
processed independently, so one channel's failure does not cause an already successful
channel to repeat on the next evaluation.

The transition event identifier is a SHA-256 digest of account, channel, destination
fingerprint, target state, and the stored transition anchor. It remains stable across
retries and is supplied as the webhook idempotency key. SMTP is necessarily
at-least-once: a process crash after the server accepts a message but before state is
saved can produce a duplicate email.

## Notification content

Webhook JSON uses schema version `1` and email presents the same information in plain
text. Each transition includes:

- event identifier and event type (`alert` or `recovery`);
- account identifier and measurement time;
- 30-day run and success counts;
- success rate and its threshold;
- p95 duration and its threshold;
- current reason codes; and
- the consecutive-failure signal.

Reason codes are `success_rate_below_threshold`, `duration_above_threshold`, and
`consecutive_failures`. Recovery messages contain no active reason codes. Numeric nulls
remain null in JSON and render as `not available` in email.

## Error behavior

Network calls use the configured bounded timeout. HTTP non-success responses, URL or
socket failures, SMTP failures, and persistence failures make the CLI exit nonzero and
are visible in the systemd journal without credentials or destination values. The
evaluator continues to the other configured channel after a delivery failure, then
reports an overall failure. The next timer occurrence retries channels whose durable
state did not advance.

## Tests and verification

Tests will cover:

- shared SLO status and reason-code calculation;
- initial healthy baseline without notification;
- initial alert, duplicate suppression, recovery, and a later alert episode;
- stable event identifiers across failed retries;
- independent channel progress when one delivery fails;
- webhook headers and payload;
- SMTP TLS, authentication, recipients, subject, and body;
- complete, disabled, and invalid configuration combinations;
- database read/upsert behavior and migration upgrade/downgrade shape;
- CLI exit behavior; and
- launcher routing plus `systemd-analyze verify` for the alert service and timer.

The focused tests, full pytest suite, Ruff, MyPy, shell syntax check, Alembic head check,
and systemd unit verification must pass before integration.
