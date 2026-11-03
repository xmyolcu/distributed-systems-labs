"""Zaman serisi grafiği (tohum 0) ve 10 tohumluk özet tablo."""
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRIGGER = (20, 30)
SHOW = ["none", "immediate", "backoff", "budget", "none+drop",
        "immediate+drop+lifo", "immediate+breaker"]
COLORS = dict(zip(SHOW, ["#7f7f7f", "#d62728", "#ff7f0e", "#9467bd",
                         "#2ca02c", "#17becf", "#1f77b4"]))


def summary() -> None:
    rows = defaultdict(list)
    for r in csv.DictReader((ROOT / "results" / "summary.csv").open()):
        rows[r["config"]].append(r)
    print(f"{'konfigürasyon':<22}{'başarı':>9}{'çarpan':>8}{'toparlanan':>12}{'medyan topar.':>15}")
    for name, rs in rows.items():
        succ = statistics.mean(float(r["success_rate"]) for r in rs)
        amp = statistics.mean(float(r["amplification"]) for r in rs)
        rec = [float(r["recovery_s"]) for r in rs]
        ok = [x for x in rec if x != float("inf")]
        med = f"{statistics.median(ok):.0f} sn" if ok else "-"
        print(f"{name:<22}{succ:9.1%}{amp:8.2f}{len(ok):>8}/{len(rs):<3}{med:>15}")


def timeseries() -> None:
    data = defaultdict(lambda: defaultdict(list))
    for r in csv.DictReader((ROOT / "results" / "timeseries.csv").open()):
        for k in ("success", "sent", "queue_len"):
            data[r["config"]][k].append(int(r[k]))
    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    panels = [("success", "Goodput (başarılı istek / sn)", "linear"),
              ("sent", "B'ye giden deneme / sn", "linear"),
              ("queue_len", "B kuyruk uzunluğu (log)", "log")]
    for ax, (key, label, scale) in zip(axes, panels):
        for name in SHOW:
            ys = data[name][key]
            if scale == "log":
                ys = [max(y, 1) for y in ys]
            ax.plot(range(len(ys)), ys, label=name, color=COLORS[name], linewidth=1.4)
        ax.axvspan(*TRIGGER, color="orange", alpha=0.12)
        ax.set_ylabel(label)
        ax.set_yscale(scale)
        ax.grid(alpha=0.3)
    axes[1].axhline(400, color="black", linestyle="--", linewidth=1)
    axes[1].annotate("B kapasitesi (400/sn)", (1, 400), xytext=(0, 4),
                     textcoords="offset points", fontsize=8)
    axes[0].legend(fontsize=8, ncol=4, loc="lower left")
    axes[-1].set_xlabel("Zaman (sn); turuncu bant: tetikleyici (B 2x yavaş)")
    fig.suptitle("Retry ve kısa bir yavaşlama: toparlanma mı, metastabil çöküş mü? "
                 "(yük %80, timeout 100 ms, tohum 0)", fontsize=11)
    fig.tight_layout()
    out = ROOT / "results" / "retry_timeseries.png"
    fig.savefig(out, dpi=150)
    print(f"Yazıldı: {out}")


if __name__ == "__main__":
    summary()
    timeseries()