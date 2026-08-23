"""Database-level guardrails for immutable refund approval events."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError


ROOT = Path(__file__).parents[3]


def _load_repair_migration():
    migration_path = (
        ROOT / "backend" / "alembic" / "versions"
        / "d33_refund_event_db_immutability.py"
    )
    spec = importlib.util.spec_from_file_location("d33_refund_event_db_immutability", migration_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repair_migration_fails_closed_when_triggers_cannot_be_created(monkeypatch):
    migration = _load_repair_migration()
    denied = OperationalError("CREATE TRIGGER", {}, Exception(1419, "SUPER privilege required"))
    monkeypatch.setattr(migration.op, "execute", lambda _statement: (_ for _ in ()).throw(denied))

    with pytest.raises(OperationalError):
        migration._create_mysql_immutability_triggers()


def test_local_mysql_enables_trusted_trigger_creation():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--log-bin-trust-function-creators=1" in compose
