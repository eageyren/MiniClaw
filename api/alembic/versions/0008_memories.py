"""Add scoped, attributable and revocable conversation memories."""
from alembic import op
import sqlalchemy as sa

revision = "0008_memories"
down_revision = "0007_real_reminders"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("character_roles.id", ondelete="SET NULL")),
        sa.Column("content", sa.String(500), nullable=False),
        sa.Column("source_conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_message_id", sa.String(36), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_memories_family_id", "memories", ["family_id"])
    op.create_index("ix_memories_member_id", "memories", ["member_id"])
    op.create_index("ix_memories_status", "memories", ["status"])

def downgrade():
    op.drop_table("memories")
