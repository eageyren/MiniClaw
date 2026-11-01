"""Store end-to-end voice completion latency."""
from alembic import op
import sqlalchemy as sa

revision = "0014_voice_end_to_end"
down_revision = "0013_role_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("voice_metrics", sa.Column("end_to_end_ms", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("voice_metrics", "end_to_end_ms")
