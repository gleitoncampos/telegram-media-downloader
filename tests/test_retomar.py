import asyncio
from types import SimpleNamespace

import pytest


def _g(id, name):
    return SimpleNamespace(id=id, name=name)


class ClienteFalso:
    def __init__(self):
        self.desconectou = False

    async def disconnect(self):
        self.desconectou = True


@pytest.fixture
def ctx(t, tmp_path, monkeypatch):
    """Redireciona o lote e troca list_groups / executar_fila / run_post_actions por fakes."""
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    grupos = [_g(1, "A"), _g(2, "B"), _g(3, "C")]
    chamadas = []
    acoes = []

    async def fake_list_groups(client):
        return grupos

    async def fake_fila(client, queue, media_types, limit, base_dir, course_structure,
                        drive_service, drive_root_folder_id, interativo, lote=None):
        chamadas.append({"queue": queue, "media_types": media_types, "limit": limit,
                         "base_dir": base_dir, "interativo": interativo, "lote": lote})
        return []

    monkeypatch.setattr(t, "list_groups", fake_list_groups)
    monkeypatch.setattr(t, "executar_fila", fake_fila)
    monkeypatch.setattr(t, "run_post_actions", lambda a, s: acoes.append(a))
    return SimpleNamespace(grupos=grupos, chamadas=chamadas, acoes=acoes, tmp=tmp_path)


def _gravar_lote(t, ctx, estados=("pendente", "pendente", "pendente"), **extra):
    lote = t.novo_lote("1", 100, None, False, {"1"}, ctx.grupos)
    for item, estado in zip(lote["grupos"], estados):
        item["estado"] = estado
    lote.update(extra)
    t.salvar_lote(lote)
    return lote


def _retomar(t):
    cliente = ClienteFalso()
    return asyncio.run(t.retomar(cliente)), cliente


def test_parse_args_sem_flag(t):
    assert t.parse_args([]).resume is False


def test_parse_args_resume(t):
    assert t.parse_args(["--resume"]).resume is True


def test_parse_args_flag_desconhecida(t):
    with pytest.raises(SystemExit) as e:
        t.parse_args(["--nao-existe"])
    assert e.value.code == 2


def test_sem_lote_codigo_2(t, ctx):
    codigo, _ = _retomar(t)
    assert codigo == 2
    assert ctx.chamadas == []


def test_tudo_concluido_codigo_0_sem_baixar(t, ctx):
    _gravar_lote(t, ctx, estados=("concluido",) * 3)
    codigo, _ = _retomar(t)
    assert codigo == 0
    assert ctx.chamadas == []


def test_retoma_so_nao_concluidos_na_ordem(t, ctx):
    _gravar_lote(t, ctx, estados=("concluido", "interrompido", "pendente"))
    codigo, _ = _retomar(t)
    assert codigo == 0
    assert len(ctx.chamadas) == 1
    assert ctx.chamadas[0]["queue"] == [ctx.grupos[1], ctx.grupos[2]]
    assert ctx.chamadas[0]["interativo"] is False


def test_grupo_ausente_codigo_1(t, ctx):
    lote = _gravar_lote(t, ctx)
    lote["grupos"][0] = {"id": 77, "nome": "Sumiu", "estado": "pendente"}
    t.salvar_lote(lote)
    codigo, _ = _retomar(t)
    assert codigo == 1
    assert len(ctx.chamadas) == 1
    assert ctx.chamadas[0]["queue"] == [ctx.grupos[1], ctx.grupos[2]]


def test_opcoes_do_lote_chegam_a_fila(t, ctx):
    _gravar_lote(t, ctx, destino=str(ctx.tmp), limite=250)
    _retomar(t)
    c = ctx.chamadas[0]
    assert c["media_types"] == t.MEDIA_FILTERS["1"]["types"]
    assert c["limit"] == 250
    assert c["base_dir"] == ctx.tmp.resolve()


def test_acao_final_executada(t, ctx):
    _gravar_lote(t, ctx, acoes=["4"])
    codigo, cliente = _retomar(t)
    assert codigo == 0
    assert ctx.acoes == [{"4"}]
    assert cliente.desconectou is True


def test_lote_invalido_codigo_2(t, ctx):
    _gravar_lote(t, ctx, destino=str(ctx.tmp / "nao_existe"))
    codigo, _ = _retomar(t)
    assert codigo == 2
    assert ctx.chamadas == []
    assert not (ctx.tmp / "nao_existe").exists()
