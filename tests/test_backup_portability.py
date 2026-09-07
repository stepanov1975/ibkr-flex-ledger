"""Run the real backup shell workflow with disposable storage and broker-free tools."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


SCRIPT = Path(__file__).parents[1] / 'scripts/backup_postgres.sh'


def test_retained_backup_checksums_verify_the_retained_copy(tmp_path):
    root = tmp_path / 'backups'
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    docker = binaries / 'docker'
    docker.write_text(f'''#!{sys.executable}
import os, subprocess, sys
args = sys.argv[1:]
if '-c' not in args:
    raise SystemExit(0)
script = args[args.index('-c') + 1].replace('/backups', os.environ['TEST_BACKUP_ROOT'])
env = dict(os.environ, BACKUP_STAMP='20260906T020000Z', POSTGRES_USER='test')
raise SystemExit(subprocess.call(['sh', '-eu', '-c', script], env=env))
''')
    tools = {
        'date': '#!/bin/sh\ncase "$*" in *+%u) echo 7;; *+%d) echo 01;; *) echo 20260906T020000Z;; esac\n',
        'pg_basebackup': '#!/bin/sh\nwhile test "$1" != -D; do shift; done\nshift\nprintf "test fixture" > "$1/PG_VERSION"\n',
        'pg_verifybackup': '#!/bin/sh\nexit 0\n',
    }
    for name, source in tools.items():
        (binaries / name).write_text(source)
    for path in binaries.iterdir():
        path.chmod(0o755)
    result = subprocess.run(['sh', str(SCRIPT)], env={
        **os.environ, 'PATH': f"{binaries}:{os.environ['PATH']}", 'TEST_BACKUP_ROOT': str(root),
    }, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    shutil.rmtree(root / 'base')
    for retained in (root / 'weekly', root / 'monthly'):
        sidecar = next(retained.glob('*.sha256'))
        checked = subprocess.run(['sha256sum', '-c', sidecar.name], cwd=retained, capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
        archive = next(retained.glob('*.tar.gz'))
        archive.write_bytes(b'corrupt retained copy')
        checked = subprocess.run(['sha256sum', '-c', sidecar.name], cwd=retained, capture_output=True, text=True)
        assert checked.returncode != 0
