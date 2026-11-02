import pytest

from riffdb import db
from riffdb.search import Query, fts_expression, search


def add(conn, rid, recorded_at, tags=(), **fields):
    db.insert_recording(
        conn, id=rid, filename_orig=f"{rid}.m4a", path=f"{rid}.m4a", sha256=rid,
        recorded_at=recorded_at, imported_at="2026-01-01T00:00:00",
    )
    db.update_recording(conn, rid, fields, list(tags))


@pytest.fixture
def lib(conn):
    add(conn, "a", "2025-01-05T10:00:00", ["dark", "palm-muted"], title="Sludge verse", key="Em", tuning="Drop D", bpm=90)
    add(conn, "b", "2025-02-10T10:00:00", ["dark", "clean"], title="Arpeggio thing", key="Am", tuning="Standard", bpm=120,
        notes="could be a bridge for sludge")
    add(conn, "c", "2025-03-01T10:00:00", ["chuggy"], tuning="Drop D", bpm=160)
    add(conn, "d", "2025-03-02T10:00:00")  # untagged
    return conn


def ids(rows):
    return [r["id"] for r in rows]


def test_default_sort_newest(lib):
    assert ids(search(lib, Query())) == ["d", "c", "b", "a"]


def test_text_search_prefix_and_notes(lib):
    assert sorted(ids(search(lib, Query(q="slud")))) == ["a", "b"]
    assert ids(search(lib, Query(q="sludge verse"))) == ["a"]
    assert ids(search(lib, Query(q="chug"))) == ["c"]  # tag text is searchable


def test_fts_expression_is_safe():
    assert fts_expression('drop "d" AND (OR') == '"drop"* "d"* "AND"* "OR"*'
    assert fts_expression("  ") == ""


def test_filters(lib):
    assert ids(search(lib, Query(tuning="drop d", sort="oldest"))) == ["a", "c"]
    assert ids(search(lib, Query(key="em"))) == ["a"]
    assert ids(search(lib, Query(bpm_min=100, bpm_max=150))) == ["b"]
    assert sorted(ids(search(lib, Query(tags=["dark"])))) == ["a", "b"]
    assert ids(search(lib, Query(tags=["dark", "clean"]))) == ["b"]
    assert ids(search(lib, Query(tags=["Dark, Palm-Muted"]))) == ["a"]
    assert ids(search(lib, Query(untagged=True))) == ["d"]
    assert ids(search(lib, Query(date_from="2025-02-01", date_to="2025-03-01", sort="oldest"))) == ["b", "c"]


def test_tags_attached_and_sort_title(lib):
    rows = search(lib, Query(sort="title"))
    assert ids(rows)[:2] == ["b", "a"]  # titled first, alphabetical
    assert rows[1]["tags"] == ["dark", "palm-muted"]


def test_untagged_queue_order(lib):
    add(lib, "e", "2025-01-01T00:00:00")
    first = db.next_untagged(lib)
    assert first["id"] == "e"
    assert db.next_untagged(lib, first)["id"] == "d"
    assert db.next_untagged(lib, db.get_recording(lib, "d"))["id"] == "e"  # wraps around
    db.update_recording(lib, "e", {"key": "E"}, [])
    assert db.count_untagged(lib) == 1


def test_unused_tags_are_pruned(lib):
    db.update_recording(lib, "c", {}, [])
    assert "chuggy" not in [t["name"] for t in db.all_tags(lib)]
    assert ids(search(lib, Query(q="chug"))) == []
