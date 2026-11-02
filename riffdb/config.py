import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    inbox_poll_s: float

    @property
    def db_path(self) -> Path:
        return self.data_dir / "riffdb.sqlite"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.audio_dir, self.inbox_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("RIFFDB_DATA_DIR", "./data")).resolve(),
        inbox_poll_s=float(os.environ.get("RIFFDB_INBOX_POLL_S", "30")),
    )
