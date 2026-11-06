"""Initial schema. Alembic is the source of truth for indexes and append-only triggers."""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
CREATE TABLE departments (
    id uuid PRIMARY KEY,
    name varchar(120) NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX uq_departments_name_lower ON departments (lower(name));

CREATE TABLE users (
    id uuid PRIMARY KEY,
    email varchar(254) NOT NULL,
    display_name varchar(120) NOT NULL,
    password_hash varchar(255) NOT NULL,
    role varchar(16) NOT NULL,
    is_active boolean NOT NULL,
    must_change_password boolean NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    last_login_at timestamptz NULL,
    CONSTRAINT ck_users_role CHECK (role IN ('ADMIN', 'MANAGER'))
);
CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email));

CREATE TABLE employees (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL UNIQUE REFERENCES users (id),
    department_id uuid NULL REFERENCES departments (id),
    employee_number varchar(32) NOT NULL,
    device_pin varchar(14) NULL,
    full_name varchar(120) NOT NULL,
    designation varchar(120) NULL,
    remote_attendance_enabled boolean NOT NULL,
    is_active boolean NOT NULL,
    is_test_record boolean NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT uq_employees_employee_number UNIQUE (employee_number)
);
CREATE UNIQUE INDEX uq_employee_device_pin ON employees (device_pin) WHERE device_pin IS NOT NULL;

CREATE TABLE user_mfa (
    user_id uuid PRIMARY KEY REFERENCES users (id),
    totp_secret_encrypted text NOT NULL,
    enabled boolean NOT NULL,
    recovery_code_hashes jsonb NOT NULL,
    last_totp_step integer NULL,
    enrolled_at timestamptz NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE auth_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id),
    token_hash varchar(64) NOT NULL,
    csrf_token varchar(128) NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    revoked_at timestamptz NULL,
    ip_address varchar(64) NULL,
    user_agent_family varchar(16) NULL,
    CONSTRAINT uq_auth_sessions_token UNIQUE (token_hash)
);
CREATE INDEX ix_auth_sessions_user ON auth_sessions (user_id);

CREATE TABLE login_challenges (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id),
    token_hash varchar(64) NOT NULL,
    purpose varchar(16) NOT NULL,
    pending_totp_secret_encrypted text NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz NULL,
    CONSTRAINT uq_login_challenges_token UNIQUE (token_hash),
    CONSTRAINT ck_login_challenge_purpose CHECK (purpose IN ('PASSWORD', 'ENROLL', 'MFA'))
);

CREATE TABLE devices (
    id uuid PRIMARY KEY,
    name varchar(120) NOT NULL,
    model varchar(80) NOT NULL,
    serial_number varchar(64) NOT NULL,
    firmware varchar(80) NULL,
    platform varchar(80) NULL,
    mac_address varchar(32) NULL,
    location varchar(120) NULL,
    protocol varchar(32) NOT NULL,
    status varchar(32) NOT NULL,
    notes text NULL,
    last_seen_at timestamptz NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT uq_devices_serial UNIQUE (serial_number),
    CONSTRAINT ck_devices_status CHECK (status IN ('NOT_CONNECTED', 'DISABLED'))
);

CREATE TABLE employee_device_mappings (
    id uuid PRIMARY KEY,
    employee_id uuid NOT NULL REFERENCES employees (id),
    device_id uuid NOT NULL REFERENCES devices (id),
    device_user_pin varchar(14) NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_mapping_device_pin UNIQUE (device_id, device_user_pin),
    CONSTRAINT uq_mapping_employee_device UNIQUE (employee_id, device_id)
);

CREATE TABLE attendance_events (
    id uuid PRIMARY KEY,
    employee_id uuid NOT NULL REFERENCES employees (id),
    event_type varchar(16) NOT NULL,
    source varchar(32) NOT NULL,
    server_received_at_utc timestamptz NOT NULL,
    effective_at_utc timestamptz NOT NULL,
    client_reported_at timestamptz NULL,
    device_id uuid NULL REFERENCES devices (id),
    external_event_id varchar(128) NULL,
    idempotency_key varchar(80) NOT NULL,
    status varchar(16) NOT NULL,
    created_by uuid NULL REFERENCES users (id),
    metadata jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_attendance_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_event_type CHECK (event_type IN ('CHECK_IN', 'CHECK_OUT')),
    CONSTRAINT ck_event_source CHECK (source IN ('REMOTE_WEB', 'REMOTE_MOBILE', 'PHYSICAL_BIOMETRIC', 'ADMIN_CORRECTION')),
    CONSTRAINT ck_event_status CHECK (status IN ('RECORDED', 'FLAGGED'))
);
CREATE INDEX ix_events_employee_effective ON attendance_events (employee_id, effective_at_utc);
CREATE UNIQUE INDEX uq_attendance_external_event
    ON attendance_events (source, external_event_id)
    WHERE external_event_id IS NOT NULL;

CREATE TABLE attendance_sessions (
    id uuid PRIMARY KEY,
    employee_id uuid NOT NULL REFERENCES employees (id),
    status varchar(16) NOT NULL,
    check_in_event_id uuid NOT NULL REFERENCES attendance_events (id),
    check_out_event_id uuid NULL REFERENCES attendance_events (id),
    opened_at timestamptz NOT NULL,
    closed_at timestamptz NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT ck_session_status CHECK (status IN ('OPEN', 'CLOSED'))
);
CREATE UNIQUE INDEX uq_one_open_session_per_employee
    ON attendance_sessions (employee_id)
    WHERE status = 'OPEN';
CREATE INDEX ix_sessions_employee ON attendance_sessions (employee_id, opened_at);

CREATE TABLE correction_requests (
    id uuid PRIMARY KEY,
    employee_id uuid NOT NULL REFERENCES employees (id),
    requested_by uuid NOT NULL REFERENCES users (id),
    kind varchar(32) NOT NULL,
    reason text NOT NULL,
    requested_start_at timestamptz NULL,
    requested_end_at timestamptz NULL,
    status varchar(16) NOT NULL,
    review_reason text NULL,
    reviewed_by uuid NULL REFERENCES users (id),
    reviewed_at timestamptz NULL,
    resulting_check_in_event_id uuid NULL REFERENCES attendance_events (id),
    resulting_check_out_event_id uuid NULL REFERENCES attendance_events (id),
    resulting_session_id uuid NULL REFERENCES attendance_sessions (id),
    created_at timestamptz NOT NULL,
    CONSTRAINT ck_correction_kind CHECK (kind IN ('MISSED_CHECK_IN', 'MISSED_CHECK_OUT', 'HISTORICAL_PAIR')),
    CONSTRAINT ck_correction_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED'))
);
CREATE INDEX ix_corrections_status ON correction_requests (status, created_at);

CREATE TABLE integration_connections (
    id uuid PRIMARY KEY,
    code varchar(32) NOT NULL,
    display_name varchar(120) NOT NULL,
    status varchar(32) NOT NULL,
    summary text NOT NULL,
    last_heartbeat_at timestamptz NULL,
    last_error text NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT uq_integration_code UNIQUE (code)
);

CREATE TABLE sync_jobs (
    id uuid PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES integration_connections (id),
    status varchar(32) NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NULL,
    message text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE audit_logs (
    id uuid PRIMARY KEY,
    actor_user_id uuid NULL REFERENCES users (id),
    action varchar(64) NOT NULL,
    entity_type varchar(64) NOT NULL,
    entity_id varchar(64) NULL,
    ip_address varchar(64) NULL,
    details jsonb NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at DESC);

CREATE TABLE attendance_policies (
    id integer PRIMARY KEY,
    remote_attendance_approved boolean NOT NULL,
    approved_at timestamptz NULL,
    approved_by uuid NULL REFERENCES users (id),
    working_days jsonb NOT NULL,
    work_start_local time NULL,
    work_end_local time NULL,
    grace_minutes integer NULL,
    inference_enabled boolean NOT NULL,
    enforce_working_days boolean NOT NULL,
    policy_note text NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE OR REPLACE FUNCTION kpra_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $func$
BEGIN
    RAISE EXCEPTION 'append-only table % cannot be modified', TG_TABLE_NAME;
END;
$func$;

CREATE TRIGGER attendance_events_append_only
    BEFORE UPDATE OR DELETE ON attendance_events
    FOR EACH ROW EXECUTE FUNCTION kpra_reject_mutation();

CREATE TRIGGER audit_logs_append_only
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION kpra_reject_mutation();

INSERT INTO devices (
    id, name, model, serial_number, firmware, platform, mac_address, location,
    protocol, status, notes, last_seen_at, created_at, updated_at
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    'KPRA SenseFace 7A (observed)',
    'SenseFace 7A',
    'VDE2244500097',
    NULL,
    'ZAM70_TFT',
    '00:17:61:10:ad:46',
    NULL,
    'ADMS',
    'NOT_CONNECTED',
    'Registered in the KPRA TimeNex tenant as KPRA Head office. ADMS remains 173.208.167.249 port 80, domain name disabled, proxy disabled. This application is not receiving events. Do not change the terminal.',
    NULL,
    now(),
    now()
);

INSERT INTO integration_connections (
    id, code, display_name, status, summary, last_heartbeat_at, last_error, created_at, updated_at
) VALUES
(
    '22222222-2222-4222-8222-222222222222',
    'timenex',
    'TimeNex',
    'NO_SUPPORTED_INTERFACE',
    'The KPRA TimeNex tenant is a signed-in web application with CSV backup and a manual Mark Attendance screen. No API contract was found, so this app sends nothing and marks nothing synced.',
    NULL,
    NULL,
    now(),
    now()
),
(
    '33333333-3333-4333-8333-333333333333',
    'senseface',
    'SenseFace 7 Series',
    'LISTENER_DISABLED',
    'ADMS listener is off. The production terminal stays on its current server. TCP 4370 is the device COMM port in the vendor manual, not the observed ADMS port 80.',
    NULL,
    NULL,
    now(),
    now()
);

INSERT INTO attendance_policies (
    id, remote_attendance_approved, approved_at, approved_by, working_days,
    work_start_local, work_end_local, grace_minutes, inference_enabled,
    enforce_working_days, policy_note, updated_at
) VALUES (
    1, false, NULL, NULL, '[]'::jsonb,
    NULL, NULL, NULL, false,
    false,
    'Remote attendance is off until an administrator records KPRA approval. Schedules are unset. Late and early status is not calculated.',
    now()
);
"""

DOWNGRADE_SQL = r"""
DROP TRIGGER IF EXISTS attendance_events_append_only ON attendance_events;
DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs;
DROP FUNCTION IF EXISTS kpra_reject_mutation();
DROP TABLE IF EXISTS audit_logs, sync_jobs, correction_requests, attendance_sessions,
    attendance_events, employee_device_mappings, login_challenges, auth_sessions,
    user_mfa, employees, users, departments, devices, integration_connections,
    attendance_policies CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
