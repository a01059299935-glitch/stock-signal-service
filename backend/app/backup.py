import gzip
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

import boto3
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import make_url

from .config import settings

logger = logging.getLogger(__name__)
UTC = timezone.utc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("ascii")


def encrypt_backup(data: bytes, key: str) -> bytes:
    return Fernet(key.encode("ascii")).encrypt(data)


def decrypt_backup(data: bytes, key: str) -> bytes:
    return Fernet(key.encode("ascii")).decrypt(data)


def archive_key(now: datetime | None = None) -> str:
    value = (now or datetime.now(UTC)).astimezone(UTC)
    return f"backups/{value:%Y/%m/%d}/stock-signal-{value:%H%M%S}.dump.gz.enc"


def _configured() -> bool:
    return all((settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket, settings.backup_encryption_key))


def _client():
    return boto3.client("s3", endpoint_url=settings.r2_endpoint_url, aws_access_key_id=settings.r2_access_key_id, aws_secret_access_key=settings.r2_secret_access_key, region_name="auto")


def _objects(client):
    token = None
    rows = []
    while True:
        args = {"Bucket": settings.r2_bucket, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        response = client.list_objects_v2(**args)
        rows.extend(response.get("Contents", []))
        if not response.get("IsTruncated"):
            return rows
        token = response.get("NextContinuationToken")
        if not token:
            return rows


def _dump_database() -> bytes:
    url = make_url(settings.database_url)
    env = os.environ.copy()
    for key, value in (("PGHOST", url.host), ("PGPORT", str(url.port or 5432)), ("PGDATABASE", url.database), ("PGUSER", url.username), ("PGPASSWORD", url.password)):
        if value:
            env[key] = value
    query = dict(url.query)
    env["PGSSLMODE"] = str(query.get("sslmode") or ("require" if (url.host or "").endswith(".neon.tech") else "prefer"))
    result = subprocess.run(["pg_dump", "--format=custom", "--no-owner"], check=True, capture_output=True, env=env)
    return result.stdout


def _secret_env():
    url = make_url(settings.database_url)
    env = os.environ.copy()
    for key, value in (("PGHOST", url.host), ("PGPORT", str(url.port or 5432)), ("PGDATABASE", url.database), ("PGUSER", url.username), ("PGPASSWORD", url.password)):
        if value:
            env[key] = value
    env["PGSSLMODE"] = str(dict(url.query).get("sslmode") or ("require" if (url.host or "").endswith(".neon.tech") else "prefer"))
    return env


def _schema_revision() -> str:
    from .db import engine
    from sqlalchemy import text
    with engine.connect() as connection:
        return str(connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one())


def create_backup_if_due(now=None, client=None, dump_bytes=None, schema_revision=None):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if not _configured():
        return {"status": "not_configured"}
    try:
        client = client or _client()
        objects = _objects(client)
        used = sum(int(item.get("Size", 0)) for item in objects)
        max_bytes = settings.r2_max_storage_bytes
        if used >= max_bytes:
            return {"status": "storage_cap", "used_bytes": used, "max_bytes": max_bytes}
        object_keys = {str(item.get("Key", "")) for item in objects}
        archives = [item for item in objects if str(item.get("Key", "")).endswith(".dump.gz.enc") and str(item.get("Key", "")) + ".json" in object_keys]
        newest = max((item.get("LastModified") for item in archives if item.get("LastModified")), default=None)
        if newest and (now - newest.astimezone(UTC)).total_seconds() < 86400:
            return {"status": "not_due", "used_bytes": used, "max_bytes": max_bytes}
        if used >= max_bytes * .8:
            logger.warning("R2 backup storage near limit: used_bytes=%s max_bytes=%s", used, max_bytes)
        raw_dump = dump_bytes if dump_bytes is not None else _dump_database()
        encrypted = encrypt_backup(gzip.compress(raw_dump, compresslevel=6), settings.backup_encryption_key)
        manifest = {"created_at": now.isoformat(), "schema_revision": schema_revision or _schema_revision(), "archive_sha256": hashlib.sha256(encrypted).hexdigest(), "archive_size": len(encrypted), "format": "pg_dump-custom+gzip+fernet-v1"}
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
        if used + len(encrypted) + len(manifest_bytes) > max_bytes:
            return {"status": "storage_cap", "used_bytes": used, "max_bytes": max_bytes}
        key = archive_key(now)
        client.put_object(Bucket=settings.r2_bucket, Key=key, Body=encrypted, ContentType="application/octet-stream", Metadata={"sha256": manifest["archive_sha256"]})
        client.put_object(Bucket=settings.r2_bucket, Key=key + ".json", Body=manifest_bytes, ContentType="application/json")
        return {"status": "uploaded", "key": key, "used_bytes": used + len(encrypted) + len(manifest_bytes), "max_bytes": max_bytes}
    except Exception as exc:
        logger.warning("R2 backup failed (%s)", type(exc).__name__)
        return {"status": "failed"}


def restore_backup(key: str, expected_revision: str, client=None):
    if not _configured():
        return {"status": "not_configured"}
    try:
        client = client or _client()
        manifest_obj = client.get_object(Bucket=settings.r2_bucket, Key=key + ".json")
        manifest = json.loads(manifest_obj["Body"].read())
        if manifest.get("schema_revision") != expected_revision:
            return {"status": "schema_mismatch", "archive_revision": manifest.get("schema_revision"), "expected_revision": expected_revision}
        encrypted = client.get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
        if hashlib.sha256(encrypted).hexdigest() != manifest.get("archive_sha256"):
            return {"status": "checksum_mismatch"}
        payload = gzip.decompress(decrypt_backup(encrypted, settings.backup_encryption_key))
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=True) as dump:
            dump.write(payload)
            dump.flush()
            database = make_url(settings.database_url).database
            subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", database, dump.name], check=True, capture_output=True, env=_secret_env())
        return {"status": "restored", "schema_revision": expected_revision}
    except (InvalidToken, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        logger.warning("R2 restore failed (%s)", type(exc).__name__)
        return {"status": "failed"}


def storage_status(client=None):
    if not _configured():
        return {"status": "not_configured", "used_bytes": None, "max_bytes": settings.r2_max_storage_bytes}
    try:
        client = client or _client()
        used = sum(int(item.get("Size", 0)) for item in _objects(client))
        return {"status": "near_limit" if used >= settings.r2_max_storage_bytes * .8 else "online", "used_bytes": used, "max_bytes": settings.r2_max_storage_bytes}
    except Exception as exc:
        logger.warning("R2 storage status unavailable (%s)", type(exc).__name__)
        return {"status": "unavailable", "used_bytes": None, "max_bytes": settings.r2_max_storage_bytes}


def _application_schema_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Stock Signal encrypted R2 archive tools")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("generate-key", help="print a new Fernet key for BACKUP_ENCRYPTION_KEY")
    restore = subcommands.add_parser("restore", help="restore a backup only when its schema matches this application")
    restore.add_argument("key", help="R2 object key printed after a successful archive")
    args = parser.parse_args()
    if args.command == "generate-key":
        print(generate_encryption_key())
        sys.exit(0)
    result = restore_backup(args.key, _application_schema_head())
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("status") == "restored" else 1)
