import json
from types import SimpleNamespace


def _fila():
    return [SimpleNamespace(id=1, name="Curso A"), SimpleNamespace(id=2, name="Curso B")]


def _lote(t):
    return t.novo_lote("1", 100, "/tmp/destino", False, {"4", "2"}, _fila())


def test_novo_lote_formato(t):
    lote = t.novo_lote("1", None, None, True, {"4", "2"}, _fila(), drive=False)
    assert lote["midia"] == "1"
    assert lote["limite"] is None
    assert lote["destino"] is None
    assert lote["estrutura"] is True
    assert lote["acoes"] == ["2", "4"]
    assert lote["drive"] is False
    assert len(lote["iniciado_em"]) == 19
    assert lote["grupos"] == [
        {"id": 1, "nome": "Curso A", "estado": "pendente"},
        {"id": 2, "nome": "Curso B", "estado": "pendente"},
    ]


def test_salvar_e_carregar_ida_e_volta(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    lote = _lote(t)
    t.salvar_lote(lote)
    assert t.carregar_lote() == lote


def test_lote_ausente_devolve_none(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    assert t.carregar_lote() is None


def test_lote_corrompido_devolve_none(t, tmp_path, monkeypatch):
    arquivo = tmp_path / "ultimo_lote.json"
    monkeypatch.setattr(t, "LOTE_FILE", arquivo)
    arquivo.write_text("{nao e json")
    assert t.carregar_lote() is None
    arquivo.write_text("{}")
    assert t.carregar_lote() is None


def test_marcar_estado_persiste(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "LOTE_FILE", tmp_path / "ultimo_lote.json")
    lote = _lote(t)
    t.marcar_estado(lote, 1, "concluido")
    assert t.carregar_lote()["grupos"][0]["estado"] == "concluido"
    assert t.carregar_lote()["grupos"][1]["estado"] == "pendente"


def test_estado_final_concluido(t):
    assert t.estado_final(0, {"downloaded": {"1": {}}, "scan": {"pendentes": 0}}) == "concluido"


def test_estado_final_grupo_sem_midia(t):
    assert t.estado_final(0, {"downloaded": {}, "scan": {"total": 0, "pendentes": 0}}) == "concluido"


def test_estado_final_pendentes(t):
    assert t.estado_final(0, {"downloaded": {"1": {}}, "scan": {"pendentes": 3}}) == "interrompido"


def test_estado_final_com_erros_ou_excecao(t):
    entrada = {"downloaded": {"1": {}}, "scan": {"pendentes": 0}}
    assert t.estado_final(2, entrada) == "interrompido"
    assert t.estado_final(None, entrada) == "interrompido"


def test_estado_final_log_antigo_nao_vale(t):
    entrada_antiga = {"downloaded": {"1": {}}, "scan": {"pendentes": 0}}
    assert t.estado_final(None, entrada_antiga) == "interrompido"
    assert t.estado_final(0, None) == "interrompido"
    assert t.estado_final(0, {"downloaded": {"1": {}}}) == "interrompido"
