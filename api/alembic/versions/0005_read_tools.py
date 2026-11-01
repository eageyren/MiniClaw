"""Read-only tools and tool execution audit payloads."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_read_tools"
down_revision = "0004_context_budget"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("messages", sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table("reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(80), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("recurrence", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reminders_family_id", "reminders", ["family_id"])
    op.create_index("ix_reminders_due_at", "reminders", ["due_at"])
    op.create_index("ix_reminders_status", "reminders", ["status"])
    op.create_table("family_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_type", sa.String(40), nullable=False),
        sa.Column("content", sa.String(1000), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="shared"),
        sa.Column("authorized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_family_facts_family_id", "family_facts", ["family_id"])

def downgrade():
    op.drop_table("family_facts")
    op.drop_table("reminders")
    op.drop_column("messages", "tool_calls")
