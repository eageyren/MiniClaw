"""Pairing, family OTP auth, revocable tokens and role/tool policy."""
from alembic import op
import sqlalchemy as sa

revision = "0010_identity_auth"
down_revision = "0009_family_settings"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("families", sa.Column("timezone_name", sa.String(80), nullable=False, server_default="Asia/Shanghai"))
    op.create_table("family_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone_number", sa.String(24), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default="家属"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("phone_number", name="uq_family_accounts_phone"))
    op.create_index("ix_family_accounts_family_id", "family_accounts", ["family_id"])
    op.create_table("family_access",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_account_id", sa.String(36), sa.ForeignKey("family_accounts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_family_access_family_id", "family_access", ["family_id"])
    op.create_table("pairing_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("enrollment_secret_hash", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_pairing_codes_family_id", "pairing_codes", ["family_id"])
    op.create_table("otp_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("phone_number", sa.String(24), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_otp_codes_phone_number", "otp_codes", ["phone_number"])
    op.create_table("device_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pairing_code_id", sa.String(36), sa.ForeignKey("pairing_codes.id", ondelete="SET NULL"), unique=True),
        sa.Column("refresh_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("device_label", sa.String(120), nullable=False, server_default="长辈设备"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_device_credentials_family_id", "device_credentials", ["family_id"])
    op.create_table("access_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(64), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_account_id", sa.String(36), sa.ForeignKey("family_accounts.id", ondelete="CASCADE")),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("device_credentials.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_access_sessions_family_id", "access_sessions", ["family_id"])
    op.create_table("role_tool_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("family_id", sa.String(64), sa.ForeignKey("families.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("character_roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("family_id", "role_id", "tool_name", name="uq_role_tool_permission"))
    op.create_index("ix_role_tool_permissions_family_id", "role_tool_permissions", ["family_id"])

def downgrade():
    op.drop_table("role_tool_permissions")
    op.drop_table("access_sessions")
    op.drop_table("device_credentials")
    op.drop_table("otp_codes")
    op.drop_table("pairing_codes")
    op.drop_table("family_access")
    op.drop_table("family_accounts")
    op.drop_column("families", "timezone_name")
