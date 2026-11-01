"""Add durable reminder scheduling, trigger and delivery records."""
from alembic import op
import sqlalchemy as sa

revision = "0007_real_reminders"
down_revision = "0006_confirmations"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("reminders", sa.Column("recurrence_rule", sa.String(300)))
    op.add_column("reminders", sa.Column("next_trigger_at", sa.DateTime(timezone=True)))
    op.add_column("reminders", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("reminders", sa.Column("idempotency_key", sa.String(100)))
    op.add_column("reminders", sa.Column("source_conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="SET NULL")))
    op.add_column("reminders", sa.Column("source_message_id", sa.String(36)))
    op.execute("UPDATE reminders SET next_trigger_at = due_at, idempotency_key = 'legacy:' || id WHERE idempotency_key IS NULL")
    op.alter_column("reminders", "idempotency_key", nullable=False)
    op.create_unique_constraint("uq_reminders_idempotency_key", "reminders", ["idempotency_key"])
    op.create_index("ix_reminders_next_trigger_at", "reminders", ["next_trigger_at"])
    op.execute("""
        INSERT INTO reminders (id, family_id, member_id, title, due_at, timezone_name, recurrence,
            recurrence_rule, next_trigger_at, status, version, idempotency_key,
            source_conversation_id, source_message_id)
        SELECT s.id, s.family_id, s.member_id, s.title, s.due_at, s.timezone_name, s.recurrence,
            CASE
              WHEN s.recurrence ILIKE '%不重复%' OR s.recurrence ILIKE '%一次%' THEN NULL
              WHEN s.recurrence ILIKE '%每天%' OR s.recurrence ILIKE '%每日%' THEN 'FREQ=DAILY'
              WHEN s.recurrence ILIKE '%每周%' OR s.recurrence ILIKE '%每星期%' THEN 'FREQ=WEEKLY'
              WHEN s.recurrence ILIKE '%每月%' THEN 'FREQ=MONTHLY;BYMONTHDAY=' || EXTRACT(DAY FROM s.due_at)::int
              WHEN s.recurrence ILIKE '%每年%' THEN 'FREQ=YEARLY;BYMONTH=' || EXTRACT(MONTH FROM s.due_at)::int || ';BYMONTHDAY=' || EXTRACT(DAY FROM s.due_at)::int
              ELSE NULL
            END,
            s.due_at, 'scheduled', 1, 'confirmation:' || s.confirmation_id,
            c.conversation_id, c.payload->>'source_message_id'
        FROM staging_reminders s JOIN confirmations c ON c.id = s.confirmation_id
        WHERE c.status = 'confirmed'
          AND (s.recurrence ILIKE '%不重复%' OR s.recurrence ILIKE '%一次%' OR s.recurrence ILIKE '%每天%' OR s.recurrence ILIKE '%每日%' OR s.recurrence ILIKE '%每周%' OR s.recurrence ILIKE '%每星期%' OR s.recurrence ILIKE '%每月%' OR s.recurrence ILIKE '%每年%')
        ON CONFLICT (idempotency_key) DO NOTHING
    """)
    op.create_table("reminder_triggers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("reminder_id", sa.String(36), sa.ForeignKey("reminders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_catch_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("reminder_id", "scheduled_for", name="uq_reminder_trigger_occurrence"))
    op.create_index("ix_reminder_triggers_reminder_id", "reminder_triggers", ["reminder_id"])
    op.create_index("ix_reminder_triggers_family_id", "reminder_triggers", ["family_id"])
    op.create_table("reminder_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trigger_id", sa.String(36), sa.ForeignKey("reminder_triggers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False, server_default="in_app"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)))
    op.create_index("ix_reminder_deliveries_trigger_id", "reminder_deliveries", ["trigger_id"])

def downgrade():
    op.drop_table("reminder_deliveries")
    op.drop_table("reminder_triggers")
    op.drop_index("ix_reminders_next_trigger_at", table_name="reminders")
    op.drop_constraint("uq_reminders_idempotency_key", "reminders", type_="unique")
    for name in ("source_message_id", "source_conversation_id", "idempotency_key", "version", "next_trigger_at", "recurrence_rule"):
        op.drop_column("reminders", name)
