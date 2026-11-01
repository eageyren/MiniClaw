"""Configurable characters and role switch audit."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_roles"
down_revision = "0002_voice_metrics"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("character_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_key", sa.String(40), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("family_id", "role_key", name="uq_character_roles_family_key"),
    )
    op.create_index("ix_character_roles_family_id", "character_roles", ["family_id"])
    op.add_column("conversations", sa.Column("role_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_conversations_role_id", "conversations", "character_roles", ["role_id"], ["id"])
    op.create_table("role_switches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_role", sa.String(40)), sa.Column("to_role", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_role_switches_family_id", "role_switches", ["family_id"])

def downgrade():
    op.drop_table("role_switches")
    op.drop_constraint("fk_conversations_role_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "role_id")
    op.drop_table("character_roles")
