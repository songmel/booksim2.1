#!/usr/bin/env python3
"""Plot the current Ring1D/AllToAll pilot, preserving the measurement CSVs."""
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
LABELS = {"ring1d":"Ring1D AllReduce", "alltoall":"AllToAll\nDirect exchange"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"results/ring1d_alltoall_1MiB_mesh_3reps")
    args = parser.parse_args()
    directory = args.input.resolve()
    manifest = json.loads((directory/"manifest.json").read_text())
    if manifest["status"] != "complete" or manifest["algorithms"] != ["ring1d","alltoall"]:
        raise ValueError("Expected a completed Ring1D/AllToAll pilot")
    read = lambda name: list(csv.DictReader((directory/name).open()))
    summary, raw, pairs = read("summary.csv"), read("raw.csv"), read("pairs.csv")
    assert len(summary)==2 and all(r["all_checks_passed"]=="True" for r in summary)
    lookup = {r["algorithm"]:r for r in summary}
    algorithms = manifest["algorithms"]
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":12,
                         "axes.spines.top":False,"axes.spines.right":False,"svg.fonttype":"none"})
    fig, axes = plt.subplots(1,2,figsize=(12.5,6))
    fig.subplots_adjust(left=.075,right=.97,bottom=.27,top=.75,wspace=.35)
    fig.suptitle("Ring1D and AllToAll | 1 MiB per rank",x=.075,y=.96,ha="left",fontweight="bold",fontsize=21)
    native = manifest["hardware"].startswith("mesh_native")
    text = "8×8 mesh · 16 GB/s · 1 ns links · 1 VC · 512 B packets" if native else "8×8 mesh · table profile · 25 GB/s · 21 ns links · 4 VCs"
    fig.text(.075,.88,text,color="#475569",fontsize=12)
    xs = np.arange(2)
    for offset,backend,color,label in ((-.18,"baseline","#94a3b8","BookSim 2.0"),(.18,"optimized","#2563eb","BookSim-opt")):
        values = [float(lookup[a][f"{backend}_core_seconds_estimate_median"]) for a in algorithms]
        bars = axes[0].bar(xs+offset,values,width=.32,color=color,label=label)
        for x,a,value in zip(xs+offset,algorithms,values):
            observed = [float(r["booksim_cpu_seconds_estimate"]) for r in raw if r["algorithm"]==a and r["backend"]==backend]
            axes[0].vlines(x,min(observed),max(observed),color="#0f172a",linewidth=1.2)
            axes[0].text(x,value+.3,f"{value:.2f}s",ha="center",va="bottom",fontsize=10)
    axes[0].set_ylabel("BookSim CPU seconds (estimate)")
    axes[0].set_xticks(xs,[LABELS[a] for a in algorithms])
    axes[0].set_title("Median of three runs",loc="left",fontweight="bold",pad=16)
    axes[0].set_ylim(0,max(float(r["baseline_core_seconds_estimate_median"]) for r in summary)*1.23)
    axes[0].legend(frameon=False,loc="upper left",ncol=2,fontsize=10)
    speedups = [float(lookup[a]["core_speedup_estimate"]) for a in algorithms]
    for x,a,value in zip(xs,algorithms,speedups):
        observed = [float(p["core_speedup_estimate"]) for p in pairs if p["algorithm"]==a]
        axes[1].vlines(x,min(observed),max(observed),color="#2563eb",linewidth=2)
        axes[1].hlines([min(observed),max(observed)],x-.06,x+.06,color="#2563eb",linewidth=2)
        axes[1].scatter(x,value,s=100,color="#2563eb",zorder=3)
        axes[1].annotate(f"{value:.3f}×",(x,value),xytext=(0,18),textcoords="offset points",ha="center",fontweight="bold")
        axes[1].text(x,1.025,f"{float(lookup[a]['time_reduction_pct_estimate']):.1f}% less CPU time",ha="center",fontsize=10,color="#475569")
    axes[1].set_xticks(xs,[LABELS[a] for a in algorithms])
    axes[1].set_ylabel("Baseline / optimized CPU time")
    axes[1].set_title("Speedup within each workload",loc="left",fontweight="bold",pad=16)
    axes[1].axhline(1,color="#94a3b8",linewidth=1)
    axes[1].set_ylim(.99,max(speedups)+.14)
    axes[1].set_xlim(-.5,1.5)
    for ax in axes:
        ax.grid(axis="y",color="#e2e8f0"); ax.set_axisbelow(True)
    fig.text(.075,.12,"CPU sampling estimates exclude collective control and output. Ranges show observed runs/pairs, not confidence intervals.",fontsize=10,color="#475569")
    fig.text(.075,.075,"AllToAll: 16 KiB per peer, 1008 KiB/rank on network; self copy is local. Different traffic volumes across collectives.",fontsize=10,color="#475569")
    if native:
        fig.text(.075,.03,"Exploratory mesh hardware; this is not the proposed calibrated data-center model.",fontsize=10,color="#475569")
    output = directory/"plots"
    output.mkdir(exist_ok=True)
    for suffix in ("png","pdf","svg"):
        fig.savefig(output/f"collective_speedups.{suffix}",dpi=200,facecolor="white")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    (output/"metadata.json").write_text(json.dumps({"sources":{name:sha(directory/name) for name in ("raw.csv","pairs.csv","summary.csv","manifest.json")},"script_sha256":sha(Path(__file__)),"matplotlib":matplotlib.__version__},indent=2)+"\n")
    plt.close(fig)
    print(output)


if __name__=="__main__":
    main()
