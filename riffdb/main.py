import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from riffdb import db, importer, library
from riffdb.config import Settings, load_settings
from riffdb.routes import ui

log = logging.getLogger("riffdb")
STATIC_DIR = Path(__file__).parent / "static"


async def watch_inbox(settings: Settings) -> None:
    def scan() -> None:
        conn = db.connect(settings.db_path)
        try:
            importer.scan_inbox(conn, settings)
        finally:
            conn.close()

    while True:
        try:
            await asyncio.to_thread(scan)
        except Exception:
            log.exception("inbox scan failed")
        await asyncio.sleep(settings.inbox_poll_s)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        conn = db.connect(settings.db_path)
        db.migrate(conn)
        library.sync_all_names(conn, settings)  # bring older files up to the naming scheme
        library.write_export(conn, settings)
        conn.close()
        task = None
        if settings.inbox_poll_s > 0:
            task = asyncio.create_task(watch_inbox(settings))
        yield
        if task:
            task.cancel()

    app = FastAPI(title="riffdb", lifespan=lifespan)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(ui.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
