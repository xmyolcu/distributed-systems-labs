import threading
import time

import pytest

from workerpool import BackpressurePolicy, QueueFull, WorkerPool

# Testlerin asılı kalmaması için tüm beklemeler bu üst sınırla yapılır.
TIMEOUT = 5.0


def _occupy_single_worker(pool: WorkerPool) -> threading.Event:
    """Tek worker'ı bir kapıda bekletir ve worker görevi ALDIKTAN sonra döner.

    Böylece kuyruk tam olarak queue_size kadar dolar; yarış durumu olmadan
    deterministik aşırı yük senaryosu kurulur.
    """
    started = threading.Event()
    gate = threading.Event()

    def blocker() -> None:
        started.set()
        gate.wait(TIMEOUT)

    assert pool.submit(blocker)
    assert started.wait(TIMEOUT)
    return gate


def _shutdown_with_deadline(pool: WorkerPool) -> None:
    # shutdown'ı ayrı thread'de çalıştırıp süre sınırı koyuyoruz: deadlock testte asılı kalmak yerine fail eder.
    t = threading.Thread(target=pool.shutdown)
    t.start()
    t.join(TIMEOUT)
    assert not t.is_alive(), "shutdown deadlock'a girdi"


def test_block_completes_all_tasks():
    pool = WorkerPool(num_workers=4, queue_size=2, policy=BackpressurePolicy.BLOCK)
    lock = threading.Lock()
    done = []

    def work(i: int) -> None:
        time.sleep(0.001)
        with lock:
            done.append(i)

    for i in range(200):
        assert pool.submit(work, i) is True
    _shutdown_with_deadline(pool)

    s = pool.stats()
    assert sorted(done) == list(range(200))
    assert (s.submitted, s.completed, s.failed, s.dropped, s.rejected) == (200, 200, 0, 0, 0)
    assert s.queue_length == 0
    assert 0.0 <= s.avg_wait_s <= s.p95_wait_s


def test_drop_counts_shed_tasks_under_overload():
    pool = WorkerPool(num_workers=1, queue_size=2, policy=BackpressurePolicy.DROP)
    gate = _occupy_single_worker(pool)

    assert pool.submit(lambda: None) is True
    assert pool.submit(lambda: None) is True
    # Kuyruk dolu: sonraki 5 görev atılmalı.
    results = [pool.submit(lambda: None) for _ in range(5)]
    assert results == [False] * 5

    gate.set()
    _shutdown_with_deadline(pool)
    s = pool.stats()
    assert s.dropped == 5
    assert s.submitted == 3
    assert s.completed == 3
    assert s.rejected == 0


def test_reject_raises_queue_full():
    pool = WorkerPool(num_workers=1, queue_size=1, policy=BackpressurePolicy.REJECT)
    gate = _occupy_single_worker(pool)

    assert pool.submit(lambda: None) is True
    with pytest.raises(QueueFull):
        pool.submit(lambda: None)

    gate.set()
    _shutdown_with_deadline(pool)
    s = pool.stats()
    assert s.rejected == 1
    assert s.dropped == 0
    assert s.completed == 2


def test_shutdown_drains_queue_and_refuses_new_tasks():
    pool = WorkerPool(num_workers=1, queue_size=10, policy=BackpressurePolicy.BLOCK)
    gate = _occupy_single_worker(pool)
    done = []
    for i in range(10):
        pool.submit(done.append, i)
    assert pool.stats().queue_length == 10

    # Kapı shutdown başladıktan sonra açılır: shutdown kuyruğu atmamalı, boşaltmalı.
    threading.Timer(0.05, gate.set).start()
    _shutdown_with_deadline(pool)

    assert done == list(range(10))  # tek worker: FIFO sıra korunur
    s = pool.stats()
    assert s.completed == 11
    assert s.queue_length == 0
    assert all(not t.is_alive() for t in pool._threads)
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)


def test_failing_task_does_not_kill_worker():
    pool = WorkerPool(num_workers=1, queue_size=4, policy=BackpressurePolicy.BLOCK)
    done = threading.Event()

    def boom() -> None:
        raise ValueError("beklenen hata")

    pool.submit(boom)
    pool.submit(boom)
    # Tek worker var: bu görev çalışıyorsa worker önceki hatalardan sağ çıkmıştır.
    pool.submit(done.set)
    assert done.wait(TIMEOUT)

    _shutdown_with_deadline(pool)
    s = pool.stats()
    assert s.failed == 2
    assert s.completed == 1


def test_block_with_timeout_does_not_deadlock():
    pool = WorkerPool(num_workers=1, queue_size=1, policy=BackpressurePolicy.BLOCK)
    gate = _occupy_single_worker(pool)
    assert pool.submit(lambda: None) is True

    start = time.monotonic()
    with pytest.raises(QueueFull):
        pool.submit(lambda: None, timeout=0.1)
    elapsed = time.monotonic() - start
    assert 0.09 <= elapsed < 1.0

    gate.set()
    _shutdown_with_deadline(pool)
    s = pool.stats()
    assert s.rejected == 1
    assert s.completed == 2


def test_shutdown_while_producer_blocked_does_not_deadlock():
    # Ek senaryo: üretici BLOCK'ta (timeout'suz) beklerken shutdown çağrılırsa
    # ne shutdown ne üretici asılı kalmalı, bekleyen görev de kaybolmamalı.
    pool = WorkerPool(num_workers=1, queue_size=1, policy=BackpressurePolicy.BLOCK)
    gate = _occupy_single_worker(pool)
    pool.submit(lambda: None)

    late = threading.Event()
    producer = threading.Thread(target=pool.submit, args=(late.set,))
    producer.start()
    time.sleep(0.05)  # üreticinin put() içinde bloklanmasına fırsat ver

    shutter = threading.Thread(target=pool.shutdown)
    shutter.start()
    time.sleep(0.05)
    gate.set()

    producer.join(TIMEOUT)
    shutter.join(TIMEOUT)
    assert not producer.is_alive() and not shutter.is_alive()
    assert late.is_set()
    assert pool.stats().completed == 3


def test_block_wait_time_excludes_time_blocked_in_submit():
    # 1 worker, kuyruk 1: üçüncü submit yaklaşık 0.2 sn bloklanır.
    # Kuyrukta bekleme ~0.2 sn olmalı; bloklanma süresi eklenirse ~0.4 sn çıkar.
    with WorkerPool(1, 1, BackpressurePolicy.BLOCK) as pool:
        for _ in range(3):
            pool.submit(time.sleep, 0.2)
    assert pool.stats().p95_wait_s < 0.3
