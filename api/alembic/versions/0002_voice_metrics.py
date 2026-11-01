"""Persist voice pipeline timings."""
from alembic import op
import sqlalchemy as sa

revision = "0002_voice_metrics"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("voice_metrics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stt_ms", sa.Integer(), nullable=False),
        sa.Column("llm_first_token_ms", sa.Integer(), nullable=False),
        sa.Column("tts_first_byte_ms", sa.Integer(), nullable=False),
        sa.Column("playback_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_voice_metrics_family_id", "voice_metrics", ["family_id"])
    op.create_index("ix_voice_metrics_conversation_id", "voice_metrics", ["conversation_id"])

def downgrade():
    op.drop_table("voice_metrics")
