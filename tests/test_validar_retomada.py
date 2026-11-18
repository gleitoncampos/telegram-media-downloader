def _lote(**extra):
    lote = {"midia": "1", "limite": 100, "destino": None, "estrutura": False,
            "acoes": ["1"], "drive": False, "grupos": []}
    lote.update(extra)
    return lote


def test_lote_drive_recusado(t):
    erro = t.validar_retomada(_lote(drive=True))
    assert erro and "Drive" in erro


def test_destino_inexistente_nao_cria_pasta(t, tmp_path):
    destino = tmp_path / "nao_existe"
    erro = t.validar_retomada(_lote(destino=str(destino)))
    assert erro and str(destino) in erro
    assert not destino.exists()


def test_estrutura_sem_arquivo(t, monkeypatch):
    monkeypatch.setattr(t, "load_course_structure", lambda *a, **k: None)
    erro = t.validar_retomada(_lote(estrutura=True))
    assert erro and "course_structure.json" in erro


def test_lote_valido(t, tmp_path):
    assert t.validar_retomada(_lote(destino=str(tmp_path))) is None


def test_destino_nulo_usa_downloads(t):
    assert t.validar_retomada(_lote(destino=None)) is None
