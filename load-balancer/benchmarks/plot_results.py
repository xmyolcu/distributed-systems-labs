"""sweep.csv'den yanıt süresi grafikleri. Hata çubuğu: 10 tohum üzerinden %95 GA."""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "sweep.csv"
T95 = {4: 2.776, 9: 2.262, 19: 2.093}

STYLE = {
    "rr": dict(color="#1f77b4", marker="o", label="Round Robin"),
    "lc": dict(color="#d62728", marker="s", label="Least Connections"),
    "p2c": dict(color="#2ca02c", marker="^", label="Power of Two Choices"),
}
RR_COLLAPSE = 2 / 3.5   # heterojende RR'nin teorik çöküş yükü (yavaş sunucu 1.75*rho = 1)


def mean_ci(xs):
    m = statistics.mean(xs)
    if len(xs) < 2:
        return m, 0.0
    return m, T95.get(len(xs) - 1, 1.96) * statistics.stdev(xs) / len(xs) ** 0.5


def main() -> None:
    rows = list(csv.DictReader(SRC.open()))
    data = defaultdict(list)
    for r in rows:
        key = (r["service"], r["scenario"], r["policy"], float(r["rho"]))
        data[key].append(r)

    for service in ("exp", "lognormal"):
        fig, axes = plt.subplots(2, 2, figsize=(12, 9), sharex=True)
        for row, scen in enumerate(("homojen", "heterojen")):
            for col, (metric, label) in enumerate((("resp_mean_ms", "Ortalama yanıt (ms, log)"),
                                                   ("resp_p99_ms", "p99 yanıt (ms, log)"))):
                ax = axes[row][col]
                for policy, style in STYLE.items():
                    rhos = sorted({k[3] for k in data if k[:3] == (service, scen, policy)})
                    stats = [mean_ci([float(r[metric]) for r in data[(service, scen, policy, rho)]])
                             for rho in rhos]
                    ax.errorbar(rhos, [m for m, _ in stats], yerr=[c for _, c in stats],
                                capsize=3, linewidth=1.5, markersize=5, **style)
                if scen == "heterojen":
                    ax.axvline(RR_COLLAPSE, color="gray", linestyle=":", linewidth=1)
                    ax.annotate("RR teorik çöküş ≈ 0.57", (RR_COLLAPSE, 0.95),
                                xycoords=("data", "axes fraction"), xytext=(4, 0),
                                textcoords="offset points", fontsize=8, color="gray")
                ax.set_yscale("log")
                ax.set_title(f"{scen} ({'4 x 1.0' if scen == 'homojen' else '3 x 1.0 + 1 x 0.5'} hız)",
                             fontsize=10)
                ax.set_ylabel(label)
                ax.grid(alpha=0.3, which="both")
                if row == 1:
                    ax.set_xlabel("Toplam yük ρ (sunulan / toplam kapasite)")
                ax.legend(fontsize=8)
        dist = "üstel" if service == "exp" else "lognormal (ağır kuyruk)"
        fig.suptitle(f"Yük dengeleme: 4 sunucu, ort. servis 10 ms, servis dağılımı {dist}  "
                     f"(n=10 tohum, %95 GA; çökmüş RR değerleri simülasyon süresine bağlıdır)",
                     fontsize=10)
        fig.tight_layout()
        out = ROOT / "results" / f"lb_response_{service}.png"
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"Yazıldı: {out}")


if __name__ == "__main__":
    main()