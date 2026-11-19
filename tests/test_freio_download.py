import asyncio
from types import SimpleNamespace


class MonitorFalso:
    def __init__(self, lento):
        self._lento = lento
        self.avisado = False
        self.vazao_bps = 1234.0

    def lento(self):
        return self._lento


def preparar(t, tmp_path, monkeypatch, monitor, sucesso):
    monkeypatch.setattr(t, "DOWNLOAD_DELAY", 0)
    grupo = SimpleNamespace(name="G", entity=object(), id=1)

    async def fake_scan(*a, **k):
        stats = {"total_size": 0, "counts": {"video": 2, "photo": 0, "document": 0}}
        pending = [
            (11, "video", "a.mp4", tmp_path / "a.mp4", 100),
            (12, "video", "b.mp4", tmp_path / "b.mp4", 100),
        ]
        return stats, pending, 0

    chamadas = []

    async def fake_download(client, entity, message_id, caminho, expected_size, **kw):
        chamadas.append(message_id)
        if sucesso:
            caminho.write_bytes(b"x" * 100)
        return sucesso

    monkeypatch.setattr(t, "scan_group_media", fake_scan)
    monkeypatch.setattr(t, "download_with_retry", fake_download)

    def rodar():
        return asyncio.run(t.download_media_from_group(
            None, grupo, ["video"], None, base_dir=tmp_path, interactive=False, monitor=monitor,
        ))

    return rodar, chamadas


def test_freio_nao_baixa_nem_marca_falha(t, tmp_path, monkeypatch):
    rodar, chamadas = preparar(t, tmp_path, monkeypatch, MonitorFalso(True), sucesso=True)
    assert rodar() == 0
    assert chamadas == []
    log = t.load_download_log()
    assert log["G"]["failed"] == {}
    assert log["G"]["scan"]["pendentes"] == 2


def test_sem_freio_baixa_normal(t, tmp_path, monkeypatch):
    rodar, chamadas = preparar(t, tmp_path, monkeypatch, MonitorFalso(False), sucesso=True)
    assert rodar() == 0
    log = t.load_download_log()
    assert len(log["G"]["downloaded"]) == 2
    assert log["G"]["scan"]["pendentes"] == 0


def test_aviso_so_uma_vez(t, tmp_path, monkeypatch, capsys):
    rodar, chamadas = preparar(t, tmp_path, monkeypatch, MonitorFalso(True), sucesso=True)
    rodar()
    assert capsys.readouterr().out.count("Rate limit detectado") == 1
