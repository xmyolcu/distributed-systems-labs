"""Her konfigürasyon için 10 tohumlu özet + tohum 0 için saniye saniye zaman serisi."""
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retrysim import Config, simulate  # noqa: E402

CONFIGS = {
    "none": {},
    "immediate": dict(retry="immediate"),
    "backoff": dict(retry="backoff"),
    "budget": dict(retry="budget"),
    "none+drop": dict(drop_expired=True),
    "immediate+drop": dict(retry="immediate", drop_expired=True),
    "immediate+drop+lifo": dict(retry="immediate", drop_expired=True, lifo=True),
    "immediate+breaker": dict(retry="immediate", breaker=True),
}
SEEDS = range(10)


def main() -> None:
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    with (out_dir / "summary.csv").open("w", newline="") as fs, \
         (out_dir / "timeseries.csv").open("w", newline="") as ft:
        ws = csv.writer(fs)
        wt = csv.writer(ft)
        ws.writerow(["config", "seed", "success_rate", "amplification", "recovery_s", "latency_p99_ms"])
        wt.writerow(["config", "t", "arrivals", "sent", "success", "failed", "wasted", "queue_len"])
        for name, kw in CONFIGS.items():
            for seed in SEEDS:
                r = simulate(Config(seed=seed, **kw))
                ws.writerow([name, seed, round(r.success_rate, 4), round(r.amplification, 3),
                             "inf" if math.isinf(r.recovery_s) else r.recovery_s,
                             round(r.latency_p99_ms, 2)])
                if seed == 0:
                    for i, b in enumerate(r.bins):
                        wt.writerow([name, i, b.arrivals, b.sent, b.success, b.failed,
                                     b.wasted, b.queue_len])
            print(f"{name} tamam", flush=True)
    print("Yazıldı: results/summary.csv, results/timeseries.csv")


if __name__ == "__main__":
    main()