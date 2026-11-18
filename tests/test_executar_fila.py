import asyncio
from types import SimpleNamespace

import pytest


def _g(id, name):
    return SimpleNamespace(id=id, name=name)


@pytest.fixture
def lote_arq(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    return t.LOTE_FILE


def _rodar(t, fila, lote=None, interativo=False):
    return asyncio.run(
        t.executar_fila(None, fila, ["video"], 100, None, None, None, None, interativo, lote)
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
    assert _rodar(t, fila) == ["B"]


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
    assert _rodar(t, fila, lote) == ["A"]
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
