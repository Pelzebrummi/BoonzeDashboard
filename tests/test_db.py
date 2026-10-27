import sqlite3
from contextlib import closing

import pytest

from app import db


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "test.db"
    db.migrate(path)
    db.migrate(path)
    with closing(db.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_failed_migration_leaves_the_database_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    db.migrate(path)
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, "CREATE TABLE extra (id INTEGER); SELECT * FROM missing;"])
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(path)
    with closing(db.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'extra'").fetchone() is None


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "test.db"
    db.migrate(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        db.migrate(path)


def test_deleting_a_document_keeps_its_entries(tmp_path):
    path = tmp_path / "test.db"
    db.migrate(path)
    with closing(db.connect(path)) as conn:
        document_id = db.insert_document(
            conn, filename="a.pdf", sha256="x", size_bytes=1, page_count=1, text="", uploaded_by=None
        )
        db.insert_entry(
            conn,
            kind="zaehlerstand",
            medium="strom",
            period_start=None,
            period_end="2025-12-31",
            value=1.0,
            unit="kWh",
            document_id=document_id,
        )
        db.delete_document(conn, document_id)
        [entry] = db.list_entries(conn)
        assert entry["document_id"] is None


def test_kind_is_checked_by_the_database(tmp_path):
    path = tmp_path / "test.db"
    db.migrate(path)
    with closing(db.connect(path)) as conn, pytest.raises(sqlite3.IntegrityError):
        db.insert_entry(
            conn, kind="unsinn", medium="strom", period_start=None, period_end="2025-12-31", value=1.0, unit="kWh"
        )
