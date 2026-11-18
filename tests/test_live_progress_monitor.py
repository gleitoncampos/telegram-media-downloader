class MonitorFalso:
    def __init__(self):
        self.chamadas = []

    def registrar(self, delta_bytes, ativos):
        self.chamadas.append((delta_bytes, ativos))


def test_update_envia_delta_ao_monitor(t):
    lp = t.LiveProgress()
    fake = MonitorFalso()
    lp.monitor = fake
    task_id, cb = lp.start("a.mp4")
    cb(1000, 5000)
    cb(3500, 5000)
    assert fake.chamadas == [(1000, 1), (2500, 1)]


def test_nova_tentativa_conta_do_zero(t):
    lp = t.LiveProgress()
    fake = MonitorFalso()
    lp.monitor = fake
    task_id, cb = lp.start("a.mp4")
    cb(1000, 5000)
    lp.finish(task_id)
    task_id, cb = lp.start("a.mp4")
    cb(400, 5000)
    assert fake.chamadas[-1] == (400, 1)


def test_total_zero_ainda_conta_bytes(t):
    lp = t.LiveProgress()
    fake = MonitorFalso()
    lp.monitor = fake
    task_id, cb = lp.start("a.mp4")
    cb(700, 0)
    assert fake.chamadas == [(700, 1)]


def test_sem_monitor_nao_quebra(t):
    lp = t.LiveProgress()
    task_id, cb = lp.start("a.mp4")
    cb(10, 100)
