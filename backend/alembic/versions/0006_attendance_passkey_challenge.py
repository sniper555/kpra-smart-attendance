"""Allow an attendance passkey challenge."""

from alembic import op

revision = "0006_passkey_challenge"
down_revision = "0005_passkey_attendance"
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
ALTER TABLE webauthn_challenges DROP CONSTRAINT ck_webauthn_purpose;
ALTER TABLE webauthn_challenges
    ADD CONSTRAINT ck_webauthn_purpose CHECK (purpose IN ('REGISTER', 'AUTHENTICATE', 'ATTENDANCE'));
"""

DOWNGRADE_SQL = r"""
ALTER TABLE webauthn_challenges DROP CONSTRAINT ck_webauthn_purpose;
ALTER TABLE webauthn_challenges
    ADD CONSTRAINT ck_webauthn_purpose CHECK (purpose IN ('REGISTER', 'AUTHENTICATE'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
