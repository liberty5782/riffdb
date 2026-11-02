import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from riffdb import db
from riffdb.config import Settings
from riffdb.main import create_app


def make_wav(path: Path, seconds: float = 1.0, freq_seed: int = 0) -> Path:
    """Write a tiny mono WAV; `freq_seed` varies the content so hashes differ."""
    rate = 8000
    frames = b"".join(struct.pack("<h", ((i * (freq_seed + 3)) % 2000) - 1000) for i in range(int(rate * seconds)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", inbox_poll_s=0)
    s.ensure_dirs()
    return s


@pytest.fixture
def conn(settings: Settings):
    c = db.connect(settings.db_path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c
