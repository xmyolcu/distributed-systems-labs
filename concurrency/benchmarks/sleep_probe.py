"""Makinenin zamanlayıcı ve CPU davranışı zamanla değişiyor mu?

Her 30 sn'lik pencerede: sleep(5 ms) aşımı, sabit CPU işinin süresi,
yük ortalaması ve en çok CPU kullanan 3 işlem.
"""
import os
import statistics
import subprocess
import time

WINDOW_S = 30
TOTAL_S = 1200  # 20 dk


def run(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def top3() -> str:
    lines = run("ps -Ao pcpu,comm -r | head -4 | tail -3").splitlines()
    out = []
    for line in lines:
        pcpu, comm = line.strip().split(None, 1)
        out.append(f"{comm.rsplit('/', 1)[-1]}:{pcpu}")
    return " ".join(out)


def p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, int(0.95 * len(s)) - 1)]


print("BAŞLANGIÇ pmset -g therm:\n" + run("pmset -g therm"), flush=True)
print("BAŞLANGIÇ pil:", run("pmset -g batt | tail -1"), flush=True)
print(f"{'sn':>5} {'sleep_med':>10} {'sleep_p95':>10} {'cpu_med':>9} {'load1':>6}  en çok CPU")

t0 = time.perf_counter()
win_start = t0
sleeps: list[float] = []
cpus: list[float] = []
while time.perf_counter() - t0 < TOTAL_S:
    a = time.perf_counter()
    time.sleep(0.005)
    sleeps.append((time.perf_counter() - a) * 1000)
    b = time.perf_counter()
    sum(range(100_000))               # sabit CPU işi
    cpus.append((time.perf_counter() - b) * 1000)
    if time.perf_counter() - win_start >= WINDOW_S:
        print(f"{win_start - t0:5.0f} {statistics.median(sleeps):10.3f} {p95(sleeps):10.3f} "
              f"{statistics.median(cpus):9.3f} {os.getloadavg()[0]:6.2f}  {top3()}", flush=True)
        sleeps, cpus, win_start = [], [], time.perf_counter()

print("BİTİŞ pmset -g therm:\n" + run("pmset -g therm"))