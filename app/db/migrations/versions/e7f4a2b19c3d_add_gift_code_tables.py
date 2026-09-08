"""add gift codes tables

Revision ID: e7f4a2b19c3d
Revises: a1b2c3d4e5f6
Create Date: 2026-09-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7f4a2b19c3d'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gift_codes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=60), nullable=False),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('value', sa.BigInteger(), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('times_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('per_user_limit', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('expires_at', sa.BigInteger(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )
    op.create_index('ix_gift_codes_code', 'gift_codes', ['code'], unique=True)
    op.create_table(
        'gift_code_uses',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('code_id', sa.BigInteger(), nullable=False),
        sa.Column('code', sa.String(length=60), nullable=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('service_code', sa.String(length=100), nullable=True),
        sa.Column('value', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('used_at', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_gift_code_uses_code_id', 'gift_code_uses', ['code_id'], unique=False)
    op.create_index('ix_gift_code_uses_user_id', 'gift_code_uses', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_gift_code_uses_user_id', table_name='gift_code_uses')
    op.drop_index('ix_gift_code_uses_code_id', table_name='gift_code_uses')
    op.drop_table('gift_code_uses')
    op.drop_index('ix_gift_codes_code', table_name='gift_codes')
    op.drop_table('gift_codes')
