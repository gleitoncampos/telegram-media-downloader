class Relogio:
    agora = 0.0

    def __call__(self):
        return self.agora


def alimentar(m, relogio, minutos, bps, ativos):
    for _ in range(int(minutos * 6)):
        relogio.agora += 10
        m.registrar(int(bps * 10), ativos)


def novo(t):
    relogio = Relogio()
    m = t.MonitorVazao(limite_bps=500_000, janela_s=600, min_ativos=5, relogio=relogio)
    return m, relogio


def test_vazao_alta_nunca_dispara(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 15, 2_000_000, 10)
    assert m.lento() is False


def test_vazao_baixa_dispara_apos_janela(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 11, 30_000, 10)
    assert m.lento() is True
    assert m.vazao_bps < 500_000


def test_antes_da_janela_nao_dispara(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 9, 30_000, 10)
    assert m.lento() is False


def test_poucos_ativos_nunca_dispara(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 15, 30_000, 3)
    assert m.lento() is False


def test_buracos_sem_ativos_nao_contam(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 6, 2_000_000, 10)
    relogio.agora += 5 * 60
    alimentar(m, relogio, 2, 2_000_000, 10)
    assert m.lento() is False


def test_disparo_e_permanente(t):
    m, relogio = novo(t)
    alimentar(m, relogio, 11, 30_000, 10)
    assert m.lento() is True
    alimentar(m, relogio, 5, 5_000_000, 10)
    assert m.lento() is True
