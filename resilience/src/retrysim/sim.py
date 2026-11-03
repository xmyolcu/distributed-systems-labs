"""Ayrık olay simülasyonu: istemci (A) -> servis (B); timeout, retry, circuit breaker.

Amaç: kısa bir yavaşlamanın (tetikleyici) retry'lar yüzünden kalıcı bir aşırı yüke
dönüşüp dönüşmediğini (metastabil arıza) saniye saniye görmek.
"""
from __future__ import annotations

import heapq
import math
import random
import statistics
from collections import deque
from dataclasses import dataclass

RETRY_POLICIES = ("none", "immediate", "backoff", "budget")
ARRIVAL, SEND, FINISH, TIMEOUT, SAMPLE = range(5)


@dataclass
class Config:
    arrival_rate: float = 320.0       # kullanıcı isteği / sn (kapasitenin %80'i)
    workers: int = 4
    service_ms: float = 10.0          # normal ortalama servis süresi (kapasite 400/sn)
    timeout_ms: float = 100.0         # istemci deneme başına zaman aşımı
    retry: str = "none"
    max_retries: int = 3
    backoff_base_ms: float = 20.0
    backoff_cap_ms: float = 1000.0
    budget_ratio: float = 0.1         # her yeni istek 0.1 retry jetonu kazandırır
    breaker: bool = False
    breaker_window: int = 50          # son N deneme sonucuna bakar
    breaker_threshold: float = 0.5    # hata oranı bunu aşarsa devre açılır
    breaker_open_ms: float = 1000.0   # açık kalma süresi, sonra yeniden denenir
    drop_expired: bool = False        # B, süresi dolmuş isteği işlemeye başlamaz
    lifo: bool = False
    duration_s: float = 90.0
    trigger_start_s: float = 20.0
    trigger_end_s: float = 30.0
    trigger_factor: float = 2.0       # tetikleyici sırasında servis süresi çarpanı
    bin_s: float = 1.0
    seed: int = 0


@dataclass
class Bin:
    arrivals: int = 0   # yeni kullanıcı isteği
    sent: int = 0       # B'ye giden deneme (ilk + retry)
    success: int = 0    # zamanında yanıt alan kullanıcı isteği (goodput)
    failed: int = 0     # denemeleri tükenen ya da devre kesiciye takılan istek
    wasted: int = 0     # B'nin bitirdiği ama istemcinin çoktan vazgeçtiği iş
    queue_len: int = 0  # saniye sonunda B'nin kuyruğu


@dataclass
class Result:
    bins: list[Bin]
    success_rate: float
    amplification: float   # B'ye giden deneme / kullanıcı isteği
    pre_goodput: float     # tetikleyiciden önceki ortalama goodput (istek/sn)
    recovery_s: float      # tetikleyici bitince toparlanma süresi (inf = toparlanmadı)
    latency_p99_ms: float


def simulate(cfg: Config) -> Result:
    if cfg.retry not in RETRY_POLICIES:
        raise ValueError(f"bilinmeyen retry politikası: {cfg.retry}")
    rng = random.Random(cfg.seed)
    nb = math.ceil(cfg.duration_s / cfg.bin_s)
    bins = [Bin() for _ in range(nb)]
    timeout_s = cfg.timeout_ms / 1000

    events: list = []
    seq = 0

    def push(t: float, kind: int, data=None) -> None:
        nonlocal seq
        heapq.heappush(events, (t, seq, kind, data))
        seq += 1

    def bin_at(t: float) -> Bin:
        return bins[min(nb - 1, int(t / cfg.bin_s))]

    queue: deque = deque()            # B'de bekleyen deneme numaraları
    busy = 0
    user_arrival: dict[int, float] = {}
    retries_used: dict[int, int] = {}
    att_user: dict[int, int] = {}
    att_deadline: dict[int, float] = {}
    att_state: dict[int, int] = {}    # 0 bekliyor, 1 başarılı, 2 zaman aşımı
    next_att = 0
    tokens = 0.0
    window: deque = deque()
    open_until = -1.0
    latencies: list[float] = []

    def service_time(now: float) -> float:
        m = cfg.service_ms / 1000
        if cfg.trigger_start_s <= now < cfg.trigger_end_s:
            m *= cfg.trigger_factor
        return rng.expovariate(1 / m)

    def record_outcome(ok: bool, now: float) -> None:
        nonlocal open_until
        if not cfg.breaker:
            return
        window.append(ok)
        if len(window) > cfg.breaker_window:
            window.popleft()
        if (len(window) == cfg.breaker_window
                and window.count(False) / len(window) > cfg.breaker_threshold):
            open_until = now + cfg.breaker_open_ms / 1000
            window.clear()   # basitleştirilmiş half-open: süre dolunca sıfırdan başlar

    def try_start(now: float) -> None:
        nonlocal busy
        while busy < cfg.workers and queue:
            a = queue.pop() if cfg.lifo else queue.popleft()
            if cfg.drop_expired and now > att_deadline[a]:
                continue      # istemci zaten vazgeçti; boşuna çalışma
            busy += 1
            push(now + service_time(now), FINISH, a)

    def send(user: int, now: float) -> None:
        nonlocal next_att
        if cfg.breaker and now < open_until:
            bin_at(now).failed += 1   # hızlı başarısızlık: B'ye hiç gitmez
            return
        a = next_att
        next_att += 1
        att_user[a] = user
        att_deadline[a] = now + timeout_s
        att_state[a] = 0
        bin_at(now).sent += 1
        queue.append(a)
        push(now + timeout_s, TIMEOUT, a)
        try_start(now)

    push(rng.expovariate(cfg.arrival_rate), ARRIVAL)
    for i in range(nb):
        push((i + 1) * cfg.bin_s - 1e-9, SAMPLE, i)

    next_user = 0
    while events:
        now, _, kind, data = heapq.heappop(events)
        if now > cfg.duration_s:
            break
        if kind == ARRIVAL:
            u = next_user
            next_user += 1
            user_arrival[u] = now
            retries_used[u] = 0
            bin_at(now).arrivals += 1
            tokens = min(tokens + cfg.budget_ratio, 10.0)
            push(now + rng.expovariate(cfg.arrival_rate), ARRIVAL)
            send(u, now)
        elif kind == SEND:
            send(data, now)
        elif kind == FINISH:
            busy -= 1
            a = data
            if att_state[a] == 0:
                att_state[a] = 1
                bin_at(now).success += 1
                latencies.append(now - user_arrival[att_user[a]])
                record_outcome(True, now)
            else:
                bin_at(now).wasted += 1
            try_start(now)
        elif kind == TIMEOUT:
            a = data
            if att_state[a] != 0:
                continue
            att_state[a] = 2
            record_outcome(False, now)
            u = att_user[a]
            k = retries_used[u]
            if cfg.retry == "none" or k >= cfg.max_retries:
                bin_at(now).failed += 1
                continue
            if cfg.retry == "budget":
                if tokens < 1:
                    bin_at(now).failed += 1
                    continue
                tokens -= 1
            retries_used[u] = k + 1
            if cfg.retry == "backoff":
                cap = min(cfg.backoff_cap_ms, cfg.backoff_base_ms * 2 ** k)
                push(now + rng.uniform(0, cap) / 1000, SEND, u)   # tam jitter
            else:
                send(u, now)
        else:  # SAMPLE
            bins[data].queue_len = len(queue)

    arrivals = sum(b.arrivals for b in bins)
    sent = sum(b.sent for b in bins)
    success = sum(b.success for b in bins)
    pre = [b.success for b in bins[5:int(cfg.trigger_start_s / cfg.bin_s)]]
    pre_goodput = statistics.mean(pre) / cfg.bin_s if pre else 0.0

    # Toparlanma: tetikleyici bittikten sonra goodput'un art arda 3 saniye
    # tetikleyici öncesinin %90'ına dönmesi.
    start = int(cfg.trigger_end_s / cfg.bin_s)
    recovery = math.inf
    threshold = 0.9 * pre_goodput * cfg.bin_s
    for i in range(start, nb - 2):
        if all(bins[j].success >= threshold for j in range(i, i + 3)):
            recovery = (i - start) * cfg.bin_s
            break

    latencies.sort()
    p99 = latencies[max(0, math.ceil(0.99 * len(latencies)) - 1)] * 1000 if latencies else 0.0
    return Result(bins=bins, success_rate=success / arrivals if arrivals else 0.0,
                  amplification=sent / arrivals if arrivals else 0.0,
                  pre_goodput=pre_goodput, recovery_s=recovery, latency_p99_ms=p99)