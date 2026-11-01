"""Audit family-facing configuration and access changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_family_settings"
down_revision = "0008_memories"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("family_change_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("before_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("after_value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("elder_summary", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_family_change_audits_family_id", "family_change_audits", ["family_id"])

def downgrade():
    op.drop_table("family_change_audits")
