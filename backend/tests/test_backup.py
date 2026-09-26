import json
from datetime import datetime, timedelta, timezone

from app import backup
from app.config import settings


class FakeS3:
    def __init__(self, objects=None, fail_upload=False):
        self.objects = objects or {}
        self.fail_upload = fail_upload
        self.uploads = []
    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": key, "Size": len(value), "LastModified": modified} for key, (value, modified) in self.objects.items()]}
    def put_object(self, **kwargs):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = (body, datetime.now(timezone.utc))
        self.uploads.append(kwargs["Key"])
    def get_object(self, **kwargs):
        body, _ = self.objects[kwargs["Key"]]
        class Reader:
            def read(self): return body
        return {"Body": Reader()}


def configure(monkeypatch):
    for name, value in {
        "r2_endpoint_url": "https://account.r2.cloudflarestorage.com",
        "r2_access_key_id": "test-access",
        "r2_secret_access_key": "test-secret",
        "r2_bucket": "private-backups",
        "backup_encryption_key": backup.generate_encryption_key(),
        "r2_max_storage_bytes": 100_000,
    }.items():
        monkeypatch.setattr(settings, name, value)


def test_backup_encryption_round_trip():
    key = backup.generate_encryption_key()
    encrypted = backup.encrypt_backup(b"actual pg dump bytes", key)
    assert encrypted != b"actual pg dump bytes"
    assert backup.decrypt_backup(encrypted, key) == b"actual pg dump bytes"


def test_archive_name_uses_utc_date_and_time():
    assert backup.archive_key(datetime(2026, 9, 26, 12, 34, 56, tzinfo=timezone.utc)) == "backups/2026/09/26/stock-signal-123456.dump.gz.enc"


def test_neon_database_url_uses_tls_and_psycopg_driver():
    from app.db import normalized_database_url
    url = normalized_database_url("postgresql://user:password@ep-example.neon.tech/app")
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"


def test_backup_skips_when_latest_snapshot_is_less_than_one_day_old(monkeypatch):
    configure(monkeypatch)
    now = datetime.now(timezone.utc)
    key = backup.archive_key(now - timedelta(hours=2))
    client = FakeS3({key: (b"prior", now - timedelta(hours=2)), key + ".json": (b"manifest", now - timedelta(hours=2))})
    result = backup.create_backup_if_due(now, client=client, dump_bytes=b"dump", schema_revision="r1")
    assert result["status"] == "not_due"
    assert client.uploads == []


def test_orphan_archive_without_manifest_does_not_block_retry(monkeypatch):
    configure(monkeypatch)
    now = datetime.now(timezone.utc)
    key = backup.archive_key(now - timedelta(hours=2))
    client = FakeS3({key: (b"orphan archive", now - timedelta(hours=2))})
    result = backup.create_backup_if_due(now, client=client, dump_bytes=b"dump", schema_revision="r1")
    assert result["status"] == "uploaded"


def test_backup_refuses_upload_before_exceeding_configured_storage_cap(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(settings, "r2_max_storage_bytes", 200)
    now = datetime.now(timezone.utc)
    client = FakeS3({"old": (b"x" * 190, now - timedelta(days=2))})
    result = backup.create_backup_if_due(now, client=client, dump_bytes=b"large dump", schema_revision="r1")
    assert result["status"] == "storage_cap"
    assert client.uploads == []


def test_missing_backup_credentials_are_a_safe_noop(monkeypatch):
    monkeypatch.setattr(settings, "r2_bucket", "")
    result = backup.create_backup_if_due(dump_bytes=b"dump", schema_revision="r1")
    assert result["status"] == "not_configured"


def test_upload_failure_is_reported_without_leaking_credentials(monkeypatch, caplog):
    configure(monkeypatch)
    client = FakeS3(fail_upload=True)
    result = backup.create_backup_if_due(client=client, dump_bytes=b"dump", schema_revision="r1")
    assert result["status"] == "failed"
    assert "test-secret" not in caplog.text


def test_restore_refuses_different_migration_revision(monkeypatch):
    configure(monkeypatch)
    manifest = json.dumps({"schema_revision": "older"}).encode()
    client = FakeS3({"archive.dump.gz.enc.json": (manifest, datetime.now(timezone.utc))})
    result = backup.restore_backup("archive.dump.gz.enc", expected_revision="current", client=client)
    assert result["status"] == "schema_mismatch"
