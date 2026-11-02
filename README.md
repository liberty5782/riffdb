# riffdb

Self-hosted, searchable library of guitar riff recordings. Import your recordings (e.g. Google Recorder exports), describe them (title, key, tuning, BPM, tags, notes — all optional), search/filter, and play them straight from the result list.

Everything lives on the server: SQLite DB + audio files in `./data`. Any browser on your network (laptop, phone, desktop) streams from it.

## Run on the home server

```sh
mkdir -p data          # create first so it's owned by you, not root
docker compose up -d --build
```

Open `http://<server>:8000`. If your user isn't uid/gid 1000, set `RIFFDB_UID`/`RIFFDB_GID` (e.g. in a `.env` file next to `docker-compose.yml`).

For access away from home, put the server on Tailscale and use its Tailscale name/IP.

## Getting recordings in

- **Upload page** (`/import`): pick files or a whole folder, from a laptop or a phone browser.
- **Inbox folder**: copy files into `data/inbox/` (scp, SMB share, Syncthing…). They're imported automatically every 30s (`RIFFDB_INBOX_POLL_S`), then removed from the inbox. Files already in the library (same content) are skipped.

Recording dates come from the filename if it contains a date (`20261102_115200`, `2026-11-02 11-52`, `Nov 2, 2026 11:52 AM`), else from the file's embedded metadata, else its modification time.

Then use **Tag queue** to work through untagged riffs: listen → describe → *Save & next*.

## Data layout

```
data/
├── riffdb.sqlite        # metadata, tags, search index (source of truth)
├── riffdb-export.json   # same metadata as plain JSON, rewritten on every change
├── audio/
│   └── 2026/
│       └── 2026-08-20 2131 - Mid-tempo bridge riff [9de8ad7c].m4a
└── inbox/               # drop zone (unsupported files go to inbox/_imported/)
```

Audio files are named `<recorded date/time> - <title, or original filename if untitled> [<id>]` and renamed automatically when you change a title. Contents are never modified. Duplicates are detected by content hash, regardless of filename.

### Backups (borgmatic)

Back up `data/` for the audio and the JSON export, and let borgmatic's SQLite hook
take a consistent dump of the DB rather than copying the live file mid-write.
The hook runs the `sqlite3` CLI **on the host running borgmatic** (`apt install sqlite3`);
the riffdb container doesn't need to be stopped.

borgmatic **1.7.9 – 1.7.x** (sectioned config):

```yaml
location:
  source_directories:
    - /path/to/riffdb/data
  exclude_patterns:
    - /path/to/riffdb/data/riffdb.sqlite*   # covered by the dump below
    - /path/to/riffdb/data/tmp

hooks:
  sqlite_databases:
    - name: riffdb
      path: /path/to/riffdb/data/riffdb.sqlite
```

borgmatic **1.8+** (flat config): the same keys without the `location:`/`hooks:` wrappers.

borgmatic **< 1.7.9** (no SQLite hook): snapshot it yourself before each backup; the snapshot (`riffdb-backup.sqlite`) is then backed up as a normal file under `data/`:

```yaml
hooks:
  before_backup:
    - sqlite3 /path/to/riffdb/data/riffdb.sqlite ".backup /path/to/riffdb/data/riffdb-backup.sqlite"
```

Restore: `borgmatic restore --database riffdb` (with the container stopped), then restore
`data/audio/` with `borgmatic extract`.

Borg deduplicates by content, so renamed audio files cost no extra space. `riffdb-export.json`
lets you make sense of a restored backup even without the app.

## Development

```sh
uv venv && uv pip install -e . pytest httpx
RIFFDB_DATA_DIR=./data .venv/bin/uvicorn riffdb.main:app --reload
.venv/bin/pytest
```

Stack: FastAPI + Jinja2 + HTMX, SQLite (FTS5) via stdlib `sqlite3`, `mutagen` for audio metadata. No JS build step.
