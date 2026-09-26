"""Store source publication time for news decay."""
from alembic import op
import sqlalchemy as sa

revision = "0002_news_published_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("news_events", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))

def downgrade():
    op.drop_column("news_events", "published_at")
