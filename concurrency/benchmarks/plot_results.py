"""benchmark.csv'den dört panelli karşılaştırma grafiği üretir.

Her nokta: tekrarlar üzerinden ortalama; hata çubuğu: %95 güven aralığı (t dağılımı).
"""
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results" / "benchmark.csv"
OUT = ROOT / "results" / "backpressure_comparison.png"

# Küçük n için iki yönlü %95 t kritik değerleri (scipy bağımlılığı eklememek için).
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}

STYLE = {
    "block": dict(color="#1f77b4", marker="o", label="BLOCK"),
    "drop": dict(color="#d62728", marker="s", label="DROP"),
    "reject": dict(color="#2ca02c", marker="^", label="REJECT"),
}


def mean_ci(xs: list[float]) -> tuple[float, float]:
    m = statistics.mean(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, T95.get(len(xs) - 1, 1.96) * statistics.stdev(xs) / len(xs) ** 0.5


def main() -> None:
    rows = list(csv.DictReader(SRC.open()))
    # Yalnızca kayması %5'in altındaki tekrarlar.
    rows = [r for r in rows if float(r["capacity_drift_pct"]) < 5]
    reps = sorted({r["repeat"] for r in rows})

    metrics = {
        "throughput": lambda r: float(r["throughput"]),
        "wait": lambda r: float(r["queue_wait_p95_ms"]),
        "loss": lambda r: 100 * (int(r["dropped"]) + int(r["rejected"])) / max(1, int(r["offered"])),
        "lag": lambda r: max(float(r["schedule_lag_mean_ms"]), 1e-3),
    }
    data: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for name, fn in metrics.items():
            data[(name, r["policy"])][float(r["rate_multiplier"])].append(fn(r))

    cap = statistics.mean(float(r["capacity_start"]) for r in rows)
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    panels = [
        ("throughput", axes[0][0], "Throughput (görev/sn)", "linear"),
        ("wait", axes[0][1], "Kuyruk bekleme p95 (ms)", "linear"),
        ("loss", axes[1][0], "Kayıp oranı (%)  [dropped + rejected]", "linear"),
        ("lag", axes[1][1], "Üretici çizelge gecikmesi, ort. (ms, log)", "log"),
    ]
    for name, ax, ylabel, scale in panels:
        for policy, style in STYLE.items():
            series = data[(name, policy)]
            xs = sorted(series)
            stats = [mean_ci(series[x]) for x in xs]
            ax.errorbar(xs, [m for m, _ in stats], yerr=[c for _, c in stats],
                        capsize=3, linewidth=1.5, markersize=5, **style)
        ax.axvline(1.0, color="gray", linestyle=":", linewidth=1)
        ax.set_xlabel("Yük (kalibre kapasitenin katı)")
        ax.set_ylabel(ylabel)
        ax.set_yscale(scale)
        ax.grid(alpha=0.3)
        ax.legend()
    axes[0][0].axhline(cap, color="gray", linestyle="--", linewidth=1)
    axes[0][0].annotate(f"kalibre kapasite ≈ {cap:.0f}", (0.3, cap), textcoords="offset points",
                        xytext=(0, 5), fontsize=9, color="gray")

    fig.suptitle(f"Backpressure politikaları: 4 worker, kuyruk 50, görev 5 ms  "
                 f"(n={len(reps)} tekrar, hata çubuğu %95 GA; gecikme 6 sn'lik ölçüm penceresi üzerinden)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT, dpi=150)
    print(f"Yazıldı: {OUT}")


if __name__ == "__main__":
    main()