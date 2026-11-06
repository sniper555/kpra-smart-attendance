"""Record when a portal day was pushed."""

from alembic import op

revision = "0007_portal_push"
down_revision = "0006_passkey_challenge"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE attendance_events ADD COLUMN timenex_error text NULL;
ALTER TABLE attendance_events ADD COLUMN timenex_pushed_at timestamptz NULL;
"""

DOWNGRADE_SQL = r"""
ALTER TABLE attendance_events DROP COLUMN IF EXISTS timenex_pushed_at;
ALTER TABLE attendance_events DROP COLUMN IF EXISTS timenex_error;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
