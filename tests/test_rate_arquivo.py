from test_freio_download import MonitorFalso, preparar


def test_calcular_rate_normal(t):
    assert t.calcular_rate(1000, 4.0) == 250


def test_calcular_rate_duracao_zero(t):
    assert t.calcular_rate(1000, 0) == 0
    assert t.calcular_rate(1000, -1) == 0


def test_calcular_rate_tamanho_zero(t):
    assert t.calcular_rate(0, 5.0) == 0


def test_download_grava_rate_no_log(t, tmp_path, monkeypatch):
    rodar, chamadas = preparar(t, tmp_path, monkeypatch, MonitorFalso(False), sucesso=True)
    assert rodar() == 0
    entradas = t.load_download_log()["G"]["downloaded"].values()
    assert len(entradas) == 2
    for entrada in entradas:
        assert isinstance(entrada["rate"], int) and entrada["rate"] >= 0
        assert entrada["size"] == 100
