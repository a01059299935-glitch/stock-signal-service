"""Initial stock signal schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("watchlist", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticker", sa.String(12), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_watchlist_ticker", "watchlist", ["ticker"], unique=True)
    op.create_table("snapshots", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticker", sa.String(12), nullable=False), sa.Column("price", sa.Float(), nullable=False), sa.Column("change_pct", sa.Float(), nullable=False), sa.Column("volume", sa.Integer(), nullable=False), sa.Column("market_cap", sa.Float(), nullable=False), sa.Column("score", sa.Integer(), nullable=False), sa.Column("signal", sa.String(20), nullable=False), sa.Column("confidence", sa.Integer(), nullable=False), sa.Column("reasons", sa.Text(), nullable=False), sa.Column("risks", sa.Text(), nullable=False), sa.Column("indicators", sa.Text(), nullable=False), sa.Column("candles", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_snapshots_ticker", "snapshots", ["ticker"]); op.create_index("ix_snapshots_updated_at", "snapshots", ["updated_at"])
    op.create_table("news_events", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticker", sa.String(12), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("title", sa.Text(), nullable=False), sa.Column("source", sa.String(120), nullable=False), sa.Column("url", sa.Text(), nullable=False), sa.Column("summary", sa.Text(), nullable=False), sa.Column("sentiment", sa.Integer(), nullable=False), sa.Column("impact", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("ticker", "fingerprint", name="uq_ticker_news_fingerprint"))
    for name, table, col in [("ix_news_events_ticker", "news_events", "ticker"), ("ix_news_events_fingerprint", "news_events", "fingerprint"), ("ix_news_events_created_at", "news_events", "created_at")]: op.create_index(name, table, [col])
    op.create_table("signal_history", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("ticker", sa.String(12), nullable=False), sa.Column("score", sa.Integer(), nullable=False), sa.Column("signal", sa.String(20), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_signal_history_ticker", "signal_history", ["ticker"]); op.create_index("ix_signal_history_created_at", "signal_history", ["created_at"])

def downgrade():
    op.drop_table("signal_history"); op.drop_table("news_events"); op.drop_table("snapshots"); op.drop_table("watchlist")
