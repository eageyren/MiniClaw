"""Persisted explicit confirmation workflow and staging reminders."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_confirmations"
down_revision = "0005_read_tools"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recite_text", sa.Text(), nullable=False),
        sa.Column("high_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resume_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_confirmations_family_id", "confirmations", ["family_id"])
    op.create_index("ix_confirmations_conversation_id", "confirmations", ["conversation_id"])
    op.create_index("ix_confirmations_status", "confirmations", ["status"])
    op.create_table("confirmation_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("confirmation_id", sa.String(36), sa.ForeignKey("confirmations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("recite_text", sa.Text(), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("confirmation_id", "step", name="uq_confirmation_audit_step"),
    )
    op.create_index("ix_confirmation_audits_confirmation_id", "confirmation_audits", ["confirmation_id"])
    op.create_index("ix_confirmation_audits_family_id", "confirmation_audits", ["family_id"])
    op.create_table("staging_reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("confirmation_id", sa.String(36), sa.ForeignKey("confirmations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_name", sa.String(80), nullable=False),
        sa.Column("recurrence", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_staging_reminders_family_id", "staging_reminders", ["family_id"])

def downgrade():
    op.drop_table("staging_reminders")
    op.drop_table("confirmation_audits")
    op.drop_table("confirmations")
