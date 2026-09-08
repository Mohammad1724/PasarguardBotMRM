"""Explicit wallet auto-renew consent, attempts and notice outbox."""
from alembic import op
import sqlalchemy as sa

revision = "d93b2e5f860c"
down_revision = "c82a1d9e740b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auto_renew_attempts",
        sa.Column("token", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("active_service_code", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("policy_revision", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("cycle_expire", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("identity", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("price", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("month", sa.String(length=7), primary_key=False, nullable=False),
        sa.Column("old_values", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("target_values", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("attempted", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column("retry_count", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("next_retry_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("error", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("completed_at", sa.BigInteger(), primary_key=False, nullable=True),
        sa.UniqueConstraint('service_code', 'cycle_expire', name='uq_auto_renew_cycle'),
        sa.UniqueConstraint('active_service_code', name=None),
    )
    op.create_index('ix_auto_renew_attempts_service_code', 'auto_renew_attempts', ['service_code'], unique=False)
    op.create_index('ix_auto_renew_attempts_user_id', 'auto_renew_attempts', ['user_id'], unique=False)
    op.create_index('ix_auto_renew_budget', 'auto_renew_attempts', ['service_code', 'month', 'status'], unique=False)
    op.create_index('ix_auto_renew_retry', 'auto_renew_attempts', ['status', 'next_retry_at'], unique=False)
    op.create_table("auto_renew_consents",
        sa.Column("token", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("snapshot", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("monthly_cap", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("expires_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("confirmed_at", sa.BigInteger(), primary_key=False, nullable=True),
    )
    op.create_index('ix_auto_renew_consents_service_code', 'auto_renew_consents', ['service_code'], unique=False)
    op.create_index('ix_auto_renew_consents_user_id', 'auto_renew_consents', ['user_id'], unique=False)
    op.create_table("auto_renew_notices",
        sa.Column("key", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("cycle_expire", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("kind", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("payload", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("state", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("sent_at", sa.BigInteger(), primary_key=False, nullable=True),
    )
    op.create_index('ix_auto_renew_notice_queue', 'auto_renew_notices', ['state', 'created_at'], unique=False)
    op.create_table("auto_renew_policies",
        sa.Column("service_code", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("panel_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("panel_userid", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("username", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("panel_url", sa.String(length=255), primary_key=False, nullable=False),
        sa.Column("plan_id", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("expected_values", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("per_charge_cap", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("monthly_cap", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("state", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("reason", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("next_check_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("updated_at", sa.BigInteger(), primary_key=False, nullable=False),
    )
    op.create_index('ix_auto_renew_due', 'auto_renew_policies', ['state', 'next_check_at'], unique=False)
    op.create_index('ix_auto_renew_policies_user_id', 'auto_renew_policies', ['user_id'], unique=False)


def downgrade():
    pending = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM auto_renew_attempts WHERE status IN ('applying', 'review')")).scalar()
    if pending:
        raise RuntimeError("Resolve funded auto-renew attempts before downgrade; disable the feature instead.")
    op.drop_table("auto_renew_policies")
    op.drop_table("auto_renew_notices")
    op.drop_table("auto_renew_consents")
    op.drop_table("auto_renew_attempts")
