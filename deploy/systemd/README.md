# systemd scheduling

These units schedule the operational commands for the Docker Compose deployment
at `/stock_app`. All calendar times are UTC.

| Job | Schedule | Lock |
| --- | --- | --- |
| Verified PostgreSQL backup | Daily at 02:00 | Maintenance |
| Diagnostics retention | Daily at 03:15 | Maintenance |
| Restore drill | Sunday at 04:00 | Maintenance |
| Flex ingestion | Daily at 09:00 | Ingestion |
| Outbound SLO alert evaluation | Every 15 minutes | Alerts |

The randomized delays in the timer files spread host load after startup. Persistent
timers run a missed job after the host returns. Backup, retention, and restore drill
share a non-blocking maintenance lock so they cannot overlap; ingestion and alert
evaluation each have their own lock so duplicate runs cannot overlap.

Install and activate the units on the Compose host:

```bash
sudo install -m 0644 deploy/systemd/ibkr-flex-ledger-*.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  ibkr-flex-ledger-ingestion.timer \
  ibkr-flex-ledger-retention.timer \
  ibkr-flex-ledger-backup.timer \
  ibkr-flex-ledger-restore-drill.timer
systemctl list-timers 'ibkr-flex-ledger-*'
```

The dashboard shows SLO warnings without an outbound destination. Keep the alerts
timer disabled for dashboard-only monitoring. After configuring a webhook or SMTP
destination, enable outbound evaluation separately:

```bash
sudo systemctl enable --now ibkr-flex-ledger-alerts.timer
```

Run and inspect one job before relying on its timer:

```bash
sudo systemctl start ibkr-flex-ledger-backup.service
systemctl status ibkr-flex-ledger-backup.service
journalctl -u ibkr-flex-ledger-backup.service
```

An overlap exits unsuccessfully without starting the job. The next scheduled run is
unaffected. Use `systemctl edit <unit>.timer` to override a calendar locally rather
than editing the installed unit.
