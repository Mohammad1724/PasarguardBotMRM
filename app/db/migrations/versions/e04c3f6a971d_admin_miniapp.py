"""Admin Mini App grants, drafts, published layout and plan availability."""
from alembic import op
import sqlalchemy as sa
revision="e04c3f6a971d"
down_revision="d93b2e5f860c"
branch_labels=None
depends_on=None


def upgrade():
    op.add_column("plans",sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.text("1")))
    op.create_table("admin_app_grants",
        sa.Column("user_id",sa.BigInteger(),primary_key=True),
        sa.Column("name",sa.String(60),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False,server_default=sa.text("0")),
        sa.Column("permissions",sa.JSON(),nullable=False),
        sa.Column("updated_at",sa.BigInteger(),nullable=False))
    op.create_table("admin_app_state",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("layout",sa.JSON(),nullable=False))
    op.execute(sa.text("INSERT INTO admin_app_state (id,layout) VALUES (1,'[]')"))
    op.create_table("admin_app_changes",
        sa.Column("token",sa.String(32),primary_key=True),
        sa.Column("actor_id",sa.BigInteger(),nullable=False),
        sa.Column("entity",sa.String(24),nullable=False),sa.Column("target",sa.String(100),nullable=False),
        sa.Column("before",sa.JSON(),nullable=False),sa.Column("after",sa.JSON(),nullable=False),
        sa.Column("reason",sa.Text(),nullable=False),sa.Column("status",sa.String(16),nullable=False),
        sa.Column("created_at",sa.BigInteger(),nullable=False),sa.Column("published_at",sa.BigInteger(),nullable=True),
        sa.Column("result",sa.JSON(),nullable=True))
    for key in ("actor_id","status","created_at"):
        op.create_index("ix_admin_app_changes_"+key,"admin_app_changes",[key])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM plans WHERE enabled=0")).scalar():
        raise RuntimeError("Disabled plans would become sellable in old code. Resolve them explicitly before downgrade.")
    op.execute(sa.text("UPDATE keyboard_buttons SET button_icon=NULL WHERE button_icon=0"))
    op.drop_table("admin_app_changes")
    op.drop_table("admin_app_state")
    op.drop_table("admin_app_grants")
    op.drop_column("plans","enabled")
