"""Request traces, funnel events and audio cost inputs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_observability_cost"
down_revision = "0011_reminder_delivery"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("request_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="SET NULL")),
        sa.Column("member_id", sa.String(64)), sa.Column("actor_role", sa.String(16)),
        sa.Column("route", sa.String(200), nullable=False), sa.Column("status_code", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("duration_ms", sa.Integer()))
    op.create_index("ix_request_traces_family_id", "request_traces", ["family_id"])
    op.create_table("trace_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("request_traces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.String(64)), sa.Column("span_name", sa.String(80), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_trace_spans_request_id", "trace_spans", ["request_id"])
    op.create_table("usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("session_key", sa.String(80), nullable=False), sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_usage_events_family_id", "usage_events", ["family_id"])
    op.create_index("ix_usage_events_session_key", "usage_events", ["session_key"])
    op.create_index("ix_usage_events_event_type", "usage_events", ["event_type"])
    op.add_column("voice_metrics", sa.Column("audio_duration_ms", sa.Integer()))
    op.add_column("voice_metrics", sa.Column("tts_characters", sa.Integer()))
    op.add_column("reminders", sa.Column("origin_request_id", sa.String(36)))
    op.create_index("ix_reminders_origin_request_id", "reminders", ["origin_request_id"])

def downgrade():
    op.drop_index("ix_reminders_origin_request_id", table_name="reminders")
    op.drop_column("reminders", "origin_request_id")
    op.drop_column("voice_metrics", "tts_characters")
    op.drop_column("voice_metrics", "audio_duration_ms")
    op.drop_table("usage_events")
    op.drop_table("trace_spans")
    op.drop_table("request_traces")
