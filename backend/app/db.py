from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

def normalized_database_url(value: str):
    url = make_url(value)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if (url.host or "").endswith(".neon.tech") and not url.query.get("sslmode"):
        url = url.update_query_dict({**dict(url.query), "sslmode": "require"})
    return url


engine = create_engine(normalized_database_url(settings.database_url), pool_pre_ping=True, pool_recycle=240, pool_size=2, max_overflow=1)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
