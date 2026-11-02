import re

from tests.conftest import make_wav


def upload(client, tmp_path, name, seed=0):
    path = make_wav(tmp_path / name, freq_seed=seed)
    with path.open("rb") as f:
        return client.post("/import/upload", files=[("files", (name, f, "audio/wav"))])


def test_upload_tag_search_play_delete(client, settings, tmp_path):
    r = upload(client, tmp_path, "20250601_090000.wav")
    assert r.status_code == 200 and "1 imported" in r.text
    assert "1 imported" not in upload(client, tmp_path, "20250601_090000.wav").text  # dup

    rec_id = re.search(r'href="/r/([0-9a-f-]{36})"', r.text).group(1)

    # Queue points at it
    q = client.get("/queue", follow_redirects=False)
    assert q.status_code == 303 and q.headers["location"] == f"/r/{rec_id}?queue=1"

    # Save & next with no more untagged -> queue done page
    r = client.post(f"/r/{rec_id}", data={"title": "Doom riff", "tuning": "Drop D", "bpm": "", "tags": "Dark, slow ,dark", "action": "next"})
    assert r.status_code == 200 and "All caught up" in r.text

    res = client.get("/results", params={"q": "doom", "tags": "dark"})
    assert "Doom riff" in res.text and res.headers["HX-Replace-Url"] == "/?q=doom&tags=dark"
    assert "Doom riff" not in client.get("/results", params={"tag": "fast"}).text

    page = client.get("/")
    assert page.status_code == 200 and "Doom riff" in page.text

    audio = client.get(f"/audio/{rec_id}", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.content) == 100

    edit = client.get(f"/r/{rec_id}")
    assert 'data-value="dark,slow"' in edit.text and 'value="Drop D"' in edit.text

    assert client.post(f"/r/{rec_id}/delete", follow_redirects=False).status_code == 303
    assert client.get(f"/audio/{rec_id}").status_code == 404
    assert list(settings.audio_dir.iterdir()) == []


def test_scan_endpoint_and_pages(client):
    assert "No new files" in client.post("/import/scan").text
    assert client.get("/import").status_code == 200
    assert client.get("/r/nope").status_code == 404


def test_readable_names_and_export(client, settings, tmp_path):
    import json

    r = upload(client, tmp_path, "20250601_090000.wav")
    rec_id = re.search(r'href="/r/([0-9a-f-]{36})"', r.text).group(1)
    short = rec_id[:8]
    assert (settings.audio_dir / f"2025/2025-06-01 0900 - 20250601_090000 [{short}].wav").exists()

    client.post(f"/r/{rec_id}", data={"title": 'Doom: riff / "v2"', "tags": "dark"})
    named = settings.audio_dir / f"2025/2025-06-01 0900 - Doom riff v2 [{short}].wav"
    assert named.exists() and len(list(settings.audio_dir.rglob("*.wav"))) == 1
    assert client.get(f"/audio/{rec_id}").status_code == 200

    export = json.loads((settings.data_dir / "riffdb-export.json").read_text())
    assert export["count"] == 1
    entry = export["recordings"][0]
    assert entry["file"] == f"audio/2025/{named.name}"
    assert entry["tags"] == ["dark"] and entry["original_filename"] == "20250601_090000.wav"

    client.post(f"/r/{rec_id}/delete")
    assert not (settings.audio_dir / "2025").exists()  # empty year folder pruned
    assert json.loads((settings.data_dir / "riffdb-export.json").read_text())["count"] == 0
