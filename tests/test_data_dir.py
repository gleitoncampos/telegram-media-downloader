from pathlib import Path

import telegram_downloader as t


def test_sem_variavel_usa_a_pasta_do_script():
    assert t.resolver_data_dir({}) == Path(t.__file__).parent


def test_variavel_vazia_equivale_a_ausente():
    assert t.resolver_data_dir({"TMD_DATA_DIR": ""}) == Path(t.__file__).parent


def test_variavel_define_o_diretorio():
    assert t.resolver_data_dir({"TMD_DATA_DIR": "/data"}) == Path("/data")


def test_constantes_de_estado_derivam_de_data_dir():
    assert t.CONFIG_FILE == t.DATA_DIR / "config.json"
    assert t.LOG_FILE == t.DATA_DIR / "download_log.json"
    assert t.LOTE_FILE == t.DATA_DIR / "ultimo_lote.json"
    assert t.LOGS_DIR == t.DATA_DIR / "logs"
    assert t.SESSION_NAME == str(t.DATA_DIR / "telegram_session")
    assert t.DESTINOS_FILE == Path(t.__file__).parent / "ultimos_destinos.json"
