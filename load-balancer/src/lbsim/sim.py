"""Ayrık olay simülasyonu: bir yük dengeleyici ve N sunucu (her biri kendi FIFO kuyruğuyla).

Saat gerçek zaman değil, simülasyon zamanıdır: sonuçlar deterministiktir ve
işletim sistemi zamanlayıcısından etkilenmez (Lab 1'deki ölçüm kaymasının dersi).
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass

POLICIES = ("rr", "lc", "p2c")


@dataclass(frozen=True)
class Result:
    policy: str
    arrival_rate: float
    completed: int
    throughput: float               # ölçüm penceresinde tamamlanan iş / sn
    resp_mean_ms: float
    resp_p50_ms: float
    resp_p95_ms: float
    resp_p99_ms: float
    utilization: tuple[float, ...]  # sunucu başına meşguliyet oranı (yaklaşık)
    share: tuple[float, ...]        # sunucu başına yönlendirilen iş payı


def _pct(sorted_xs: list[float], q: float) -> float:
    """Nearest-rank yüzdelik (Lab 1 ile aynı yöntem)."""
    return sorted_xs[max(0, math.ceil(q * len(sorted_xs)) - 1)]


def simulate(policy: str, speeds: list[float], arrival_rate: float,
             n_requests: int = 100_000, seed: int = 0, base_service_ms: float = 10.0,
             service: str = "exp", warmup_frac: float = 0.1) -> Result:
    """policy: 'rr' (round robin), 'lc' (least connections), 'p2c' (power of two choices).
    speeds: sunucu hızları (1.0 = normal, 0.5 = yarı hız).
    arrival_rate: saniyedeki ortalama istek (Poisson).
    service: 'exp' (üstel) veya 'lognormal' (ağır kuyruklu, aynı ortalama).
    """
    if policy not in POLICIES:
        raise ValueError(f"bilinmeyen politika: {policy}")
    rng = random.Random(seed)
    n = len(speeds)
    mean_service_s = [base_service_ms / 1000 / s for s in speeds]

    def draw(i: int) -> float:
        m = mean_service_s[i]
        if service == "exp":
            return rng.expovariate(1 / m)
        if service == "lognormal":
            sigma = 1.0  # ortalama m'de tutulur, varyans üsteldekinden yüksek
            return rng.lognormvariate(math.log(m) - sigma ** 2 / 2, sigma)
        raise ValueError(f"bilinmeyen servis dağılımı: {service}")

    in_sys = [0] * n                       # sunucudaki iş sayısı (kuyruk + serviste)
    queues: list[deque] = [deque() for _ in range(n)]
    busy = [False] * n
    busy_time = [0.0] * n
    assigned = [0] * n
    events: list = []                      # (bitiş zamanı, sıra no, sunucu, istek no, varış)
    seq = 0
    warmup = int(n_requests * warmup_frac)
    resp: list[float] = []
    t_warm = None                          # ölçüm penceresinin başı
    done_in_window = 0
    draining = False
    rr_next = 0

    def start(i: int, now: float, req: int, arr: float) -> None:
        nonlocal seq
        st = draw(i)
        busy[i] = True
        busy_time[i] += st
        heapq.heappush(events, (now + st, seq, i, req, arr))
        seq += 1

    def depart() -> float:
        nonlocal done_in_window
        now, _, i, req, arr = heapq.heappop(events)
        in_sys[i] -= 1
        if req >= warmup:
            resp.append(now - arr)
        if t_warm is not None and not draining:
            done_in_window += 1
        if queues[i]:
            r2, a2 = queues[i].popleft()
            start(i, now, r2, a2)
        else:
            busy[i] = False
        return now

    t = 0.0
    for req in range(n_requests):
        t += rng.expovariate(arrival_rate)
        while events and events[0][0] <= t:   # bu varıştan önce biten işler
            depart()
        if req == warmup:
            t_warm = t

        if policy == "rr":
            i = rr_next
            rr_next = (rr_next + 1) % n
        elif policy == "lc":
            m = min(in_sys)
            i = rng.choice([k for k in range(n) if in_sys[k] == m])  # eşitlikte rastgele
        else:  # p2c
            a, b = rng.sample(range(n), 2)
            i = a if in_sys[a] <= in_sys[b] else b

        assigned[i] += 1
        in_sys[i] += 1
        if busy[i]:
            queues[i].append((req, t))
        else:
            start(i, t, req, t)

    t_end = t
    draining = True                        # varışlar bitti; kalanları boşalt
    last = t_end
    while events:
        last = depart()

    resp.sort()
    window = t_end - (t_warm or 0.0)
    return Result(
        policy=policy,
        arrival_rate=arrival_rate,
        completed=len(resp),
        throughput=done_in_window / window if window > 0 else 0.0,
        resp_mean_ms=1000 * sum(resp) / len(resp),
        resp_p50_ms=1000 * _pct(resp, 0.50),
        resp_p95_ms=1000 * _pct(resp, 0.95),
        resp_p99_ms=1000 * _pct(resp, 0.99),
        utilization=tuple(bt / last for bt in busy_time),
        share=tuple(a / n_requests for a in assigned),
    )