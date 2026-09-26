"""Store previous close for streaming daily change calculations."""
from alembic import op
import sqlalchemy as sa

revision = "0003_previous_close"
down_revision = "0002_news_published_at"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("snapshots", sa.Column("previous_close", sa.Float(), nullable=False, server_default="0"))

def downgrade():
    op.drop_column("snapshots", "previous_close")
