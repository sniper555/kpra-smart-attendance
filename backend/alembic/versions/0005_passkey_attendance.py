"""Record the passkey requirement for attendance actions."""

from alembic import op

revision = "0005_passkey_attendance"
down_revision = "0004_erp_internal_id"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE attendance_policies
    ADD COLUMN passkey_required_for_attendance boolean NOT NULL DEFAULT false;
"""

DOWNGRADE_SQL = r"""
ALTER TABLE attendance_policies DROP COLUMN IF EXISTS passkey_required_for_attendance;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
