"""Separate the ERP internal employee key from the TimeNex employee ID."""

from alembic import op

revision = "0004_erp_internal_id"
down_revision = "0003_timenex_mapping_passkeys"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE timenex_identity_mappings
    ADD COLUMN erp_employee_id varchar(32) NULL;

ALTER TABLE attendance_outbox
    ADD COLUMN erp_status varchar(32) NOT NULL DEFAULT 'NOT_SENT',
    ADD COLUMN erp_reference varchar(128) NULL;
ALTER TABLE attendance_outbox
    ADD CONSTRAINT ck_outbox_erp_status CHECK (
        erp_status IN ('NOT_SENT', 'NEEDS_ERP_ID', 'PENDING', 'ACCEPTED', 'FAILED')
    );
"""

DOWNGRADE_SQL = r"""
ALTER TABLE attendance_outbox DROP CONSTRAINT IF EXISTS ck_outbox_erp_status;
ALTER TABLE attendance_outbox
    DROP COLUMN IF EXISTS erp_reference,
    DROP COLUMN IF EXISTS erp_status;
ALTER TABLE timenex_identity_mappings DROP COLUMN IF EXISTS erp_employee_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
