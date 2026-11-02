"""Re-derive recorded_at for every recording (e.g. after improving date detection).

    docker compose exec riffdb python -m riffdb.redate [--dry-run]
"""

import sys

from riffdb import db, importer, library
from riffdb.config import load_settings


def main(dry_run: bool = False) -> None:
    settings = load_settings()
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    changed = 0
    for rec in conn.execute("SELECT * FROM recording").fetchall():
        path = settings.audio_dir / rec["path"]
        if not path.exists():
            print(f"missing file, skipped: {rec['filename_orig']}")
            continue
        _, embedded = importer.probe(path)
        new = importer.recorded_at_for(path, rec["filename_orig"], embedded)
        if new != rec["recorded_at"]:
            changed += 1
            print(f"{rec['filename_orig']}: {rec['recorded_at']} -> {new}")
            if not dry_run:
                with conn:
                    conn.execute("UPDATE recording SET recorded_at = ? WHERE id = ?", (new, rec["id"]))
    if changed and not dry_run:
        library.sync_all_names(conn, settings)  # filenames include the date
        library.write_export(conn, settings)
    print(f"{changed} recording(s) {'would change' if dry_run else 'updated'}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
