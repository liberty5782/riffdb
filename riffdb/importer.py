"""Import audio files into the library: hash, dedupe, copy, probe metadata."""

import hashlib
import logging
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mutagen
from mutagen.mp4 import Atoms

from riffdb import db, library
from riffdb.config import Settings

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac", ".webm", ".3gp", ".mp4"}
INBOX_DONE = "_imported"
INBOX_SETTLE_S = 10  # skip inbox files modified this recently (still being copied)

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_PATTERNS = [
    # 20261102_115200, 20261102-115200, 20261102 115200
    (re.compile(r"(\d{4})(\d{2})(\d{2})[ _-](\d{2})(\d{2})(\d{2})"), "ymdhms"),
    # 2026-11-02 11-52-00 / 2026-11-02T11:52:00 / 2026-11-02 11.52
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T_](\d{2})[-:.](\d{2})(?:[-:.](\d{2}))?"), "ymdhms"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "ymd"),
    # Nov 2, 2026 11:52 AM / Nov 2 2026 11-52 PM
    (re.compile(rf"({MONTHS})[a-z]*\.? (\d{{1,2}}),? (\d{{4}})(?:,? (\d{{1,2}})[:.-](\d{{2}})\s*([ap]m)?)?", re.I), "mdy"),
]
# Google Recorder default names: "Aug 20 at 9-30 PM" (no year)
_RECORDER_NAME = re.compile(rf"({MONTHS})[a-z]* (\d{{1,2}}) at (\d{{1,2}})-(\d{{2}}) ?([ap]m)", re.I)
MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)


@dataclass
class ImportResult:
    status: str  # "imported" | "duplicate" | "unsupported" | "error"
    filename: str
    recording_id: str | None = None
    message: str = ""


def parse_date_from_name(name: str) -> str | None:
    """Best-effort recording datetime from a filename, as ISO 'YYYY-MM-DDTHH:MM:SS'."""
    stem = Path(name).stem
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(stem)
        if not m:
            continue
        try:
            if kind == "ymdhms":
                y, mo, d, h, mi, s = (int(g or 0) for g in m.groups())
                dt = datetime(y, mo, d, h, mi, s)
            elif kind == "ymd":
                y, mo, d = (int(g) for g in m.groups())
                dt = datetime(y, mo, d)
            else:
                mon, d, y, h, mi, ampm = m.groups()
                dt = datetime(int(y), _month(mon), int(d), _to_hour(h, ampm), int(mi or 0))
        except ValueError:
            continue
        if 1990 <= dt.year <= 2100:
            return dt.isoformat(timespec="seconds")
    return None


def _to_hour(h: str | None, ampm: str | None) -> int:
    hour = int(h or 0)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    return hour


def _month(name: str) -> int:
    return MONTHS.split("|").index(name[:3].lower()) + 1


def parse_yearless_name(name: str, reference: datetime) -> str | None:
    """Google Recorder style "Aug 20 at 9-30 PM": take the most recent such
    date that isn't after `reference` (when the file was imported/modified)."""
    m = _RECORDER_NAME.search(Path(name).stem)
    if not m:
        return None
    mon, d, h, mi, ampm = m.groups()
    for year in (reference.year, reference.year - 1):
        try:
            dt = datetime(year, _month(mon), int(d), _to_hour(h, ampm), int(mi))
        except ValueError:  # e.g. Feb 29 in a non-leap year
            continue
        if dt <= reference:
            return dt.isoformat(timespec="seconds")
    return None


def mp4_creation_time(path: Path) -> str | None:
    """Recording time from the MP4/M4A movie header (mvhd), in local time.

    Android (incl. Google Recorder) writes this when recording; mutagen doesn't
    surface it as a tag, so read the atom directly.
    """
    try:
        with path.open("rb") as f:
            atom = Atoms(f).path(b"moov", b"mvhd")[-1]
            f.seek(atom.offset + 8)
            data = f.read(atom.length - 8)
    except Exception:  # not an MP4 container, or malformed
        return None
    version = data[0]
    seconds = int.from_bytes(data[4:12] if version == 1 else data[4:8], "big")
    if not seconds:
        return None
    dt = (MP4_EPOCH + timedelta(seconds=seconds)).astimezone().replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") if 1990 <= dt.year <= 2100 else None


def _date_from_tags(audio: mutagen.FileType | None) -> str | None:
    if audio is None or audio.tags is None:
        return None
    for key in ("\xa9day", "TDRC", "date", "DATE", "creation_time"):
        try:
            value = audio.tags.get(key)
        except (KeyError, ValueError, TypeError):
            continue
        if not value:
            continue
        text = str(value[0] if isinstance(value, list) else value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec="seconds")
    return None


def probe(path: Path) -> tuple[float | None, str | None]:
    """(duration seconds, embedded recording date) — either may be None."""
    try:
        audio = mutagen.File(path)
    except Exception:  # mutagen raises many types on odd files
        log.warning("could not probe %s", path, exc_info=True)
        return None, None
    duration = getattr(getattr(audio, "info", None), "length", None)
    return (round(duration, 2) if duration else None), _date_from_tags(audio) or mp4_creation_time(path)


def recorded_at_for(path: Path, name: str, embedded: str | None) -> str:
    """Best guess at when a recording was made, most trustworthy source first:
    full date in the filename, date embedded in the file, yearless Recorder-style
    filename, file modification time."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (
        parse_date_from_name(name)
        or embedded
        or parse_yearless_name(name, reference=mtime)
        or mtime.isoformat(timespec="seconds")
    )


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_file(
    conn: sqlite3.Connection,
    settings: Settings,
    src: Path,
    original_name: str | None = None,
    move: bool = False,
) -> ImportResult:
    name = original_name or src.name
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        return ImportResult("unsupported", name, message=f"unsupported file type '{ext or '?'}'")

    digest = sha256_of(src)
    if existing := db.get_by_sha(conn, digest):
        if move:
            src.unlink()
        return ImportResult("duplicate", name, existing["id"], "already in library")

    rec_id = str(uuid.uuid4())
    dest = settings.audio_dir / f"{rec_id}{ext}"
    if move:
        shutil.move(src, dest)
    else:
        shutil.copy2(src, dest)

    duration, embedded_date = probe(dest)
    recorded_at = recorded_at_for(dest, name, embedded_date)
    try:
        db.insert_recording(
            conn,
            id=rec_id,
            filename_orig=name,
            path=dest.name,
            sha256=digest,
            duration_s=duration,
            recorded_at=recorded_at,
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception as e:
        if move:
            shutil.move(dest, src)  # put it back so the inbox can retry
        else:
            dest.unlink(missing_ok=True)
        log.exception("failed to import %s", name)
        return ImportResult("error", name, message=str(e))
    library.sync_name(conn, settings, rec_id)  # move to its readable name
    return ImportResult("imported", name, rec_id)


def scan_inbox(conn: sqlite3.Connection, settings: Settings) -> list[ImportResult]:
    """Import every settled audio file under inbox/ (recursively).

    Imported files are moved into the library; duplicates are removed. Unsupported
    files are moved to inbox/_imported/ so they are not re-reported every scan.
    """
    results = []
    done_dir = settings.inbox_dir / INBOX_DONE
    now = time.time()
    for path in sorted(settings.inbox_dir.rglob("*")):
        if not path.is_file() or done_dir in path.parents or path.name.startswith("."):
            continue
        if now - path.stat().st_mtime < INBOX_SETTLE_S:
            continue
        result = import_file(conn, settings, path, move=True)
        if result.status == "unsupported":
            done_dir.mkdir(exist_ok=True)
            shutil.move(path, done_dir / path.name)
        results.append(result)
        log.info("inbox %s: %s %s", result.status, result.filename, result.message)
    if any(r.status == "imported" for r in results):
        library.write_export(conn, settings)
    return results
