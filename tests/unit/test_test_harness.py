from __future__ import annotations

import sqlite3

from tests import conftest as harness


def test_sqlite_snapshot_is_read_only_and_detects_wal_changes(tmp_path):
    database_path = tmp_path / "snapshot.db"
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE events (value TEXT)")
    connection.commit()

    before = harness._calculate_sqlite_sha256(database_path)
    database_bytes = database_path.read_bytes()
    wal_path = database_path.with_name(f"{database_path.name}-wal")
    wal_bytes = wal_path.read_bytes() if wal_path.exists() else None

    assert harness._calculate_sqlite_sha256(database_path) == before
    assert database_path.read_bytes() == database_bytes
    assert (wal_path.read_bytes() if wal_path.exists() else None) == wal_bytes

    connection.execute("INSERT INTO events VALUES ('changed')")
    connection.commit()

    assert harness._calculate_sqlite_sha256(database_path) != before
    connection.close()


def test_temp_test_database_cleanup_removes_isolated_directory(tmp_path, monkeypatch):
    isolated_dir = tmp_path / "isolation"
    isolated_dir.mkdir()
    (isolated_dir / "test.db").write_bytes(b"database")
    monkeypatch.setattr(harness, "_TEMP_TEST_DIR", str(isolated_dir))

    harness._cleanup_temp_test_dir()

    assert not isolated_dir.exists()
