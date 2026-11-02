"""HTML pages and HTMX partials."""

import mimetypes
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from riffdb import db, importer, library, search
from riffdb.config import Settings

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

KEY_PRESETS = [
    f"{n}{q}"
    for n in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
    for q in ("", "m")
]
TUNING_PRESETS = [
    "Standard", "Eb Standard", "D Standard", "C# Standard", "C Standard",
    "Drop D", "Drop C#", "Drop C", "Drop B", "Drop A",
    "DADGAD", "Open D", "Open G", "Open E", "Open C",
]


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%b %-d, %Y %-I:%M %p") if dt.time() != dt.min.time() else dt.strftime("%b %-d, %Y")


templates.env.filters["duration"] = fmt_duration
templates.env.filters["date"] = fmt_date


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Cfg = Annotated[Settings, Depends(get_settings)]


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def query_from_request(request: Request) -> search.Query:
    p = request.query_params
    return search.Query(
        q=p.get("q", "").strip(),
        key=p.get("key", "").strip(),
        tuning=p.get("tuning", "").strip(),
        bpm_min=_int_or_none(p.get("bpm_min")),
        bpm_max=_int_or_none(p.get("bpm_max")),
        tags=p.getlist("tag") + p.getlist("tags"),  # repeated or comma-separated
        untagged=p.get("untagged") in ("1", "on", "true"),
        date_from=p.get("date_from", ""),
        date_to=p.get("date_to", ""),
        sort=p.get("sort", "newest"),
    )


def _with_custom(presets: list[str], used: list[str]) -> list[str]:
    """Presets first, then any values you've typed that aren't presets."""
    known = {p.lower() for p in presets}
    return presets + [u for u in used if u.lower() not in known]


def _common(conn: sqlite3.Connection) -> dict:
    return {
        "untagged_count": db.count_untagged(conn),
        "all_tags": db.all_tags(conn),
        "key_options": _with_custom(KEY_PRESETS, db.distinct_values(conn, "key")),
        "tuning_options": _with_custom(TUNING_PRESETS, db.distinct_values(conn, "tuning")),
    }


@router.get("/", response_class=HTMLResponse)
def browse(request: Request, conn: Conn):
    query = query_from_request(request)
    ctx = _common(conn) | {
        "query": query,
        "results": search.search(conn, query),
        "used_keys": db.distinct_values(conn, "key"),
        "used_tunings": db.distinct_values(conn, "tuning"),
    }
    return templates.TemplateResponse(request, "browse.html", ctx)


@router.get("/results", response_class=HTMLResponse)
def results(request: Request, conn: Conn):
    query = query_from_request(request)
    response = templates.TemplateResponse(
        request, "_results.html", {"query": query, "results": search.search(conn, query)}
    )
    # Keep the address bar in sync so filtered views can be bookmarked/shared.
    qs = urlencode([(k, v) for k, v in request.query_params.multi_items() if v])
    response.headers["HX-Replace-Url"] = f"/?{qs}" if qs else "/"
    return response


@router.get("/audio/{rec_id}")
def audio(rec_id: str, conn: Conn, settings: Cfg):
    rec = db.get_recording(conn, rec_id)
    if rec is None:
        raise HTTPException(404)
    path = settings.audio_dir / rec["path"]
    media_type = mimetypes.guess_type(rec["filename_orig"])[0] or "application/octet-stream"
    if media_type == "audio/mp4a-latm" or path.suffix == ".m4a":
        media_type = "audio/mp4"
    return FileResponse(path, media_type=media_type)


@router.get("/r/{rec_id}", response_class=HTMLResponse)
def edit_page(request: Request, rec_id: str, conn: Conn, queue: bool = False):
    rec = db.get_recording(conn, rec_id)
    if rec is None:
        raise HTTPException(404)
    ctx = _common(conn) | {"rec": rec, "tags": db.tags_for(conn, rec_id), "queue": queue}
    return templates.TemplateResponse(request, "edit.html", ctx)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.post("/r/{rec_id}")
def save(
    rec_id: str,
    conn: Conn,
    settings: Cfg,
    title: Annotated[str, Form()] = "",
    key: Annotated[str, Form()] = "",
    tuning: Annotated[str, Form()] = "",
    bpm: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "save",
):
    rec = db.get_recording(conn, rec_id)
    if rec is None:
        raise HTTPException(404)
    db.update_recording(
        conn,
        rec_id,
        {
            "title": _clean(title),
            "key": _clean(key),
            "tuning": _clean(tuning),
            "bpm": _int_or_none(bpm.strip()),
            "notes": _clean(notes),
        },
        db.normalize_tags(tags),
    )
    library.sync_name(conn, settings, rec_id)
    library.write_export(conn, settings)
    if action == "next":
        return _redirect_next_untagged(conn, rec)
    return RedirectResponse(f"/r/{rec_id}?saved=1", status_code=303)


def _redirect_next_untagged(conn: sqlite3.Connection, after: sqlite3.Row | None) -> RedirectResponse:
    nxt = db.next_untagged(conn, after)
    if nxt is None:
        return RedirectResponse("/queue", status_code=303)
    return RedirectResponse(f"/r/{nxt['id']}?queue=1", status_code=303)


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, conn: Conn, after: str | None = None):
    after_rec = db.get_recording(conn, after) if after else None
    nxt = db.next_untagged(conn, after_rec)
    if nxt is not None:
        return RedirectResponse(f"/r/{nxt['id']}?queue=1", status_code=303)
    return templates.TemplateResponse(request, "queue_done.html", _common(conn))


@router.post("/r/{rec_id}/delete")
def delete(rec_id: str, conn: Conn, settings: Cfg):
    rec = db.delete_recording(conn, rec_id)
    if rec is None:
        raise HTTPException(404)
    library.remove_file(settings, rec["path"])
    library.write_export(conn, settings)
    return RedirectResponse("/", status_code=303)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, conn: Conn, settings: Cfg):
    ctx = _common(conn) | {"inbox_dir": settings.inbox_dir, "poll_s": settings.inbox_poll_s}
    return templates.TemplateResponse(request, "import.html", ctx)


@router.post("/import/upload", response_class=HTMLResponse)
def upload(request: Request, conn: Conn, settings: Cfg, files: list[UploadFile]):
    results = []
    for f in files:
        name = Path(f.filename or "upload").name
        tmp = settings.tmp_dir / f"{uuid.uuid4()}{Path(name).suffix}"
        try:
            with tmp.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            results.append(importer.import_file(conn, settings, tmp, original_name=name, move=True))
        finally:
            tmp.unlink(missing_ok=True)
    if any(r.status == "imported" for r in results):
        library.write_export(conn, settings)
    return templates.TemplateResponse(request, "_import_results.html", {"results": results})


@router.post("/import/scan", response_class=HTMLResponse)
def scan(request: Request, conn: Conn, settings: Cfg):
    results = importer.scan_inbox(conn, settings)
    return templates.TemplateResponse(
        request, "_import_results.html", {"results": results, "scanned": True}
    )
