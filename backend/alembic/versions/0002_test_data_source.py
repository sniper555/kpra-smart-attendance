"""Allow TEST_DATA as an attendance source for the isolated QA harness."""

from alembic import op

revision = "0002_test_data_source"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE attendance_events DROP CONSTRAINT ck_event_source")
    op.execute(
        """
        ALTER TABLE attendance_events ADD CONSTRAINT ck_event_source
        CHECK (source IN (
            'REMOTE_WEB',
            'REMOTE_MOBILE',
            'PHYSICAL_BIOMETRIC',
            'ADMIN_CORRECTION',
            'TEST_DATA'
        ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE attendance_events DROP CONSTRAINT ck_event_source")
    op.execute(
        """
        ALTER TABLE attendance_events ADD CONSTRAINT ck_event_source
        CHECK (source IN (
            'REMOTE_WEB',
            'REMOTE_MOBILE',
            'PHYSICAL_BIOMETRIC',
            'ADMIN_CORRECTION'
        ))
        """
    )
