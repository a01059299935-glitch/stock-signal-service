"""Persist the single user's successful visit watermark."""
from alembic import op
import sqlalchemy as sa

revision = "0004_app_state"
down_revision = "0003_previous_close"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_successful_visit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("app_state")
