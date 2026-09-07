"""Exercise archive cleanup against disposable backup and WAL directories."""

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile


SCRIPT = Path(__file__).parents[1] / "scripts/prune_wal_archive.sh"


def _backup(root: Path, name: str, wal: str) -> Path:
    path = root / f"{name}.tar.gz"
    label = f"START WAL LOCATION: 0/3000028 (file {wal})\n".encode()
    with tarfile.open(path, "w:gz") as archive:
        item = tarfile.TarInfo("./backup_label")
        item.size = len(label)
        archive.addfile(item, io.BytesIO(label))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path}\n")
    return path


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), str(tmp_path / "base"), str(tmp_path / "wal")],
        env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"},
        capture_output=True, text=True,
    )


def _setup(tmp_path: Path) -> None:
    for name in ("base", "wal", "bin"):
        (tmp_path / name).mkdir()
    cleanup = tmp_path / "bin/pg_archivecleanup"
    cleanup.write_text('#!/bin/sh\nprintf "%s\\n" "$2" > "$1/cutoff"\n')
    cleanup.chmod(0o755)


def test_cleanup_preserves_wal_from_oldest_verified_daily_backup(tmp_path: Path) -> None:
    _setup(tmp_path)
    oldest = "000000010000000000000003"
    _backup(tmp_path / "base", "20260824T020000Z", oldest)
    _backup(tmp_path / "base", "20260907T020000Z", "000000010000000000000020")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "wal/cutoff").read_text().strip() == oldest


def test_cleanup_refuses_corrupt_backup(tmp_path: Path) -> None:
    _setup(tmp_path)
    backup = _backup(tmp_path / "base", "20260824T020000Z", "000000010000000000000003")
    backup.write_bytes(b"corrupt")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "wal/cutoff").exists()


def test_cleanup_without_backup_keeps_all_wal(tmp_path: Path) -> None:
    _setup(tmp_path)
    result = _run(tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / "wal/cutoff").exists()


def test_cleanup_accepts_portable_checksum_from_another_working_directory(tmp_path: Path) -> None:
    _setup(tmp_path)
    oldest = "000000010000000000000003"
    backup = _backup(tmp_path / "base", "20260824T020000Z", oldest)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    Path(str(backup) + ".sha256").write_text(f"{digest}  {backup.name}\n")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "wal/cutoff").read_text().strip() == oldest
