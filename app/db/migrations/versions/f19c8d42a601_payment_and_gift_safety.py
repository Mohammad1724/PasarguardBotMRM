"""Durable payment references, referral ledger and gift application state.

Revision ID: f19c8d42a601
Revises: e7f4a2b19c3d
"""
from alembic import op
import sqlalchemy as sa

revision = "f19c8d42a601"
down_revision = "e7f4a2b19c3d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cryptopayments") as batch:
        batch.add_column(sa.Column("checkout_query_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("next_check_at", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("gateway_ref_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("payment_ref", sa.String(255), nullable=True))
        batch.add_column(sa.Column("gateway_merchant", sa.String(64), nullable=True))
        batch.add_column(sa.Column("gateway_sandbox", sa.String(5), nullable=True))
        batch.create_unique_constraint("uq_crypto_payment_ref", ["payment_ref"])
    with op.batch_alter_table("gift_code_uses") as batch:
        batch.add_column(sa.Column("request_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="applied"))
        batch.add_column(sa.Column("plan", sa.Text(), nullable=True))
        batch.create_unique_constraint("uq_gift_request_id", ["request_id"])
    op.create_table("referral_rewards",
        sa.Column("source_key", sa.String(80), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("referrer_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("first_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("first_user_id", name="uq_referral_first_user"))
    op.create_index("ix_referral_rewards_user_id", "referral_rewards", ["user_id"])
    op.create_table("webhook_deliveries",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=False))

    op.create_index("ix_webhook_deliveries_completed_at", "webhook_deliveries", ["completed_at"])

def downgrade():
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_referral_rewards_user_id", table_name="referral_rewards")
    op.drop_table("referral_rewards")
    with op.batch_alter_table("gift_code_uses") as batch:
        batch.drop_constraint("uq_gift_request_id", type_="unique")
        for name in ("plan", "status", "request_id"):
            batch.drop_column(name)
    with op.batch_alter_table("cryptopayments") as batch:
        batch.drop_constraint("uq_crypto_payment_ref", type_="unique")
        for name in ("checkout_query_id", "next_check_at", "gateway_ref_id", "gateway_sandbox", "gateway_merchant", "payment_ref"):
            batch.drop_column(name)
