import asyncio
import sys

import pytest


@pytest.fixture
def arquivos(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    sess = tmp_path / "telegram_session.session"
    sess.write_text("")
    return cfg, sess


def test_com_terminal_nunca_recusa(t, tmp_path):
    assert t.erro_de_ambiente(False, tmp_path / "c", tmp_path / "s", tem_tty=True) is None


def test_prepare_sem_terminal_recusa(t, arquivos):
    cfg, sess = arquivos
    assert "terminal" in t.erro_de_ambiente(True, cfg, sess, tem_tty=False)


def test_resume_sem_terminal_com_tudo_pronto_segue(t, arquivos):
    cfg, sess = arquivos
    assert t.erro_de_ambiente(False, cfg, sess, tem_tty=False) is None


def test_sem_config_e_sem_terminal_recusa(t, arquivos, tmp_path):
    _, sess = arquivos
    assert "config.json" in t.erro_de_ambiente(False, tmp_path / "nao", sess, tem_tty=False)


def test_sem_sessao_e_sem_terminal_recusa(t, arquivos, tmp_path):
    cfg, _ = arquivos
    msg = t.erro_de_ambiente(False, cfg, tmp_path / "nao", tem_tty=False)
    assert "login" in msg and "-it" in msg


def test_main_resume_sem_terminal_e_sem_config_devolve_2(t, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(t, "SESSION_NAME", str(tmp_path / "telegram_session"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    def nao_deve_criar_cliente(*a, **k):
        raise AssertionError("criou TelegramClient")

    monkeypatch.setattr(t, "TelegramClient", nao_deve_criar_cliente)

    assert asyncio.run(t.main(["--resume"])) == 2


def test_main_sem_flag_nao_passa_pelo_guard(t, tmp_path, monkeypatch):
    """Sem --prepare/--resume o guard não age: o fluxo interativo segue para as credenciais."""
    monkeypatch.setattr(t, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(t, "setup_logging", lambda: None)

    class Chegou(Exception):
        pass

    def fake_credenciais():
        raise Chegou()

    monkeypatch.setattr(t, "load_credentials", fake_credenciais)

    with pytest.raises(Chegou):
        asyncio.run(t.main([]))
