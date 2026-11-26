import asyncio
from types import SimpleNamespace

import pytest


def _g(id, name):
    return SimpleNamespace(id=id, name=name)


@pytest.fixture
def ctx(t, tmp_path, monkeypatch):
    """Redireciona o lote e troca list_groups / completar_scans_legados / escolher_grupos por fakes."""
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    grupos = [_g(1, "A"), _g(2, "B"), _g(3, "C")]
    escolha = SimpleNamespace(valor=None)

    async def fake_list_groups(client):
        return grupos

    async def fake_scans(client, groups, download_log):
        return None

    async def fake_escolher(groups, download_log=None):
        return escolha.valor

    monkeypatch.setattr(t, "list_groups", fake_list_groups)
    monkeypatch.setattr(t, "completar_scans_legados", fake_scans)
    monkeypatch.setattr(t, "escolher_grupos", fake_escolher)
    return SimpleNamespace(grupos=grupos, escolha=escolha, tmp=tmp_path)


def _preparar(t):
    return asyncio.run(t.preparar(object()))


def test_prepare_grava_lote_com_valores_do_container(ctx, t):
    ctx.escolha.valor = [ctx.grupos[1], ctx.grupos[2]]

    assert _preparar(t) == 0

    lote = t.carregar_lote()
    assert lote["midia"] == "4"
    assert lote["limite"] is None
    assert lote["destino"] == "/downloads"
    assert lote["estrutura"] is False
    assert lote["drive"] is False
    assert lote["acoes"] == ["1"]
    assert [(g["id"], g["estado"]) for g in lote["grupos"]] == [(2, "pendente"), (3, "pendente")]


def test_prepare_substitui_lote_anterior(ctx, t):
    anterior = t.novo_lote("1", 100, None, False, {"1"}, [ctx.grupos[0]])
    anterior["grupos"][0]["estado"] = "interrompido"
    t.salvar_lote(anterior)
    ctx.escolha.valor = [ctx.grupos[2]]

    assert _preparar(t) == 0

    assert [g["id"] for g in t.carregar_lote()["grupos"]] == [3]


def test_prepare_sem_selecao_nao_grava_e_retorna_2(ctx, t):
    ctx.escolha.valor = None

    assert _preparar(t) == 2
    assert not t.LOTE_FILE.exists()


def test_prepare_sem_selecao_preserva_lote_anterior(ctx, t):
    t.salvar_lote(t.novo_lote("1", 100, None, False, {"1"}, [ctx.grupos[0]]))
    antes = t.LOTE_FILE.read_bytes()
    ctx.escolha.valor = []

    assert _preparar(t) == 2
    assert t.LOTE_FILE.read_bytes() == antes


def test_prepare_e_resume_juntos_sao_recusados(t):
    with pytest.raises(SystemExit):
        t.parse_args(["--prepare", "--resume"])


def test_parse_args_prepare(t):
    assert t.parse_args(["--prepare"]).prepare is True
    assert t.parse_args([]).prepare is False
