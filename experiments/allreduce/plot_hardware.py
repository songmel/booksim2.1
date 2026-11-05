#!/usr/bin/env python3
"""Plot all mesh screening cases and fresh confirmations; exclude cancelled runs."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/booksim-mpl")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
COLORS = {"ring1d":"#2563eb", "ring2d":"#ea580c"}
ALGORITHM_LABELS = {"ring1d":"Ring1D", "ring2d":"Ring2D"}
LABELS = {"table_rate_anchor":"Table\n25 GB/s", "native_table":"Native\n32 GB/s", "legacy_native":"Legacy\n16 GB/s",
          "native_1vc_packet128B":"128 B\n1 VC", "native_1vc_packet4KiB":"4 KiB\n1 VC", "native_1vc_packet8KiB":"8 KiB\n1 VC",
          "native_latency100ns":"100 ns\n4 VCs", "native_latency1000ns":"1000 ns\n4 VCs",
          "native_1vc_packet256B":"256 B\n1 VC", "native_1vc_packet512B":"512 B\n1 VC", "native_1vc_packet1024B":"1 KiB\n1 VC"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/mesh_hardware_comparison")
    args = parser.parse_args()
    source_dirs = [ROOT / "results/ring_1MiB_hardware_search", ROOT / "results/ring_1MiB_mesh_refinement"]
    summaries, pairs, sources = [], [], {}
    for directory in source_dirs:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("excluded_from_analysis") or manifest.get("topology", "mesh") != "mesh":
            raise ValueError("Only authorized mesh results may enter this figure")
        for name in ("summary.csv", "pairs.csv"):
            path = directory / name
            sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            with path.open() as f:
                target = summaries if name == "summary.csv" else pairs
                target.extend({**r, "dataset":directory.name} for r in csv.DictReader(f))
    assert all(r["all_checks_passed"] == "True" and r["input_mib"] == "1" for r in summaries)
    assert all(r["algorithm"] in COLORS for r in summaries)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in (("summary.csv", summaries), ("pairs.csv", pairs)):
        with (args.output / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
            writer.writeheader(); writer.writerows(rows)
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":11, "axes.spines.top":False,
                         "axes.spines.right":False, "axes.titleweight":"bold", "svg.fonttype":"none"})
    fig, axes = plt.subplots(1, 2, figsize=(17, 6.6), gridspec_kw={"width_ratios":[1.65,1]})
    fig.subplots_adjust(left=.06, right=.98, top=.82, bottom=.22, wspace=.23)
    fig.suptitle("Mesh hardware sensitivity | 1 MiB per rank", x=.06, y=.97, ha="left", fontsize=23, fontweight="bold")
    fig.text(.06,.90,"Unchanged BookSim cores · Ring1D / Ring2D · exclusive CPU sampling estimates", color="#475569", fontsize=12)
    screens = [r for r in summaries if r["stage"] == "screen"]
    names = list(dict.fromkeys(r["hardware"] for r in screens))
    for offset, algorithm in ((-.13,"ring1d"),(.13,"ring2d")):
        lookup = {r["hardware"]:float(r["core_speedup_estimate"]) for r in screens if r["algorithm"] == algorithm}
        xs = [i+offset for i,n in enumerate(names) if n in lookup]
        ys = [lookup[n] for n in names if n in lookup]
        axes[0].scatter(xs,ys,s=70,color=COLORS[algorithm],label=ALGORITHM_LABELS[algorithm],zorder=3)
    axes[0].set_xticks(range(len(names)), [LABELS.get(n,n) for n in names], fontsize=9)
    axes[0].set_title("All explored settings — one pair per point", loc="left", pad=18)
    axes[0].set_ylabel("BookSim CPU speedup (baseline / optimized)")
    axes[0].set_xlim(-.6,len(names)-.4)
    axes[0].legend(loc="upper right",frameon=False,ncol=2)
    if len(names)>8:
        axes[0].axvline(7.5,color="#cbd5e1",linewidth=1)
    confirmations = [r for r in summaries if r["stage"] == "confirmation"]
    assert all(int(r["repetitions"]) == 3 for r in confirmations)
    labels=[]
    for i,row in enumerate(confirmations):
        selected=[float(p["core_speedup_estimate"]) for p in pairs if p["stage"] == "confirmation" and p["hardware"] == row["hardware"] and p["algorithm"] == row["algorithm"]]
        value=float(row["core_speedup_estimate"])
        axes[1].errorbar(i,value,yerr=[[value-min(selected)],[max(selected)-value]],fmt="o",markersize=9,
                         color=COLORS[row["algorithm"]],capsize=5,linewidth=2,zorder=3)
        axes[1].annotate(f"{value:.3f}×",(i,value),xytext=(0,15),textcoords="offset points",ha="center",fontweight="bold")
        labels.append(f"{row['packet_bytes']} B\n{ALGORITHM_LABELS[row['algorithm']]}")
    axes[1].set_xticks(range(len(labels)),labels)
    axes[1].set_xlim(-.5,len(labels)-.5)
    axes[1].set_title("Fresh confirmation — three repetitions",loc="left",pad=18)
    for ax in axes:
        ax.axhline(1.35,color="#64748b",linestyle="--",linewidth=1.2,zorder=1)
        ax.axhline(1,color="#cbd5e1",linewidth=1)
        ax.set_ylim(.99,max(1.55,max(float(r["core_speedup_estimate"]) for r in summaries)+.10))
        ax.grid(axis="y",color="#e2e8f0");ax.set_axisbelow(True)
    axes[1].text(.02,.03,"Dashed line: +35% speed (1.35×)",transform=axes[1].transAxes,color="#475569",fontsize=10)
    fig.text(.06,.105,"Screening and confirmation are separate. Missing Ring1D refinement points were not screened. Error bars show observed paired min/max, not confidence intervals.",fontsize=10,color="#475569")
    fig.text(.06,.055,"Native link costs are included; custom rate-link code is excluded. Each point compares identical hardware within the baseline/optimized pair. Torus data are excluded.",fontsize=10,color="#475569")
    for suffix in ("png","pdf","svg"):
        fig.savefig(args.output/f"hardware_speedups.{suffix}",dpi=220,facecolor="white")
    (args.output/"plot_metadata.json").write_text(json.dumps({"source_files":sources,"script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"matplotlib":matplotlib.__version__,"numpy":np.__version__,"torus_excluded":True},indent=2)+"\n")
    plt.close(fig)
    print(args.output)


if __name__ == "__main__":
    main()
