"""SQLite access: connection setup, migrations, and recording/tag queries."""

import sqlite3
from pathlib import Path

RECORDING_FIELDS = ("title", "key", "tuning", "bpm", "notes")


def _m1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE recording (
            id            TEXT PRIMARY KEY,
            filename_orig TEXT NOT NULL,
            path          TEXT NOT NULL,
            sha256        TEXT NOT NULL UNIQUE,
            duration_s    REAL,
            recorded_at   TEXT,
            imported_at   TEXT NOT NULL,
            title         TEXT,
            key           TEXT,
            tuning        TEXT,
            bpm           INTEGER,
            notes         TEXT
        );
        CREATE INDEX recording_recorded_at ON recording(recorded_at);

        CREATE TABLE tag (
            id   INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );

        CREATE TABLE recording_tag (
            recording_id TEXT NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
            tag_id       INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
            PRIMARY KEY (recording_id, tag_id)
        );
        CREATE INDEX recording_tag_tag ON recording_tag(tag_id);

        CREATE VIRTUAL TABLE recording_fts USING fts5(
            recording_id UNINDEXED, title, notes, filename, tags,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        """
    )


# Append new migrations; never edit or reorder existing ones.
MIGRATIONS = [_m1]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {i}")


# --- tags -------------------------------------------------------------------

def normalize_tags(raw: list[str] | str) -> list[str]:
    """Split/trim/dedupe tag input, preserving first-seen order and casing."""
    items = raw.split(",") if isinstance(raw, str) else [p for r in raw for p in r.split(",")]
    seen: dict[str, str] = {}
    for item in items:
        name = " ".join(item.split()).lower()
        if name and name not in seen:
            seen[name] = name
    return list(seen.values())


def tags_for(conn: sqlite3.Connection, recording_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT t.name FROM tag t JOIN recording_tag rt ON rt.tag_id = t.id
           WHERE rt.recording_id = ? ORDER BY t.name""",
        (recording_id,),
    )
    return [r["name"] for r in rows]


def tags_by_recording(conn: sqlite3.Connection, ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT rt.recording_id, t.name FROM recording_tag rt JOIN tag t ON t.id = rt.tag_id
            WHERE rt.recording_id IN ({marks}) ORDER BY t.name""",
        ids,
    )
    for r in rows:
        out[r["recording_id"]].append(r["name"])
    return out


def all_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Tags in use, with counts, most used first."""
    return conn.execute(
        """SELECT t.name, COUNT(*) AS n FROM tag t JOIN recording_tag rt ON rt.tag_id = t.id
           GROUP BY t.id ORDER BY n DESC, t.name"""
    ).fetchall()


def distinct_values(conn: sqlite3.Connection, column: str) -> list[str]:
    assert column in ("key", "tuning")
    rows = conn.execute(
        f"SELECT DISTINCT {column} AS v FROM recording WHERE {column} IS NOT NULL ORDER BY v"
    )
    return [r["v"] for r in rows]


def set_tags(conn: sqlite3.Connection, recording_id: str, tags: list[str]) -> None:
    conn.execute("DELETE FROM recording_tag WHERE recording_id = ?", (recording_id,))
    for name in normalize_tags(tags):
        conn.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (name,))
        conn.execute(
            """INSERT INTO recording_tag(recording_id, tag_id)
               SELECT ?, id FROM tag WHERE name = ?""",
            (recording_id, name),
        )
    conn.execute("DELETE FROM tag WHERE id NOT IN (SELECT tag_id FROM recording_tag)")


# --- recordings ---------------------------------------------------------------

def refresh_fts(conn: sqlite3.Connection, recording_id: str) -> None:
    conn.execute("DELETE FROM recording_fts WHERE recording_id = ?", (recording_id,))
    rec = get_recording(conn, recording_id)
    if rec is None:
        return
    conn.execute(
        "INSERT INTO recording_fts(recording_id, title, notes, filename, tags) VALUES (?,?,?,?,?)",
        (
            recording_id,
            rec["title"] or "",
            rec["notes"] or "",
            rec["filename_orig"],
            " ".join(tags_for(conn, recording_id)),
        ),
    )


def get_recording(conn: sqlite3.Connection, recording_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM recording WHERE id = ?", (recording_id,)).fetchone()


def get_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM recording WHERE sha256 = ?", (sha256,)).fetchone()


def insert_recording(conn: sqlite3.Connection, **values: object) -> None:
    cols = ",".join(values)
    marks = ",".join("?" * len(values))
    with conn:
        conn.execute(f"INSERT INTO recording({cols}) VALUES ({marks})", tuple(values.values()))
        refresh_fts(conn, str(values["id"]))


def update_recording(
    conn: sqlite3.Connection, recording_id: str, fields: dict[str, object], tags: list[str]
) -> None:
    fields = {k: v for k, v in fields.items() if k in RECORDING_FIELDS}
    with conn:
        if fields:
            assignments = ",".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE recording SET {assignments} WHERE id = ?",
                (*fields.values(), recording_id),
            )
        set_tags(conn, recording_id, tags)
        refresh_fts(conn, recording_id)


def delete_recording(conn: sqlite3.Connection, recording_id: str) -> sqlite3.Row | None:
    rec = get_recording(conn, recording_id)
    if rec is None:
        return None
    with conn:
        conn.execute("DELETE FROM recording_fts WHERE recording_id = ?", (recording_id,))
        conn.execute("DELETE FROM recording WHERE id = ?", (recording_id,))
        conn.execute("DELETE FROM tag WHERE id NOT IN (SELECT tag_id FROM recording_tag)")
    return rec


# "Untagged" = nothing described yet: every descriptor empty and no tags.
UNTAGGED_SQL = (
    "(COALESCE(r.title, r.key, r.tuning, r.bpm, r.notes) IS NULL AND NOT EXISTS "
    "(SELECT 1 FROM recording_tag rt WHERE rt.recording_id = r.id))"
)


def next_untagged(conn: sqlite3.Connection, after: sqlite3.Row | None = None) -> sqlite3.Row | None:
    """Untagged recordings in chronological order: the first one after `after`,
    wrapping around to the start (excluding `after` itself)."""
    order = "ORDER BY r.recorded_at, r.imported_at, r.id LIMIT 1"
    if after is None:
        return conn.execute(f"SELECT r.* FROM recording r WHERE {UNTAGGED_SQL} {order}").fetchone()
    key = (after["recorded_at"], after["imported_at"], after["id"])
    return conn.execute(
        f"""SELECT r.* FROM recording r WHERE {UNTAGGED_SQL}
            AND (r.recorded_at, r.imported_at, r.id) > (?, ?, ?) {order}""",
        key,
    ).fetchone() or conn.execute(
        f"SELECT r.* FROM recording r WHERE {UNTAGGED_SQL} AND r.id != ? {order}",
        (after["id"],),
    ).fetchone()


def count_untagged(conn: sqlite3.Connection) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM recording r WHERE {UNTAGGED_SQL}").fetchone()[0]
