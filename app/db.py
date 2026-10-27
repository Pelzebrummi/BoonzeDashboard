"""SQLite storage in /data with PRAGMA user_version based migrations.

Never edit a migration that has shipped; append a new one instead. The app's
/data survives updates, so every deployed version must migrate older databases.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

KINDS = {
    "verbrauch": "Verbrauch",
    "zaehlerstand": "Zählerstand",
    "betrag": "Betrag / Abbuchung",
}
MEDIA_LABELS = {"strom": "Strom", "gas": "Gas", "wasser": "Wasser", "sonstiges": "Sonstiges"}
UNITS = ("kWh", "m³", "EUR")

MIGRATIONS = [
    """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL,
        page_count INTEGER NOT NULL,
        text TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        uploaded_by TEXT
    );
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY,
        document_id INTEGER REFERENCES documents (id) ON DELETE SET NULL,
        kind TEXT NOT NULL CHECK (kind IN ('verbrauch', 'zaehlerstand', 'betrag')),
        medium TEXT NOT NULL CHECK (medium IN ('strom', 'gas', 'wasser', 'sonstiges')),
        period_start TEXT,
        period_end TEXT NOT NULL,
        value REAL NOT NULL,
        unit TEXT NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL,
        created_by TEXT
    );
    CREATE INDEX entries_by_medium ON entries (medium, period_end);
    """,
]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(path: Path) -> None:
    """Apply pending migrations, each in its own transaction.

    Foreign keys stay off while migrating (SQLite's table-rebuild procedure
    requires it; otherwise ON DELETE actions fire on DROP TABLE) and are
    checked before each commit instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError(
                f"Database schema {version} is newer than this app version knows ({len(MIGRATIONS)})."
            )
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            try:
                conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number};")
                if violations := conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError(f"Migration {number} breaks foreign keys: {violations[:5]}")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def insert_document(
    conn: sqlite3.Connection,
    *,
    filename: str,
    sha256: str,
    size_bytes: int,
    page_count: int,
    text: str,
    uploaded_by: str | None,
) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO documents (filename, sha256, size_bytes, page_count, text, uploaded_at, uploaded_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (filename, sha256, size_bytes, page_count, text, _now(), uploaded_by),
        )
    return cursor.lastrowid


def find_document_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()


def get_document(conn: sqlite3.Connection, document_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()


def list_documents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT d.id, d.filename, d.uploaded_at, d.uploaded_by, d.page_count,"
        " (SELECT COUNT(*) FROM entries e WHERE e.document_id = d.id) AS entry_count"
        " FROM documents d ORDER BY d.uploaded_at DESC"
    ).fetchall()


def delete_document(conn: sqlite3.Connection, document_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


INSERT_ENTRY = (
    "INSERT INTO entries (document_id, kind, medium, period_start, period_end, value, unit, note,"
    " created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def insert_entry(
    conn: sqlite3.Connection,
    *,
    kind: str,
    medium: str,
    period_start: str | None,
    period_end: str,
    value: float,
    unit: str,
    note: str | None = None,
    document_id: int | None = None,
    created_by: str | None = None,
) -> int:
    with conn:
        cursor = conn.execute(
            INSERT_ENTRY,
            (document_id, kind, medium, period_start, period_end, value, unit, note, _now(), created_by),
        )
    return cursor.lastrowid


def insert_entries(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    document_id: int | None = None,
    created_by: str | None = None,
) -> None:
    """Insert several entries in one transaction: all of them or none."""
    now = _now()
    with conn:
        conn.executemany(
            INSERT_ENTRY,
            [
                (
                    document_id,
                    row["kind"],
                    row["medium"],
                    row["period_start"],
                    row["period_end"],
                    row["value"],
                    row["unit"],
                    row.get("note"),
                    now,
                    created_by,
                )
                for row in rows
            ],
        )


def list_entries(conn: sqlite3.Connection, document_id: int | None = None) -> list[sqlite3.Row]:
    if document_id is None:
        return conn.execute("SELECT * FROM entries ORDER BY period_end DESC, id DESC").fetchall()
    return conn.execute(
        "SELECT * FROM entries WHERE document_id = ? ORDER BY period_end DESC, id DESC", (document_id,)
    ).fetchall()


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
