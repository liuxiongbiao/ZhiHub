"""Repair database-level immutability for refund approval events.

Revision ID: d33_refund_event_db_immutability
Revises: d32_refresh_token_rotation
Create Date: 2026-08-17

The original d30 migration tolerated MySQL error 1419 and relied on the ORM
listener.  That left direct SQL issued by the application database account
able to rewrite financial audit evidence.  Local/production MySQL must permit
trigger creation; this repair migration deliberately fails closed otherwise.
"""

from alembic import op


revision = "d33_refund_event_db_immutability"
down_revision = "d32_refresh_token_rotation"
branch_labels = None
depends_on = None


_IMMUTABILITY_TRIGGERS = (
    "CREATE TRIGGER trg_refund_approval_events_no_update "
    "BEFORE UPDATE ON refund_approval_events FOR EACH ROW "
    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'",
    "CREATE TRIGGER trg_refund_approval_events_no_delete "
    "BEFORE DELETE ON refund_approval_events FOR EACH ROW "
    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'",
)


def _create_mysql_immutability_triggers() -> None:
    """Create both guards and surface every database error to Alembic."""
    for statement in _IMMUTABILITY_TRIGGERS:
        op.execute(statement)


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_refund_approval_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_refund_approval_events_no_update")
    _create_mysql_immutability_triggers()


def downgrade() -> None:
    # d30 already declares these triggers as part of the d32 schema.  Keep
    # them in place when stepping back over this repair-only revision.
    pass
