"""Yük taraması: 2 senaryo x 2 servis dağılımı x 3 politika x 8 yük x 10 tohum."""
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lbsim import POLICIES, simulate  # noqa: E402

SCENARIOS = {"homojen": [1, 1, 1, 1], "heterojen": [1, 1, 1, 0.5]}
SERVICES = ["exp", "lognormal"]
RHOS = [0.3, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 0.95]
SEEDS = range(10)
N_REQUESTS = 100_000
BASE_SERVICE_MS = 10.0

FIELDS = ["scenario", "service", "policy", "rho", "seed", "arrival_rate", "throughput",
          "resp_mean_ms", "resp_p50_ms", "resp_p95_ms", "resp_p99_ms",
          "slow_share", "slow_util", "max_util"]


def main() -> None:
    out = ROOT / "results" / "sweep.csv"
    out.parent.mkdir(exist_ok=True)
    total = len(SCENARIOS) * len(SERVICES) * len(POLICIES) * len(RHOS) * len(SEEDS)
    done, t0 = 0, time.perf_counter()
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for scen, speeds in SCENARIOS.items():
            capacity = sum(speeds) * 1000 / BASE_SERVICE_MS   # iş/sn
            for service in SERVICES:
                for policy in POLICIES:
                    for rho in RHOS:
                        for seed in SEEDS:
                            r = simulate(policy, speeds, rho * capacity, n_requests=N_REQUESTS,
                                         seed=seed, base_service_ms=BASE_SERVICE_MS,
                                         service=service)
                            w.writerow({
                                "scenario": scen, "service": service, "policy": policy,
                                "rho": rho, "seed": seed,
                                "arrival_rate": round(rho * capacity, 2),
                                "throughput": round(r.throughput, 2),
                                "resp_mean_ms": round(r.resp_mean_ms, 3),
                                "resp_p50_ms": round(r.resp_p50_ms, 3),
                                "resp_p95_ms": round(r.resp_p95_ms, 3),
                                "resp_p99_ms": round(r.resp_p99_ms, 3),
                                "slow_share": round(r.share[-1], 4),
                                "slow_util": round(r.utilization[-1], 4),
                                "max_util": round(max(r.utilization), 4),
                            })
                            done += 1
                    print(f"[{done}/{total}] {scen} {service} {policy} "
                          f"({time.perf_counter() - t0:.0f} sn)", flush=True)
    print(f"Yazıldı: {out}")


if __name__ == "__main__":
    main()