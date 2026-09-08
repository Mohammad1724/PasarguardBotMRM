"""Customer experience v1: tickets, consented trial journeys and conversion intents."""
from alembic import op
import sqlalchemy as sa

revision = "c82a1d9e740b"
down_revision = "f19c8d42a601"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cx_audit",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, nullable=False, autoincrement=True),
        sa.Column("actor_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("action", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("detail", sa.Text(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
    )
    op.create_table("cx_events",
        sa.Column("key", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("name", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
    )
    op.create_index('ix_cx_events_created_at', 'cx_events', ['created_at'], unique=False)
    op.create_index('ix_cx_events_name', 'cx_events', ['name'], unique=False)
    op.create_index('ix_cx_events_user_id', 'cx_events', ['user_id'], unique=False)
    op.create_table("cx_ticket_messages",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, nullable=False, autoincrement=True),
        sa.Column("ticket_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("author_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("kind", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("text", sa.Text(), primary_key=False, nullable=False),
        sa.Column("file_id", sa.Text(), primary_key=False, nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.UniqueConstraint('source_chat_id', 'source_message_id', name='uq_cx_ticket_source'),
    )
    op.create_index('ix_cx_message_author_time', 'cx_ticket_messages', ['author_id', 'created_at'], unique=False)
    op.create_index('ix_cx_ticket_messages_ticket_id', 'cx_ticket_messages', ['ticket_id'], unique=False)
    op.create_table("cx_tickets",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, nullable=False, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("topic", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("assigned_to", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("updated_at", sa.BigInteger(), primary_key=False, nullable=False),
    )
    op.create_index('ix_cx_ticket_queue', 'cx_tickets', ['status', 'updated_at'], unique=False)
    op.create_index('ix_cx_tickets_user_id', 'cx_tickets', ['user_id'], unique=False)
    op.create_table("cx_trial_conversions",
        sa.Column("token", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("service_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("active_service_code", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("panel_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("panel_userid", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("username", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("plan_id", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("price", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("old_values", sa.JSON(), primary_key=False, nullable=True),
        sa.Column("target_values", sa.JSON(), primary_key=False, nullable=True),
        sa.Column("status", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("attempted", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("expires_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("completed_at", sa.BigInteger(), primary_key=False, nullable=True),
        sa.UniqueConstraint('active_service_code', name=None),
    )
    op.create_index('ix_cx_trial_conversions_service_code', 'cx_trial_conversions', ['service_code'], unique=False)
    op.create_index('ix_cx_trial_conversions_status', 'cx_trial_conversions', ['status'], unique=False)
    op.create_index('ix_cx_trial_conversions_user_id', 'cx_trial_conversions', ['user_id'], unique=False)
    op.create_table("cx_trial_journeys",
        sa.Column("service_code", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("panel_code", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("expires_at", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("ended_at", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("retain_until", sa.BigInteger(), primary_key=False, nullable=False),
        sa.Column("opted_in", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column("experiment_group", sa.String(length=16), primary_key=False, nullable=False),
        sa.Column("followup_status", sa.String(length=24), primary_key=False, nullable=False),
        sa.Column("consented_at", sa.BigInteger(), nullable=True),
        sa.Column("attempted_at", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("clicked_at", sa.BigInteger(), primary_key=False, nullable=True),
        sa.Column("first_purchase_at", sa.BigInteger(), primary_key=False, nullable=True),
    )
    op.create_index('ix_cx_journey_due', 'cx_trial_journeys', ['followup_status', 'ended_at'], unique=False)
    op.create_index('ix_cx_trial_journeys_user_id', 'cx_trial_journeys', ['user_id'], unique=False)


def downgrade():
    # Never discard a funded but unresolved external operation.
    pending = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM cx_trial_conversions WHERE status = 'applying'")).scalar()
    if pending:
        raise RuntimeError("Resolve all funded trial conversions before downgrading; prefer disabling CX flags instead.")
    op.drop_table("cx_trial_journeys")
    op.drop_table("cx_trial_conversions")
    op.drop_table("cx_tickets")
    op.drop_table("cx_ticket_messages")
    op.drop_table("cx_events")
    op.drop_table("cx_audit")
