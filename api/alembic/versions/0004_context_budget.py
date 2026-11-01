"""Persist rolling summaries and context/token metrics."""
from alembic import op
import sqlalchemy as sa

revision = "0004_context_budget"
down_revision = "0003_roles"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("messages", sa.Column("tokens_in", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("tokens_out", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("compressed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("messages", sa.Column("context_tokens", sa.Integer(), nullable=True))
    op.create_table("conversation_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("covered_through_message_id", sa.String(36)),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversation_summaries_family_id", "conversation_summaries", ["family_id"])

def downgrade():
    op.drop_table("conversation_summaries")
    op.drop_column("messages", "context_tokens")
    op.drop_column("messages", "compressed")
    op.drop_column("messages", "tokens_out")
    op.drop_column("messages", "tokens_in")
