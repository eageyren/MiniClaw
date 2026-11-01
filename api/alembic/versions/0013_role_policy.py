"""Default role grants and dedicated cancellation permission."""
from alembic import op
import sqlalchemy as sa

revision = "0013_role_policy"
down_revision = "0012_observability_cost"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE role_tool_permissions AS permission
        SET enabled = false
        FROM character_roles AS role
        WHERE permission.role_id = role.id
          AND permission.family_id = role.family_id
          AND role.role_key = 'granddaughter'
          AND permission.tool_name IN ('propose_reminder', 'propose_reminder_change')
    """))
    connection.execute(sa.text("""
        INSERT INTO role_tool_permissions (id, family_id, role_id, tool_name, enabled)
        SELECT gen_random_uuid()::text, family_id, id, 'cancel_reminder', role_key = 'daughter'
        FROM character_roles
        ON CONFLICT (family_id, role_id, tool_name)
        DO UPDATE SET enabled = EXCLUDED.enabled
    """))


def downgrade():
    op.execute("DELETE FROM role_tool_permissions WHERE tool_name = 'cancel_reminder'")
