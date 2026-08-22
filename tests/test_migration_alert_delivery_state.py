"""Migration-shape tests for durable outbound alert delivery state."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from pytest import MonkeyPatch
from sqlalchemy.sql.schema import SchemaItem


def _migration() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic/versions/20260822_07_alert_delivery_state.py"
    spec = importlib.util.spec_from_file_location("alert_delivery_state_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alert_delivery_state_upgrade_creates_channel_scoped_table(monkeypatch: MonkeyPatch) -> None:
    """Create the exact per-account, per-channel, per-destination state table."""

    migration = _migration()
    captured: tuple[str, tuple[SchemaItem, ...]] | None = None

    def create_table(name: str, *columns: SchemaItem) -> None:
        nonlocal captured
        captured = (name, columns)

    monkeypatch.setattr(migration.op, "create_table", create_table)

    migration.upgrade()

    assert captured is not None
    table_name, columns = captured
    assert migration.revision == "20260822_07"
    assert migration.down_revision == "20260822_06"
    assert table_name == "alert_delivery_state"
    constraints = [item for item in columns if isinstance(item, sa.Constraint)]
    check = next(item for item in constraints if isinstance(item, sa.CheckConstraint))
    table = sa.Table("alert_delivery_state", sa.MetaData(), *columns)
    assert list(table.primary_key.columns.keys()) == ["account_id", "channel", "destination_fingerprint"]
    assert table.primary_key.name == "pk_alert_delivery_state"
    assert str(check.sqltext) == "channel IN ('webhook', 'email')"
    assert check.name == "ck_alert_delivery_state_channel"


def test_alert_delivery_state_downgrade_drops_only_its_table(monkeypatch: MonkeyPatch) -> None:
    """Reverse only the table introduced by this migration."""

    migration = _migration()
    dropped: list[str] = []
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    migration.downgrade()

    assert dropped == ["alert_delivery_state"]
