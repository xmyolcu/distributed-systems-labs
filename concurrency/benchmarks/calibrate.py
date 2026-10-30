"""Havuzun gerçek kapasitesini (görev/sn) ölçer.

Üretici BLOCK politikasıyla kuyruğu sürekli dolu tutar; worker'lar hiç boş
kalmaz. Bu durumda saniyedeki tamamlanan görev sayısı havuzun kapasitesidir.
"""
import sys
import threading
import time
from pathlib import Path

# benchmarks/ klasöründen çalıştırıldığında src/ içindeki paketi bulabilmek için.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workerpool import BackpressurePolicy, WorkerPool  # noqa: E402

SERVICE_TIME_S = 0.005   # görev süresi: 5 ms (I/O benzeri, GIL'i bırakır)
NUM_WORKERS = 4
QUEUE_SIZE = 50


def task() -> None:
    time.sleep(SERVICE_TIME_S)


def measure_capacity(warmup_s: float = 1.0, duration_s: float = 3.0) -> float:
    pool = WorkerPool(NUM_WORKERS, QUEUE_SIZE, BackpressurePolicy.BLOCK)
    stop = threading.Event()

    def producer() -> None:
        # BLOCK: kuyruk dolunca submit() bekler, böylece kuyruk hep dolu kalır.
        while not stop.is_set():
            pool.submit(task)

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    time.sleep(warmup_s)                      # ısınma: ölçüme katılmaz
    c0 = pool.stats().completed
    t0 = time.perf_counter()
    time.sleep(duration_s)
    c1 = pool.stats().completed
    elapsed = time.perf_counter() - t0

    stop.set()
    t.join()
    pool.shutdown(wait=True)
    return (c1 - c0) / elapsed


if __name__ == "__main__":
    theoretical = NUM_WORKERS / SERVICE_TIME_S
    print(f"Teorik kapasite: {theoretical:.0f} görev/sn "
          f"({NUM_WORKERS} worker / {SERVICE_TIME_S * 1000:.0f} ms)")
    results = []
    for i in range(3):
        cap = measure_capacity()
        results.append(cap)
        print(f"  ölçüm {i + 1}: {cap:.0f} görev/sn")
    mean = sum(results) / len(results)
    spread = (max(results) - min(results)) / mean * 100
    print(f"Ortalama gerçek kapasite: {mean:.0f} görev/sn "
          f"(teorik değerin %{mean / theoretical * 100:.0f}'i, ölçümler arası fark %{spread:.1f})")