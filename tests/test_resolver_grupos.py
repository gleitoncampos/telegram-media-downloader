from types import SimpleNamespace


def _g(id, name):
    return SimpleNamespace(id=id, name=name)


def _lote(*itens):
    return {"grupos": [{"id": i, "nome": n, "estado": e} for i, n, e in itens]}


def test_acha_pelo_id(t):
    lote = _lote((1, "Antigo", "pendente"))
    a = _g(1, "Novo nome")
    encontrados, ausentes = t.resolver_grupos(lote, [a])
    assert encontrados == [a]
    assert ausentes == []


def test_id_mudou_acha_pelo_nome(t):
    lote = _lote((1, "Curso A", "pendente"))
    a = _g(99, "Curso A")
    encontrados, ausentes = t.resolver_grupos(lote, [a])
    assert encontrados == [a]
    assert ausentes == []


def test_nome_duplicado_e_ausente(t):
    lote = _lote((1, "Curso A", "pendente"))
    encontrados, ausentes = t.resolver_grupos(lote, [_g(98, "Curso A"), _g(99, "Curso A")])
    assert encontrados == []
    assert ausentes == ["Curso A"]


def test_ignora_concluidos(t):
    lote = _lote((1, "Curso A", "concluido"), (2, "Curso B", "interrompido"))
    b = _g(2, "Curso B")
    encontrados, ausentes = t.resolver_grupos(lote, [_g(1, "Curso A"), b])
    assert encontrados == [b]
    assert ausentes == []
    # concluído ausente também não aparece
    encontrados, ausentes = t.resolver_grupos(lote, [b])
    assert ausentes == []


def test_mantem_ordem_do_lote(t):
    lote = _lote((2, "B", "pendente"), (1, "A", "pendente"))
    a, b = _g(1, "A"), _g(2, "B")
    encontrados, _ = t.resolver_grupos(lote, [a, b])
    assert encontrados == [b, a]


def test_id_tem_precedencia_sobre_nome(t):
    lote = _lote((1, "Curso A", "pendente"))
    pelo_id = _g(1, "Outro nome")
    pelo_nome = _g(2, "Curso A")
    encontrados, _ = t.resolver_grupos(lote, [pelo_nome, pelo_id])
    assert encontrados == [pelo_id]


def test_casar_pelo_nome_atualiza_o_lote(t):
    lote = _lote((1, "Curso A", "pendente"))
    t.resolver_grupos(lote, [_g(99, "Curso A")])
    assert lote["grupos"][0]["id"] == 99


def test_casar_pelo_id_atualiza_o_nome(t):
    lote = _lote((1, "Antigo", "pendente"))
    t.resolver_grupos(lote, [_g(1, "Nome atual")])
    assert lote["grupos"][0]["nome"] == "Nome atual"
