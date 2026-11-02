import os
import shutil
import subprocess
from datetime import datetime, timezone
import time

import pytest

from riffdb import db, importer
from tests.conftest import make_wav


@pytest.mark.parametrize(
    "name, expected",
    [
        ("20261102_115200.m4a", "2026-11-02T11:52:00"),
        ("Recording 2025-03-14 21-05-09.m4a", "2025-03-14T21:05:09"),
        ("riff 2025-03-14.wav", "2025-03-14T00:00:00"),
        ("Nov 2, 2026 11:52 PM.m4a", "2026-11-02T23:52:00"),
        ("Mar 9 2024.m4a", "2024-03-09T00:00:00"),
        ("Dec 1, 2023 12:05 AM.m4a", "2023-12-01T00:05:00"),
        ("Recording 5.m4a", None),
        ("99999999_999999.m4a", None),
    ],
)
def test_parse_date_from_name(name, expected):
    assert importer.parse_date_from_name(name) == expected


def test_import_copies_probes_and_dedupes(conn, settings, tmp_path):
    src = make_wav(tmp_path / "20250101_120000.wav", seconds=2)
    first = importer.import_file(conn, settings, src)
    assert first.status == "imported"
    rec = db.get_recording(conn, first.recording_id)
    assert rec["recorded_at"] == "2025-01-01T12:00:00"
    assert rec["duration_s"] == pytest.approx(2, abs=0.05)
    assert (settings.audio_dir / rec["path"]).exists()
    assert src.exists()  # copy, not move

    again = importer.import_file(conn, settings, src)
    assert again.status == "duplicate"
    assert again.recording_id == first.recording_id
    assert conn.execute("SELECT COUNT(*) FROM recording").fetchone()[0] == 1


def test_import_rejects_unsupported(conn, settings, tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("hi")
    assert importer.import_file(conn, settings, src).status == "unsupported"


def test_scan_inbox(conn, settings):
    sub = settings.inbox_dir / "batch"
    sub.mkdir()
    old = time.time() - 60
    for i, name in enumerate(["a.wav", "b.wav"]):
        p = make_wav(sub / name, freq_seed=i)
        os.utime(p, (old, old))
    dup = make_wav(settings.inbox_dir / "a-copy.wav", freq_seed=0)
    os.utime(dup, (old, old))
    junk = settings.inbox_dir / "readme.txt"
    junk.write_text("x")
    os.utime(junk, (old, old))
    fresh = make_wav(settings.inbox_dir / "still-copying.wav", freq_seed=9)

    results = importer.scan_inbox(conn, settings)
    statuses = sorted(r.status for r in results)
    assert statuses == ["duplicate", "imported", "imported", "unsupported"]
    assert fresh.exists()  # too recent, left for the next scan
    assert not (sub / "a.wav").exists() and not dup.exists()
    assert (settings.inbox_dir / importer.INBOX_DONE / "readme.txt").exists()
    assert importer.scan_inbox(conn, settings) == []


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Aug 20 at 9-30 PM.m4a", "2026-08-20T21:30:00"),
        ("Oct 4 at 10-55 AM.m4a", "2026-10-04T10:55:00"),
        ("Dec 30 at 11-05 AM.m4a", "2025-12-30T11:05:00"),  # later than reference -> last year
        ("Recording 5.m4a", None),
    ],
)
def test_parse_yearless_recorder_name(name, expected):
    from datetime import datetime

    assert importer.parse_yearless_name(name, reference=datetime(2026, 11, 2, 12)) == expected


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_mp4_header_date_beats_upload_time(conn, settings, tmp_path):
    src = tmp_path / "Aug 20 at 9-30 PM.m4a"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac",
         "-metadata", "creation_time=2026-08-21T02:31:01Z", str(src)],
        check=True,
    )
    expected = datetime(2026, 8, 21, 2, 31, 1, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert importer.mp4_creation_time(src) == expected.isoformat()
    rec = db.get_recording(conn, importer.import_file(conn, settings, src).recording_id)
    assert rec["recorded_at"] == expected.isoformat()
