"""Backpressure politikalarını (BLOCK / DROP / REJECT) artan yük altında karşılaştırır.

Deney tasarımı:
- Üretici tek thread, MUTLAK zaman çizelgesiyle sabit hızda görev yayınlar.
  Bir görevin gecikmesi sonrakilere yansımaz; geride kalırsa art arda gönderip yetişir.
- BLOCK'ta üretici kuyruk doluyken submit() içinde bekler ve hedef hızın gerisine düşer.
  Bu bir hata değil, ölçülen şeyin kendisidir (geri basıncın üreticiye yansıması).
  Üreticinin gerçekten ulaştığı hız achieved_offered_rate sütununda görünür.
- İlk 'warmup' saniyesi ölçüme katılmaz.
- Denemeler sırası karıştırılır (her tekrarda), zamanla oluşan sapma tek bir
  politikayı sistematik olarak etkilemesin diye.
"""
import argparse
import csv
import math
import random
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from calibrate import NUM_WORKERS, QUEUE_SIZE, SERVICE_TIME_S, measure_capacity  # noqa: E402
from workerpool import BackpressurePolicy, QueueFull, WorkerPool  # noqa: E402

MULTIPLIERS = [0.25, 0.5, 0.75, 0.9, 1.0, 1.1, 1.5, 2.0]
POLICIES = list(BackpressurePolicy)

FIELDS = [
    "policy", "rate_multiplier", "target_rate", "repeat",
    "offered", "accepted", "completed", "dropped", "rejected",
    "throughput", "queue_wait_mean_ms", "queue_wait_p95_ms",
    "submit_latency_mean_ms", "submit_latency_p95_ms",
    "schedule_lag_mean_ms", "schedule_lag_p95_ms",
    "achieved_offered_rate",
]


def task() -> None:
    time.sleep(SERVICE_TIME_S)


def p95(values: list[float]) -> float:
    """Nearest-rank p95 (pool.stats() ile aynı yöntem)."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sleep_until(t: float) -> None:
    d = t - time.perf_counter()
    if d > 0:
        time.sleep(d)


def snapshot(pool: WorkerPool) -> tuple[int, int]:
    """(completed, bekleme_örneği_sayısı) değerlerini tek kilit altında okur.

    Benchmark, pool'un özel alanlarına bilerek erişir: ısınma sonrası dilimi
    almak için ham örnekler gerekiyor ve pool API'sini bunun için şişirmek istemedik.
    """
    with pool._lock:
        return pool._completed, len(pool._wait_times)


def run_trial(policy: BackpressurePolicy, target_rate: float,
              warmup: float, duration: float) -> dict:
    pool = WorkerPool(NUM_WORKERS, QUEUE_SIZE, policy)
    interval = 1.0 / target_rate
    t_start = time.perf_counter()
    t_warm = t_start + warmup
    t_end = t_start + duration
    stop = threading.Event()
    samples: list[tuple[float, str, float]] = []  # (submit gecikmesi, sonuç, çizelge gecikmesi)

    def producer() -> None:
        next_t = t_start
        while not stop.is_set() and time.perf_counter() < t_end:
            sleep_until(next_t)       # geride isek hemen döner (yetişme)
            intended = next_t         # bu görevin planlanan gönderim zamanı
            next_t += interval
            t0 = time.perf_counter()
            try:
                outcome = "accepted" if pool.submit(task) else "dropped"
            except QueueFull:
                outcome = "rejected"
            latency = time.perf_counter() - t0
            if t_warm <= t0 < t_end:  # yalnızca ölçüm penceresi
                # schedule_lag: üretici planın ne kadar gerisinde (BLOCK'ta birikir)
                samples.append((latency, outcome, t0 - intended))

    th = threading.Thread(target=producer, daemon=True)
    th.start()

    sleep_until(t_warm)
    c0, w0 = snapshot(pool)
    ts0 = time.perf_counter()
    sleep_until(t_end)
    c1, w1 = snapshot(pool)
    ts1 = time.perf_counter()
    with pool._lock:
        waits = pool._wait_times[w0:w1]
    stop.set()
    th.join()
    pool.shutdown(wait=True)

    lat = [s[0] for s in samples]
    lag = [s[2] for s in samples]
    counts = {o: sum(1 for s in samples if s[1] == o) for o in ("accepted", "dropped", "rejected")}
    window = t_end - t_warm
    return {
        "policy": policy.value,
        "target_rate": round(target_rate, 1),
        "offered": len(samples),
        "accepted": counts["accepted"],
        "completed": c1 - c0,
        "dropped": counts["dropped"],
        "rejected": counts["rejected"],
        "throughput": round((c1 - c0) / (ts1 - ts0), 1),
        "queue_wait_mean_ms": round(mean(waits) * 1000, 3),
        "queue_wait_p95_ms": round(p95(waits) * 1000, 3),
        "submit_latency_mean_ms": round(mean(lat) * 1000, 4),
        "submit_latency_p95_ms": round(p95(lat) * 1000, 4),
        "schedule_lag_mean_ms": round(mean(lag) * 1000, 3),
        "schedule_lag_p95_ms": round(p95(lag) * 1000, 3),
        "achieved_offered_rate": round(len(samples) / window, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="1 tekrar, kısa süre (geliştirme)")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--duration", type=float, default=8.0, help="deneme süresi (sn), ısınma dahil")
    ap.add_argument("--warmup", type=float, default=2.0)
    args = ap.parse_args()

    if args.quick:
        args.repeats, args.duration, args.warmup = 1, 3.0, 1.0
        capacity = measure_capacity(0.5, 1.5)
        out = ROOT / "results" / "benchmark_quick.csv"
    else:
        capacity = sum(measure_capacity() for _ in range(3)) / 3
        out = ROOT / "results" / "benchmark.csv"
    print(f"Kalibre kapasite: {capacity:.0f} görev/sn")

    out.parent.mkdir(exist_ok=True)
    rng = random.Random(42)
    total = args.repeats * len(MULTIPLIERS) * len(POLICIES)
    done = 0

    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for rep in range(1, args.repeats + 1):
            order = [(m, p) for m in MULTIPLIERS for p in POLICIES]
            rng.shuffle(order)  # sıra etkisini dağıtmak için
            for mult, policy in order:
                row = run_trial(policy, capacity * mult, args.warmup, args.duration)
                row.update(rate_multiplier=mult, repeat=rep)
                writer.writerow({k: row[k] for k in FIELDS})
                f.flush()  # yarıda kesilirse o ana kadarki veri kalsın
                done += 1
                print(f"[{done}/{total}] {policy.value:6s} x{mult:<4} "
                      f"throughput={row['throughput']:7.1f}  "
                      f"wait_p95={row['queue_wait_p95_ms']:7.2f} ms  "
                      f"dropped={row['dropped']} rejected={row['rejected']}")
    print(f"Yazıldı: {out}")


if __name__ == "__main__":
    main()