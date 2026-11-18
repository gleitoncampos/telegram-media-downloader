import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    def _bloqueia(*args, **kwargs):
        raise RuntimeError("teste tentou abrir conexão de rede")

    monkeypatch.setattr(socket.socket, "connect", _bloqueia)


@pytest.fixture
def t(tmp_path, monkeypatch):
    import telegram_downloader as modulo

    monkeypatch.setattr(modulo, "LOG_FILE", tmp_path / "download_log.json")
    return modulo
