import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .db import engine as target_engine
from .models import AppState, NewsEvent, SignalHistory, Snapshot, WatchItem

MODELS = (WatchItem, Snapshot, NewsEvent, SignalHistory, AppState)


def transfer_database(source_engine, destination_engine):
    """Copy a pre-deployment database once into an empty migrated database."""
    source_tables = set(inspect(source_engine).get_table_names())
    destination_tables = set(inspect(destination_engine).get_table_names())
    missing = [model.__tablename__ for model in MODELS if model.__tablename__ not in destination_tables]
    if missing:
        raise ValueError("destination database is missing migrated tables: " + ", ".join(missing))

    counts = {}
    with destination_engine.begin() as destination:
        for model in MODELS:
            table_name = model.__tablename__
            if table_name in destination_tables and destination.execute(select(model.id).limit(1)).first():
                raise ValueError("destination database must be empty before importing local records")
        for model in MODELS:
            table_name = model.__tablename__
            if table_name not in source_tables:
                counts[table_name] = 0
                continue
            with Session(source_engine) as source:
                rows = source.scalars(select(model).order_by(model.id)).all()
                values = [{column.key: getattr(row, column.key) for column in model.__table__.columns} for row in rows]
            if values:
                destination.execute(model.__table__.insert(), values)
            counts[table_name] = len(values)
            if destination_engine.dialect.name == "postgresql":
                quoted = destination_engine.dialect.identifier_preparer.quote(table_name)
                destination.execute(text(f"SELECT setval(pg_get_serial_sequence(:table_name, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {quoted}"), {"table_name": table_name})
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import existing local stock history into an empty migrated Neon database")
    parser.add_argument("sqlite_file", help="path to the current SQLite database file")
    args = parser.parse_args()
    source_path = Path(args.sqlite_file).expanduser().resolve()
    if not source_path.is_file():
        parser.error("SQLite source file does not exist")
    from sqlalchemy import create_engine
    source_url = make_url("sqlite:///" + source_path.as_posix())
    source_engine = create_engine(source_url)
    try:
        counts = transfer_database(source_engine, target_engine)
        print(json.dumps({"status": "imported", "counts": counts}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "reason": type(exc).__name__}, ensure_ascii=False))
        sys.exit(1)
    finally:
        source_engine.dispose()
