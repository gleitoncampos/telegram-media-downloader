import asyncio
from types import SimpleNamespace

import pytest


def _g(id, name):
    return SimpleNamespace(id=id, name=name)


@pytest.fixture
def lote_arq(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    return t.LOTE_FILE


def _rodar(t, fila, lote=None, interativo=False, monitor=None):
    return asyncio.run(
        t.executar_fila(None, fila, ["video"], 100, None, None, None, None, interativo, lote,
                        monitor=monitor)
    )


def _gravar(t, g, pendentes, downloaded=None):
    log = t.load_download_log()
    log[t.sanitize_filename(g.name)] = {
        "downloaded": downloaded if downloaded is not None else {"1": {}},
        "failed": {},
        "scan": {"total": 1, "pendentes": pendentes},
    }
    t.save_download_log(log)


def _estados(t):
    return [g["estado"] for g in t.carregar_lote()["grupos"]]


def test_devolve_grupos_com_falha(t, lote_arq, monkeypatch):
    async def fake(client, g, *a, **k):
        return 1 if g.name == "B" else 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    fila = [_g(1, "A"), _g(2, "B")]
    assert _rodar(t, fila) == (["B"], [])


def test_estado_concluido_quando_log_completo(t, lote_arq, monkeypatch):
    fila = [_g(1, "A")]
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)

    async def fake(client, g, *a, **k):
        _gravar(t, g, 0)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, fila, lote)
    assert _estados(t) == ["concluido"]


def test_estado_interrompido_quando_incompleto(t, lote_arq, monkeypatch):
    fila = [_g(1, "A")]
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)

    async def fake(client, g, *a, **k):
        _gravar(t, g, 2)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, fila, lote)
    assert _estados(t) == ["interrompido"]


def test_log_antigo_nao_conclui_grupo_que_falhou(t, lote_arq, monkeypatch):
    fila = [_g(1, "A")]
    _gravar(t, fila[0], 0)  # log "completo" de execução anterior
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)

    async def fake(client, g, *a, **k):
        return 1

    monkeypatch.setattr(t, "download_media_from_group", fake)
    assert _rodar(t, fila, lote) == (["A"], [])
    assert _estados(t) == ["interrompido"]


def test_grupo_sem_midia_conclui(t, lote_arq, monkeypatch):
    fila = [_g(1, "A")]
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)

    async def fake(client, g, *a, **k):
        log = t.load_download_log()
        log[t.sanitize_filename(g.name)] = {
            "downloaded": {}, "failed": {}, "scan": {"total": 0, "pendentes": 0},
        }
        t.save_download_log(log)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, fila, lote)
    assert _estados(t) == ["concluido"]


def test_excecao_marca_interrompido(t, lote_arq, monkeypatch):
    fila = [_g(1, "A")]
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)

    async def fake(client, g, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(t, "download_media_from_group", fake)
    with pytest.raises(KeyboardInterrupt):
        _rodar(t, fila, lote)
    assert _estados(t) == ["interrompido"]


def test_sem_lote_nao_cria_arquivo(t, lote_arq, monkeypatch):
    async def fake(client, g, *a, **k):
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, [_g(1, "A")], lote=None)
    assert not lote_arq.exists()


def test_repassa_interativo(t, lote_arq, monkeypatch):
    recebidos = []

    async def fake(client, g, *a, **k):
        recebidos.append(k["interactive"])
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, [_g(1, "A")], interativo=True)
    _rodar(t, [_g(1, "A")], interativo=False)
    assert recebidos == [True, False]


class MonitorFalso:
    """lento() controlável: True a partir da n-ésima consulta (1-based); None = nunca."""

    def __init__(self, lento_a_partir=None):
        self.consultas = 0
        self.lento_a_partir = lento_a_partir
        self.avisado = False
        self.vazao_bps = 0.0

    def lento(self):
        self.consultas += 1
        return self.lento_a_partir is not None and self.consultas >= self.lento_a_partir


def test_sem_disparo_comportamento_antigo(t, lote_arq, monkeypatch):
    chamados = []

    async def fake(client, g, *a, **k):
        chamados.append(g.name)
        return 1 if g.name == "B" else 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    fila = [_g(1, "A"), _g(2, "B")]
    assert _rodar(t, fila, monitor=MonitorFalso()) == (["B"], [])
    assert chamados == ["A", "B"]


def test_freio_para_os_grupos_seguintes(t, lote_arq, monkeypatch):
    chamados = []
    monitor = MonitorFalso(lento_a_partir=2)  # 1ª consulta: início de A; a partir da 2ª (pós-A) está lento

    async def fake(client, g, *a, **k):
        chamados.append(g.name)
        _gravar(t, g, 5)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    fila = [_g(1, "A"), _g(2, "B"), _g(3, "C")]
    falhas, interrompidos = _rodar(t, fila, monitor=monitor)
    assert chamados == ["A"]
    assert interrompidos == ["A", "B", "C"]
    assert falhas == []


def test_pulado_pelo_freio_fica_pendente(t, lote_arq, monkeypatch):
    fila = [_g(1, "A"), _g(2, "B")]
    lote = t.novo_lote("1", 100, None, False, {"1"}, fila)
    monitor = MonitorFalso(lento_a_partir=2)

    async def fake(client, g, *a, **k):
        _gravar(t, g, 5)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    _rodar(t, fila, lote, monitor=monitor)
    assert _estados(t) == ["interrompido", "pendente"]


def test_grupo_completo_antes_do_freio_nao_e_interrompido(t, lote_arq, monkeypatch):
    monitor = MonitorFalso(lento_a_partir=2)

    async def fake(client, g, *a, **k):
        _gravar(t, g, 0)
        return 0

    monkeypatch.setattr(t, "download_media_from_group", fake)
    fila = [_g(1, "A"), _g(2, "B")]
    assert _rodar(t, fila, monitor=monitor) == ([], ["B"])
