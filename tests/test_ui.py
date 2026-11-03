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

    # Queue opens it in the editor panel
    q = client.get("/queue")
    assert q.status_code == 200 and f'action="/r/{rec_id}"' in q.text and "1 untagged" in q.text

    # Save & next with no more untagged -> queue done
    r = client.post(f"/r/{rec_id}", data={"title": "Doom riff", "tuning": "Drop D", "bpm": "", "tags": "Dark, slow ,dark", "action": "next", "queue": "1"})
    assert r.status_code == 200 and "All caught up" in r.text and "0 untagged" in r.text

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


def test_queue_split_view(client, tmp_path):
    ids = []
    for i, name in enumerate(["20250101_100000.wav", "20250102_100000.wav", "20250103_100000.wav"]):
        r = upload(client, tmp_path, name, seed=i)
        ids.append(re.search(r'href="/r/([0-9a-f-]{36})"', r.text).group(1))
    a, b, c = ids

    page = client.get("/queue")
    assert "3 untagged" in page.text and f'action="/r/{a}"' in page.text  # oldest first
    assert page.headers["HX-Push-Url"] == f"/queue?id={a}"

    # Picking one from the list opens it
    assert f'action="/r/{c}"' in client.get(f"/queue?id={c}").text

    # Plain save keeps the same riff open, now gone from the untagged list
    r = client.post(f"/r/{c}", data={"tags": "x", "queue": "1"})
    assert f'action="/r/{c}"' in r.text and "Saved" in r.text and "2 untagged" in r.text
    assert f'href="/queue?id={c}"' not in r.text

    # Skip moves to the next untagged after a
    assert f'action="/r/{b}"' in client.get(f"/queue?after={a}").text

    # Deleting from the queue lands on the next untagged
    r = client.post(f"/r/{b}/delete", data={"queue": "1"})
    assert f'action="/r/{a}"' in r.text and "1 untagged" in r.text
