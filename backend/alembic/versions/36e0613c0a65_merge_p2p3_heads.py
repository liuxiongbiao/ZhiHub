"""merge_p2p3_heads

Revision ID: 36e0613c0a65
Revises: d10_account_ext, d10_audit, d10_wx_oid
Create Date: 2026-08-09 19:54:42.051796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36e0613c0a65'
down_revision: Union[str, None] = ('d10_account_ext', 'd10_audit', 'd10_wx_oid')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
