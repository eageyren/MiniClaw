"""Push subscriptions, delivery retries and escalation state."""
from alembic import op
import sqlalchemy as sa

revision = "0011_reminder_delivery"
down_revision = "0010_identity_auth"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("push_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("device_credentials.id", ondelete="CASCADE")),
        sa.Column("family_account_id", sa.String(36), sa.ForeignKey("family_accounts.id", ondelete="CASCADE")),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth_key", sa.String(100), nullable=False),
        sa.Column("notify_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_push_subscriptions_family_id", "push_subscriptions", ["family_id"])
    op.add_column("reminder_deliveries", sa.Column("idempotency_key", sa.String(180)))
    op.add_column("reminder_deliveries", sa.Column("target_subscription_id", sa.String(36), sa.ForeignKey("push_subscriptions.id", ondelete="SET NULL")))
    op.add_column("reminder_deliveries", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("reminder_deliveries", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("reminder_deliveries", sa.Column("delivered_at", sa.DateTime(timezone=True)))
    op.add_column("reminder_deliveries", sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute("UPDATE reminder_deliveries SET idempotency_key = 'legacy:' || id WHERE idempotency_key IS NULL")
    op.alter_column("reminder_deliveries", "idempotency_key", nullable=False)
    op.create_unique_constraint("uq_reminder_delivery_idempotency", "reminder_deliveries", ["idempotency_key"])
    op.create_index("ix_reminder_deliveries_next_attempt_at", "reminder_deliveries", ["next_attempt_at"])

def downgrade():
    op.drop_index("ix_reminder_deliveries_next_attempt_at", table_name="reminder_deliveries")
    op.drop_constraint("uq_reminder_delivery_idempotency", "reminder_deliveries", type_="unique")
    for name in ("status_updated_at", "delivered_at", "next_attempt_at", "attempt_count", "target_subscription_id", "idempotency_key"):
        op.drop_column("reminder_deliveries", name)
    op.drop_table("push_subscriptions")
