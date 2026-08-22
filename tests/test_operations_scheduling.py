"""Behavior tests for host-level operational scheduling artifacts."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "run_scheduled_job.sh"
SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"


def _run_launcher(job_name: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert LAUNCHER.is_file(), "scheduled job launcher is missing"
    return subprocess.run(
        [str(LAUNCHER), job_name],
        check=False,
        env=environment,
        text=True,
        capture_output=True,
    )


def _scheduler_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    stock_root = tmp_path / "stock-app"
    binary_root = tmp_path / "bin"
    lock_root = tmp_path / "locks"
    capture_path = tmp_path / "captured-arguments"
    stock_root.mkdir()
    binary_root.mkdir()
    lock_root.mkdir()
    (stock_root / "scripts").mkdir()
    (stock_root / ".env").write_text("POSTGRES_DB=test\n", encoding="utf-8")
    (stock_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    fake_docker = binary_root / "docker"
    fake_docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{binary_root}:{os.environ['PATH']}",
        "CAPTURE_PATH": str(capture_path),
        "STOCK_APP_ROOT": str(stock_root),
        "STOCK_APP_LOCK_ROOT": str(lock_root),
    }
    return environment, stock_root, capture_path


@pytest.mark.parametrize(
    ("job_name", "expected_arguments"),
    [
        (
            "ingestion",
            [
                "compose",
                "--project-name",
                "stock_app",
                "--env-file",
                ".env",
                "--file",
                "docker-compose.yml",
                "exec",
                "-T",
                "app",
                "python",
                "-m",
                "app.main",
                "ingestion-run",
            ],
        ),
        (
            "retention",
            [
                "compose",
                "--project-name",
                "stock_app",
                "--env-file",
                ".env",
                "--file",
                "docker-compose.yml",
                "exec",
                "-T",
                "app",
                "python",
                "-m",
                "app.main",
                "diagnostics-retention",
            ],
        ),
        (
            "alerts",
            [
                "compose",
                "--project-name",
                "stock_app",
                "--env-file",
                ".env",
                "--file",
                "docker-compose.yml",
                "exec",
                "-T",
                "app",
                "python",
                "-m",
                "app.main",
                "alerts-evaluate",
            ],
        ),
    ],
)
def test_scheduler_routes_container_jobs_to_pinned_compose_project(
    tmp_path: Path,
    job_name: str,
    expected_arguments: list[str],
) -> None:
    """Catch a launcher routing a scheduled command to the wrong Compose project."""

    environment, _, capture_path = _scheduler_environment(tmp_path)

    result = _run_launcher(job_name, environment)

    assert result.returncode == 0, result.stderr
    assert capture_path.read_text(encoding="utf-8").splitlines() == expected_arguments


def test_scheduler_rejects_unknown_job_without_executing_command(tmp_path: Path) -> None:
    """Catch an unknown scheduler job falling through to an unintended command."""

    environment, _, capture_path = _scheduler_environment(tmp_path)

    result = _run_launcher("unknown", environment)

    assert result.returncode == 64
    assert not capture_path.exists()


def test_scheduler_refuses_overlapping_maintenance_job(tmp_path: Path) -> None:
    """Catch backup/restore/retention jobs running concurrently against PostgreSQL."""

    environment, stock_root, capture_path = _scheduler_environment(tmp_path)
    backup_script = stock_root / "scripts" / "backup_postgres.sh"
    backup_script.write_text(
        "#!/bin/sh\nprintf 'backup\\n' > \"$CAPTURE_PATH\"\n",
        encoding="utf-8",
    )
    backup_script.chmod(0o755)
    lock_path = Path(environment["STOCK_APP_LOCK_ROOT"]) / "maintenance.lock"

    with lock_path.open("w", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_launcher("backup", environment)

    assert result.returncode != 0
    assert not capture_path.exists()


def test_systemd_scheduler_units_verify(tmp_path: Path) -> None:
    """Catch malformed service/timer units before installation on the host."""

    unit_paths = sorted(SYSTEMD_DIR.glob("ibkr-flex-ledger-*.*"))
    assert len(unit_paths) == 10
    alert_timer = (
        SYSTEMD_DIR / "ibkr-flex-ledger-alerts.timer"
    ).read_text(encoding="utf-8")
    assert "OnCalendar=*:0/15" in alert_timer
    assert "Persistent=true" in alert_timer
    assert "RandomizedDelaySec=1m" in alert_timer
    verification_paths = []
    for unit_path in unit_paths:
        contents = unit_path.read_text(encoding="utf-8")
        if unit_path.suffix == ".service":
            assert "WorkingDirectory=/stock_app" in contents
            assert "ExecStart=/stock_app/scripts/run_scheduled_job.sh" in contents
            contents = contents.replace("/stock_app", str(PROJECT_ROOT))
        verification_path = tmp_path / unit_path.name
        verification_path.write_text(contents, encoding="utf-8")
        verification_paths.append(verification_path)

    result = subprocess.run(
        ["systemd-analyze", "verify", *[str(path) for path in verification_paths]],
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "SYSTEMD_LOG_LEVEL": "warning"},
    )

    assert result.returncode == 0, result.stderr
