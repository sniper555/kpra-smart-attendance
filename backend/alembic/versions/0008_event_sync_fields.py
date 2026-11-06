"""Allow TimeNex sync fields on an attendance row to change.

The punch itself stays append-only. A later sync may record the portal result.
"""

from alembic import op

revision = "0008_event_sync_fields"
down_revision = "0007_portal_push"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE OR REPLACE FUNCTION kpra_reject_event_change()
RETURNS trigger
LANGUAGE plpgsql
AS $func$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'append-only table attendance_events cannot be modified';
    END IF;
    IF (to_jsonb(NEW) - 'timenex_sync_status' - 'timenex_reference' - 'timenex_error' - 'timenex_pushed_at')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'timenex_sync_status' - 'timenex_reference' - 'timenex_error' - 'timenex_pushed_at')
    THEN
        RAISE EXCEPTION 'append-only table attendance_events cannot be modified';
    END IF;
    RETURN NEW;
END;
$func$;

DROP TRIGGER IF EXISTS attendance_events_append_only ON attendance_events;
CREATE TRIGGER attendance_events_append_only
    BEFORE UPDATE OR DELETE ON attendance_events
    FOR EACH ROW EXECUTE FUNCTION kpra_reject_event_change();
"""

DOWNGRADE_SQL = r"""
DROP TRIGGER IF EXISTS attendance_events_append_only ON attendance_events;
CREATE TRIGGER attendance_events_append_only
    BEFORE UPDATE OR DELETE ON attendance_events
    FOR EACH ROW EXECUTE FUNCTION kpra_reject_mutation();
DROP FUNCTION IF EXISTS kpra_reject_event_change();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
