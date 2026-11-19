from test_freio_download import MonitorFalso


def entrada(downloaded, pendentes, failed=None):
    return {
        "downloaded": downloaded,
        "failed": failed or {},
        "stats": {"completed": len(downloaded), "errors": len(failed or {})},
        "scan": {"total": len(downloaded) + pendentes, "pendentes": pendentes, "em": "2026-11-18 00:00:00"},
    }


def test_interrompido_quando_disparou_e_incompleto(t):
    log = {"G": entrada({"1": {"file": "a", "size": 1}}, 3)}
    assert t.grupo_interrompido(MonitorFalso(True), "G", log) is True


def test_nao_interrompido_se_grupo_completo(t):
    log = {"G": entrada({"1": {"file": "a", "size": 1}}, 0)}
    assert t.grupo_interrompido(MonitorFalso(True), "G", log) is False


def test_nao_interrompido_sem_disparo(t):
    log = {"G": entrada({"1": {"file": "a", "size": 1}}, 3)}
    assert t.grupo_interrompido(MonitorFalso(False), "G", log) is False


def test_nome_com_caracteres_invalidos(t):
    log = {"A_B": entrada({"1": {"file": "a", "size": 1}}, 3)}
    assert t.grupo_interrompido(MonitorFalso(True), "A/B", log) is True
