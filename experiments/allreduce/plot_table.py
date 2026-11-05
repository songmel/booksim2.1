#!/usr/bin/env python3
"""Plot the frozen-binary experiment on the supplied system table."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"results/table1_ring1d_alltoall_final_1MiB_3reps")
    args = parser.parse_args()
    directory = args.input.resolve()
    manifest = json.loads((directory/"manifest.json").read_text())
    if manifest["status"] != "complete" or not manifest["all_checks_passed"]:
        raise ValueError("Experiment is not complete")
    raw = list(csv.DictReader((directory/"raw.csv").open()))
    summaries = {r["algorithm"]:r for r in csv.DictReader((directory/"summary.csv").open())}
    algorithms = ("ring1d", "alltoall")
    cells = ("baseline_o3", "baseline_final", "optimized_final")
    labels = ("BookSim2\nO3", "BookSim2\nfinal flags", "BookSim-opt\nfinal flags")
    colors = ("#cbd5e1", "#64748b", "#2563eb")
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":11,
                         "axes.spines.top":False, "axes.spines.right":False,
                         "svg.fonttype":"none"})
    fig, axes = plt.subplots(1, 3, figsize=(16, 6.2))
    fig.subplots_adjust(left=.055, right=.98, top=.76, bottom=.27, wspace=.35)
    hardware = manifest["effective_config"]
    title = "Bandwidth / flit-size variation | frozen executables" if manifest.get("hardware_changes_from_table") else "Supplied system configuration | frozen executables"
    if manifest["input_mib"] != 1:
        title = "Input size scaling | frozen executables"
    fig.suptitle(title, x=.055, y=.96,
                 ha="left", fontsize=22, fontweight="bold")
    subtitle = (f"8×8 Mesh · 1 GHz · {hardware['experiment_bandwidth_gbps']} GB/s · "
                f"{hardware['experiment_link_latency_ns']} ns · 512 B packets · "
                f"{hardware['experiment_flit_bytes']} B flits · {hardware['num_vcs']} VCs × {hardware['vc_buf_size']} flits")
    fig.text(.055, .88, subtitle,
             fontsize=12, color="#475569")
    for axis, algorithm, title in zip(axes[:2], algorithms, ("Ring1D AllReduce", "AllToAll · direct exchange")):
        values = [float(summaries[algorithm][f"{cell}_booksim_cpu_seconds_estimate_median"]) for cell in cells]
        axis.bar(range(3), values, color=colors, width=.65)
        for x, (cell, value) in enumerate(zip(cells, values)):
            observed = [float(r["booksim_cpu_seconds_estimate"]) for r in raw
                        if r["algorithm"]==algorithm and r["variant"]==cell]
            axis.vlines(x, min(observed), max(observed), color="#0f172a", linewidth=1.5)
            axis.text(x, max(value, max(observed))+.025*max(values), f"{value:.3f}s", ha="center", fontsize=10)
        axis.set_xticks(range(3), labels, fontsize=10)
        axis.set_ylim(0, max(values)*1.2)
        axis.set_ylabel("BookSim CPU seconds (estimate)")
        axis.set_title(title, loc="left", fontweight="bold", pad=17)
    speedup_groups = ((-.18,"combined_upgrade_speedup","O3 baseline / final opt","#60a5fa"),
                      (.18,"matching_final_flags_speedup","Final baseline / final opt","#2563eb"))
    for offset, key, label, color in speedup_groups:
        values = [float(summaries[a][key]) for a in algorithms]
        axes[2].bar(np.arange(2)+offset, values, width=.32, color=color, label=label)
        for x, value in zip(np.arange(2)+offset, values):
            label_y = max(value+.025, 1.39) if abs(value-1.35)<.1 else value+.025
            axes[2].text(x, label_y, f"{value:.3f}×", ha="center", fontsize=10)
    axes[2].set_xticks(range(2), ("Ring1D", "AllToAll"))
    axes[2].set_ylim(0, max(1.35, *(float(summaries[a][key]) for a in algorithms
                                 for _,key,_,_ in speedup_groups))*1.3)
    axes[2].axhline(1.35, color="#64748b", linestyle="--", linewidth=1)
    axes[2].set_ylabel("Baseline / BookSim-opt")
    axes[2].set_title("Simulator speedup", loc="left", fontweight="bold", pad=17)
    axes[2].legend(frameon=False, fontsize=9, loc="upper left")
    for axis in axes:
        axis.grid(axis="y", color="#e2e8f0")
        axis.set_axisbelow(True)
    fig.text(.055,.14,f"{manifest['input_mib']} MiB/rank · medians of {manifest['repetitions']} fresh runs · ranges show observed runs · dashed line: 1.35×",fontsize=10,color="#475569")
    fig.text(.055,.09,"Final flags: native + LTO auto + no exceptions + PGO. Archived PGO retained; no hardware-specific retraining.",fontsize=10,color="#475569")
    fig.text(.055,.04,"CPU sampling estimates exclude collective control, custom rate adapter and output. Original simulator cores and Makefiles unchanged.",fontsize=10,color="#475569")
    output = directory/"plots"
    output.mkdir(exist_ok=True)
    for extension in ("png", "pdf", "svg"):
        fig.savefig(output/f"table_comparison.{extension}", dpi=210, facecolor="white")
    sha = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    (output/"metadata.json").write_text(json.dumps(dict(
        sources={n:sha(directory/n) for n in ("raw.csv","pairs.csv","summary.csv","manifest.json")},
        plot_script_sha256=sha(Path(__file__)), matplotlib=matplotlib.__version__), indent=2)+"\n")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    main()
