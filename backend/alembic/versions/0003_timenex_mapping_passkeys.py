"""TimeNex identity mapping, passkey credentials, and the attendance outbox."""

from alembic import op

revision = "0003_timenex_mapping_passkeys"
down_revision = "0002_test_data_source"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE auth_sessions
    ADD COLUMN auth_method varchar(32) NOT NULL DEFAULT 'PASSWORD_MFA';
ALTER TABLE auth_sessions
    ADD CONSTRAINT ck_auth_sessions_method CHECK (auth_method IN ('PASSWORD_MFA', 'WEBAUTHN'));

ALTER TABLE attendance_events
    ADD COLUMN authentication_method varchar(32) NULL,
    ADD COLUMN timenex_sync_status varchar(32) NOT NULL DEFAULT 'NOT_SUBMITTED',
    ADD COLUMN timenex_reference varchar(128) NULL;
ALTER TABLE attendance_events
    ADD CONSTRAINT ck_event_auth_method CHECK (
        authentication_method IS NULL OR authentication_method IN ('PASSWORD_MFA', 'WEBAUTHN')
    );
ALTER TABLE attendance_events
    ADD CONSTRAINT ck_event_timenex_status CHECK (
        timenex_sync_status IN (
            'NOT_SUBMITTED', 'NOT_MAPPED', 'AWAITING_CONTRACT', 'MISCONFIGURED', 'CONFIRMED', 'FAILED'
        )
    );

CREATE TABLE timenex_identity_mappings (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL UNIQUE REFERENCES users (id),
    employee_id uuid NOT NULL UNIQUE REFERENCES employees (id),
    timenex_employee_id varchar(32) NOT NULL,
    employee_name varchar(120) NOT NULL,
    department_name varchar(120) NOT NULL,
    account_status varchar(16) NOT NULL,
    verification_status varchar(16) NOT NULL,
    verification_source varchar(32) NULL,
    verified_by uuid NULL REFERENCES users (id),
    verified_at timestamptz NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT ck_timenex_account_status CHECK (account_status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT ck_timenex_verification CHECK (verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')),
    CONSTRAINT ck_timenex_verification_source CHECK (
        verification_source IS NULL OR verification_source IN ('ADMIN_APPROVAL', 'AUTHORIZED_EXPORT')
    )
);
CREATE UNIQUE INDEX uq_timenex_employee_id ON timenex_identity_mappings (timenex_employee_id);

CREATE TABLE webauthn_credentials (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users (id),
    credential_id bytea NOT NULL UNIQUE,
    public_key bytea NOT NULL,
    sign_count integer NOT NULL,
    transports jsonb NOT NULL,
    aaguid varchar(36) NULL,
    device_type varchar(32) NULL,
    backed_up boolean NOT NULL,
    created_at timestamptz NOT NULL,
    last_used_at timestamptz NULL
);
CREATE INDEX ix_webauthn_credentials_user ON webauthn_credentials (user_id);

CREATE TABLE webauthn_challenges (
    id uuid PRIMARY KEY,
    user_id uuid NULL REFERENCES users (id),
    purpose varchar(16) NOT NULL,
    challenge bytea NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz NULL,
    CONSTRAINT ck_webauthn_purpose CHECK (purpose IN ('REGISTER', 'AUTHENTICATE', 'ATTENDANCE'))
);

CREATE TABLE attendance_outbox (
    id uuid PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE REFERENCES attendance_events (id),
    timenex_employee_id varchar(32) NULL,
    status varchar(32) NOT NULL,
    attempt_count integer NOT NULL,
    next_attempt_at timestamptz NULL,
    message text NOT NULL,
    timenex_reference varchar(128) NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT ck_outbox_status CHECK (
        status IN (
            'NOT_SUBMITTED', 'NOT_MAPPED', 'AWAITING_CONTRACT', 'MISCONFIGURED', 'CONFIRMED', 'FAILED'
        )
    )
);
CREATE INDEX ix_outbox_due ON attendance_outbox (status, next_attempt_at);
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS attendance_outbox;
DROP TABLE IF EXISTS webauthn_challenges;
DROP TABLE IF EXISTS webauthn_credentials;
DROP TABLE IF EXISTS timenex_identity_mappings;
ALTER TABLE attendance_events DROP CONSTRAINT IF EXISTS ck_event_timenex_status;
ALTER TABLE attendance_events DROP CONSTRAINT IF EXISTS ck_event_auth_method;
ALTER TABLE attendance_events
    DROP COLUMN IF EXISTS timenex_reference,
    DROP COLUMN IF EXISTS timenex_sync_status,
    DROP COLUMN IF EXISTS authentication_method;
ALTER TABLE auth_sessions DROP CONSTRAINT IF EXISTS ck_auth_sessions_method;
ALTER TABLE auth_sessions DROP COLUMN IF EXISTS auth_method;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
