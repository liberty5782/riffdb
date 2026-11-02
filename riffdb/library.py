"""Keep the on-disk library human-readable: descriptive audio filenames and a
JSON export of all metadata (so backups make sense without the app)."""

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from riffdb import db
from riffdb.config import Settings

EXPORT_NAME = "riffdb-export.json"
_UNSAFE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')
MAX_LABEL = 80


def _label(rec: sqlite3.Row) -> str:
    raw = rec["title"] or Path(rec["filename_orig"]).stem
    label = " ".join(_UNSAFE.sub(" ", raw).split())[:MAX_LABEL].strip(" .")
    return label or "untitled"


def storage_path(rec: sqlite3.Row) -> str:
    """Relative path under audio/, e.g. '2026/2026-08-20 2131 - Sludge verse [9de8ad7c].m4a'."""
    try:
        when = datetime.fromisoformat(rec["recorded_at"])
        year, stamp = f"{when:%Y}", f"{when:%Y-%m-%d %H%M}"
    except (TypeError, ValueError):
        year, stamp = "undated", "undated"
    ext = Path(rec["path"]).suffix
    return f"{year}/{stamp} - {_label(rec)} [{rec['id'][:8]}]{ext}"


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    d = start
    while d != stop and d.is_relative_to(stop):
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def sync_name(conn: sqlite3.Connection, settings: Settings, recording_id: str) -> None:
    """Rename the recording's file to match its current title/date, if needed."""
    rec = db.get_recording(conn, recording_id)
    if rec is None:
        return
    want = storage_path(rec)
    if want == rec["path"]:
        return
    src, dest = settings.audio_dir / rec["path"], settings.audio_dir / want
    if not src.exists():
        return
    if dest.exists():  # vanishingly unlikely 8-char id clash: use the full id
        want = want.replace(f"[{rec['id'][:8]}]", f"[{rec['id']}]")
        dest = settings.audio_dir / want
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    try:
        with conn:
            conn.execute("UPDATE recording SET path = ? WHERE id = ?", (want, recording_id))
    except Exception:
        dest.rename(src)
        raise
    _prune_empty_dirs(src.parent, settings.audio_dir)


def sync_all_names(conn: sqlite3.Connection, settings: Settings) -> None:
    for (rid,) in conn.execute("SELECT id FROM recording").fetchall():
        sync_name(conn, settings, rid)


def remove_file(settings: Settings, rel_path: str) -> None:
    path = settings.audio_dir / rel_path
    path.unlink(missing_ok=True)
    _prune_empty_dirs(path.parent, settings.audio_dir)


def write_export(conn: sqlite3.Connection, settings: Settings) -> None:
    """Write data/riffdb-export.json atomically: every recording's metadata,
    keyed to its audio file path."""
    rows = conn.execute("SELECT * FROM recording ORDER BY recorded_at, imported_at").fetchall()
    tags = db.tags_by_recording(conn, [r["id"] for r in rows])
    recordings = [
        {
            "file": f"audio/{r['path']}",
            "title": r["title"],
            "recorded_at": r["recorded_at"],
            "key": r["key"],
            "tuning": r["tuning"],
            "bpm": r["bpm"],
            "tags": tags[r["id"]],
            "notes": r["notes"],
            "duration_s": r["duration_s"],
            "original_filename": r["filename_orig"],
            "imported_at": r["imported_at"],
            "sha256": r["sha256"],
            "id": r["id"],
        }
        for r in rows
    ]
    doc = {"exported_at": datetime.now().isoformat(timespec="seconds"), "count": len(recordings),
           "recordings": recordings}
    dest = settings.data_dir / EXPORT_NAME
    tmp = dest.with_name(f".{EXPORT_NAME}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
