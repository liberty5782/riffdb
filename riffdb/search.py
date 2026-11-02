"""Translate search/filter parameters into a SQL query over recordings."""

import re
import sqlite3
from dataclasses import dataclass, field

from riffdb import db

SORTS = {
    "newest": "r.recorded_at DESC, r.imported_at DESC",
    "oldest": "r.recorded_at ASC, r.imported_at ASC",
    "title": "r.title IS NULL, r.title COLLATE NOCASE, r.recorded_at DESC",
    "relevance": "fts.rank, r.recorded_at DESC",
}


@dataclass
class Query:
    q: str = ""
    key: str = ""
    tuning: str = ""
    bpm_min: int | None = None
    bpm_max: int | None = None
    tags: list[str] = field(default_factory=list)
    untagged: bool = False
    date_from: str = ""  # YYYY-MM-DD
    date_to: str = ""
    sort: str = "newest"
    limit: int = 200


def fts_expression(text: str) -> str:
    """User text -> safe FTS5 prefix query: every word must match (AND)."""
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


def search(conn: sqlite3.Connection, query: Query) -> list[dict]:
    where: list[str] = []
    params: list[object] = []
    joins = ""

    fts = fts_expression(query.q)
    if fts:
        joins = "JOIN recording_fts fts ON fts.recording_id = r.id"
        where.append("recording_fts MATCH ?")
        params.append(fts)
    if query.key:
        where.append("r.key = ? COLLATE NOCASE")
        params.append(query.key)
    if query.tuning:
        where.append("r.tuning = ? COLLATE NOCASE")
        params.append(query.tuning)
    if query.bpm_min is not None:
        where.append("r.bpm >= ?")
        params.append(query.bpm_min)
    if query.bpm_max is not None:
        where.append("r.bpm <= ?")
        params.append(query.bpm_max)
    for tag in db.normalize_tags(query.tags):
        where.append(
            """EXISTS (SELECT 1 FROM recording_tag rt JOIN tag t ON t.id = rt.tag_id
                       WHERE rt.recording_id = r.id AND t.name = ?)"""
        )
        params.append(tag)
    if query.untagged:
        where.append(db.UNTAGGED_SQL)
    if query.date_from:
        where.append("r.recorded_at >= ?")
        params.append(query.date_from)
    if query.date_to:
        where.append("r.recorded_at < date(?, '+1 day')")
        params.append(query.date_to)

    sort = query.sort if query.sort in SORTS else "newest"
    if sort == "relevance" and not fts:
        sort = "newest"

    sql = f"SELECT r.* FROM recording r {joins}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORTS[sort]} LIMIT ?"
    params.append(query.limit)

    rows = [dict(r) for r in conn.execute(sql, params)]
    tags = db.tags_by_recording(conn, [r["id"] for r in rows])
    for r in rows:
        r["tags"] = tags[r["id"]]
    return rows
