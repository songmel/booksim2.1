#!/usr/bin/env python3
"""Plot matched compiler-option comparisons for Ring1D and AllToAll."""
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

ROOT=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=ROOT/"results/ring1d_alltoall_native_lto_pgo_1MiB_3reps")
    args=parser.parse_args()
    directory=args.input.resolve()
    manifest=json.loads((directory/"manifest.json").read_text())
    if manifest["status"]!="complete" or not manifest["all_checks_passed"]:
        raise ValueError("Measurement is not complete")
    with (directory/"compiler_comparison.csv").open() as f:rows=list(csv.DictReader(f))
    with (directory/"raw.csv").open() as f:raw=list(csv.DictReader(f))
    lookup={r["algorithm"]:r for r in rows}
    no_exceptions="variants" in manifest and "noexceptions" in manifest["variants"]["new"]
    algorithms=("ring1d","alltoall")
    labels={"ring1d":"Ring1D AllReduce","alltoall":"AllToAll · direct exchange"}
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"axes.spines.top":False,
                         "axes.spines.right":False,"svg.fonttype":"none"})
    fig,axes=plt.subplots(1,3,figsize=(17,6.3),gridspec_kw={"width_ratios":[1,1,1.12]})
    fig.subplots_adjust(left=.055,right=.98,top=.77,bottom=.27,wspace=.34)
    title="LTO auto + no exceptions | native + PGO retained" if no_exceptions else "Native + LTO + PGO | identical BookSim sources"
    fig.suptitle(title,x=.055,y=.96,ha="left",fontsize=23,fontweight="bold")
    fig.text(.055,.88,"64 ranks · 1 MiB/rank · unchanged exploratory mesh hardware · three fresh runs per cell",fontsize=12,color="#475569")
    columns=("o3_baseline_cpu_seconds","o3_optimized_cpu_seconds","compiled_baseline_cpu_seconds","compiled_optimized_cpu_seconds")
    cell_labels=("Baseline\nO3","Opt\nO3","Baseline\n3 options","Opt\n3 options")
    cells=(("o3","baseline"),("o3","optimized"),("native_lto_pgo","baseline"),("native_lto_pgo","optimized"))
    if no_exceptions:
        columns=("previous_baseline_cpu_seconds","previous_optimized_cpu_seconds","new_baseline_cpu_seconds","new_optimized_cpu_seconds")
        cell_labels=("Baseline\nprevious","Opt\nprevious","Baseline\nnew flags","Opt\nnew flags")
        cells=((manifest["variants"]["control"],"baseline"),(manifest["variants"]["control"],"optimized"),
               (manifest["variants"]["new"],"baseline"),(manifest["variants"]["new"],"optimized"))
    colors=("#cbd5e1","#93c5fd","#64748b","#2563eb")
    for axis,algorithm in zip(axes[:2],algorithms):
        values=[float(lookup[algorithm][k]) for k in columns]
        axis.bar(range(4),values,color=colors,width=.65)
        for x,(value,(variant,backend)) in enumerate(zip(values,cells)):
            observed=[float(r["booksim_cpu_seconds_estimate"]) for r in raw if r["algorithm"]==algorithm and r["variant"]==variant and r["backend"]==backend]
            axis.vlines(x,min(observed),max(observed),color="#0f172a",linewidth=1)
            axis.text(x,value+.025*max(values),f"{value:.2f}s",ha="center",fontsize=10)
        axis.axvline(1.5,color="#cbd5e1",linewidth=1)
        axis.set_xticks(range(4),cell_labels,fontsize=10)
        axis.set_title(labels[algorithm],loc="left",fontweight="bold",pad=17)
        axis.set_ylabel("BookSim CPU seconds (estimate)")
        axis.set_ylim(0,max(values)*1.18)
    comparisons=((-.18,"previous_backend_speedup","Previous flags for both","#94a3b8"),(.18,"new_backend_speedup","New flags for both","#2563eb")) if no_exceptions else ((-.18,"o3_backend_speedup","O3 for both","#94a3b8"),(.18,"compiled_backend_speedup","3 options for both","#2563eb"))
    for offset,key,label,color in comparisons:
        values=[float(lookup[a][key]) for a in algorithms]
        axes[2].bar(np.arange(2)+offset,values,width=.32,color=color,label=label)
        for x,v in zip(np.arange(2)+offset,values):axes[2].text(x,v+.025,f"{v:.3f}×",ha="center",fontsize=10)
    axes[2].set_xticks(range(2),["Ring1D","AllToAll"])
    axes[2].set_ylabel("Baseline / opt with matching compiler options")
    axes[2].set_title("Code optimization speedup",loc="left",fontweight="bold",pad=17)
    axes[2].legend(frameon=False,fontsize=9,loc="upper left")
    axes[2].axhline(1.35,color="#64748b",linestyle="--",linewidth=1)
    axes[2].set_ylim(0,max(float(r[item[1]]) for r in rows for item in comparisons)*1.3)
    for axis in axes:axis.grid(axis="y",color="#e2e8f0");axis.set_axisbelow(True)
    if no_exceptions:
        fig.text(.055,.13,"Native + PGO retained for both backends. Previous: -flto=4, exceptions enabled. New: -flto=auto -fno-exceptions.",fontsize=10,color="#475569")
        fig.text(.055,.08,"PGO training excluded. Experiment error handling adapted for disabled exceptions; successful workload and validation retained.",fontsize=10,color="#475569")
        fig.text(.055,.03,"CPU sampling estimates; medians of three runs. Dashed line: 1.35×. Same exploratory mesh; no held-out workload claim.",fontsize=10,color="#475569")
    else:
        fig.text(.055,.13,"3 options: -march=native + LTO + trained PGO, applied to both code versions. Bars are medians; ranges are observed runs.",fontsize=10,color="#475569")
        fig.text(.055,.08,"PGO training is excluded. Each backend uses a mixed Ring1D/AllToAll profile at the evaluation size; no held-out workload claim.",fontsize=10,color="#475569")
        fig.text(.055,.03,"CPU times are sampling estimates excluding collective control/output. Dashed line: 1.35×. Hardware remains the exploratory mesh model.",fontsize=10,color="#475569")
    out=directory/"plots"
    out.mkdir(exist_ok=True)
    for extension in ("png","pdf","svg"):fig.savefig(out/f"compiler_comparison.{extension}",dpi=210,facecolor="white")
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    (out/"metadata.json").write_text(json.dumps({"sources":{n:sha(directory/n) for n in ("compiler_comparison.csv","raw.csv","summary.csv","manifest.json")},"plot_script_sha256":sha(Path(__file__)),"matplotlib":matplotlib.__version__},indent=2)+"\n")
    plt.close(fig)
    print(out)


if __name__=="__main__":main()
