#!/usr/bin/env python3
"""Create report figures from the completed AllReduce benchmark CSVs.

Requires Matplotlib (and its NumPy dependency). Does not change benchmark data.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

os.environ.setdefault("MPLCONFIGDIR", "/tmp/booksim-matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import ListedColormap
from matplotlib.patches import FancyBboxPatch, Patch
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS = ROOT / "results/table1_1to128MiB_3reps"
ALGORITHMS = ("ring1d", "ring2d", "tto")
NAMES = {"ring1d": "1D Ring", "ring2d": "2D Ring", "tto": "TTO"}
COLORS = {"ring1d": "#2563EB", "ring2d": "#D97706", "tto": "#7C3AED"}
INK, MUTED, GRID = "#0F172A", "#64748B", "#E2E8F0"
BASELINE, OPTIMIZED = "#64748B", "#2563EB"
BACKGROUND, GREEN, RED = "#F5F7FB", "#15803D", "#B91C1C"


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def truth(value):
    return str(value).lower() == "true"


def load_data(directory):
    summary = read_csv(directory / "summary.csv")
    raw = read_csv(directory / "raw.csv")
    pairs = read_csv(directory / "pairs.csv")
    manifest = json.loads((directory / "manifest.json").read_text())
    sizes = sorted(manifest["sizes_mib"])
    repeats = manifest["repetitions"]
    expected = len(ALGORITHMS)*len(sizes)
    if len(summary) != expected or len(raw) != 2*repeats*expected or len(pairs) != repeats*expected:
        raise ValueError("The complete experiment matrix is required; CSV row counts do not match the manifest")
    rows = {}
    for row in summary:
        key = row["algorithm"], int(row["input_mib"])
        if key in rows:
            raise ValueError(f"Duplicate summary condition: {key}")
        rows[key] = row
    for algorithm in ALGORITHMS:
        for size in sizes:
            row = rows[algorithm, size]
            for backend, prefix in (("baseline", "baseline"), ("optimized", "optimized")):
                selected = [r for r in raw if r["algorithm"] == algorithm and int(r["input_mib"]) == size and r["backend"] == backend]
                if len(selected) != repeats or any(r["status"] != "ok" or not truth(r["validation_passed"]) for r in selected):
                    raise ValueError(f"Invalid raw measurements: {algorithm}, {size}, {backend}")
                values = [float(r["run_seconds"]) for r in selected]
                for statistic, value in (("median", statistics.median(values)), ("min", min(values)), ("max", max(values))):
                    if not math.isclose(float(row[f"{prefix}_{statistic}_seconds"]), value, rel_tol=1e-12):
                        raise ValueError("Summary runtime does not match raw measurements")
            ratio = float(row["baseline_median_seconds"])/float(row["optimized_median_seconds"])
            if not math.isclose(float(row["speedup"]), ratio, rel_tol=1e-12):
                raise ValueError("Summary speedup does not match runtime medians")
    return rows, pairs, sizes, repeats, len(raw)


def values(rows, algorithm, sizes, field):
    return np.array([float(rows[algorithm, size][field]) for size in sizes])


def style_axis(ax, xgrid=False):
    ax.set_facecolor("white")
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0, pad=8)
    ax.grid(axis="both" if xgrid else "y", color=GRID, linewidth=0.75)


def size_axis(ax, sizes):
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes, [str(size) for size in sizes])
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlim(sizes[0]*0.82, sizes[-1]*1.20)
    ax.set_xlabel("Input per contributing rank (MiB)", labelpad=12)


def plot_speedup(ax, rows, sizes, *, compact=False):
    style_axis(ax)
    size_axis(ax, sizes)
    ax.axhline(1.0, color="#94A3B8", linewidth=1.2, linestyle=(0, (4, 4)))
    ax.text(0.985, 0.036, "No speedup (1.0×)", transform=ax.transAxes,
            ha="right", color=MUTED, fontsize=9)
    for algorithm, marker in zip(ALGORITHMS, ("o", "s", "D")):
        speedup = values(rows, algorithm, sizes, "speedup")
        # Bounds use the extrema of all runs; this is not a confidence interval.
        low = values(rows, algorithm, sizes, "baseline_min_seconds")/values(rows, algorithm, sizes, "optimized_max_seconds")
        high = values(rows, algorithm, sizes, "baseline_max_seconds")/values(rows, algorithm, sizes, "optimized_min_seconds")
        ax.fill_between(sizes, low, high, color=COLORS[algorithm], alpha=0.09, linewidth=0)
        geomean = math.exp(float(np.mean(np.log(speedup))))
        label = NAMES[algorithm] if compact else f"{NAMES[algorithm]}  ·  geometric mean {geomean:.3f}×"
        ax.plot(sizes, speedup, color=COLORS[algorithm], linewidth=2.3,
                marker=marker, markersize=5.6, markeredgecolor="white", markeredgewidth=0.8,
                label=label, zorder=3)
    ax.set_ylim(0.99, 1.325)
    ax.set_yticks(np.arange(1.0, 1.301, 0.05))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f"{value:.2f}×"))
    ax.set_ylabel("Simulator speedup  ↑", labelpad=12)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 0.12), fontsize=9 if compact else 10,
              frameon=True, facecolor="white", edgecolor="white", framealpha=0.93)


def cycle_matrix(pairs, sizes, kind=None):
    matrix = np.zeros((len(ALGORITHMS), len(sizes)), dtype=np.int64)
    for ai, algorithm in enumerate(ALGORITHMS):
        for si, size in enumerate(sizes):
            selected = [p for p in pairs if p["algorithm"] == algorithm and int(p["input_mib"]) == size]
            fields = ("cycles", "drain_cycles") if kind is None else (kind,)
            matrix[ai, si] = max(abs(int(p[f"baseline_{field}"])-int(p[f"optimized_{field}"]))
                                 for p in selected for field in fields)
    return matrix


def heatmap(ax, matrix, sizes, *, xlabel=True):
    colors = ListedColormap(["#E7F5ED", "#FEE2E2"])
    ax.imshow(matrix != 0, cmap=colors, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(sizes)), [str(s) for s in sizes])
    ax.set_yticks(range(3), [NAMES[a] for a in ALGORITHMS])
    ax.tick_params(length=0, pad=8, colors=MUTED)
    ax.set_xticks(np.arange(-0.5, len(sizes), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 3, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=3)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for ai in range(3):
        for si in range(len(sizes)):
            ax.text(si, ai, f"{matrix[ai, si]:,}", ha="center", va="center",
                    color=GREEN if matrix[ai, si] == 0 else RED, weight="bold", fontsize=11)
    if xlabel:
        ax.set_xlabel("Input per contributing rank (MiB)", labelpad=10)


def overview(rows, pairs, sizes, repeats, run_count):
    fig = plt.figure(figsize=(16, 12.5), facecolor=BACKGROUND)
    fig.text(0.06, 0.951, "BookSim  ·  AllReduce performance", fontsize=27, weight="bold", color=INK)
    fig.text(0.06, 0.918, f"Original BookSim 2.0 vs BookSim-opt (OPT1–OPT5)   |   {run_count} runs   |   1–128 MiB   |   {repeats} repetitions per condition",
             fontsize=11, color=MUTED)
    for index, algorithm in enumerate(ALGORITHMS):
        x, y, width = 0.06+index*0.30, 0.79, 0.28
        card = FancyBboxPatch((x, y), width, 0.10, boxstyle="round,pad=0.009,rounding_size=0.012",
                              transform=fig.transFigure, linewidth=0.8, edgecolor=GRID, facecolor="white")
        fig.add_artist(card)
        speedups = values(rows, algorithm, sizes, "speedup")
        geomean = math.exp(float(np.mean(np.log(speedups))))
        fig.text(x+0.017, y+0.071, NAMES[algorithm], fontsize=12, weight="bold", color=COLORS[algorithm])
        fig.text(x+0.017, y+0.021, f"{geomean:.3f}×", fontsize=29, weight="bold", color=INK)
        fig.text(x+0.145, y+0.028, "geometric-mean\nspeedup across 8 sizes", fontsize=10, color=MUTED, linespacing=1.6)

    ax = fig.add_axes([0.075, 0.365, 0.535, 0.350])
    plot_speedup(ax, rows, sizes, compact=True)
    ax.set_title("Speedup across message sizes", loc="left", pad=20, fontsize=14, weight="bold")
    ax.text(0.0, 1.015, "Median runtime ratio; shaded range uses the min/max runs", transform=ax.transAxes,
            color=MUTED, fontsize=8.5, va="bottom")

    ax = fig.add_axes([0.72, 0.365, 0.23, 0.350])
    style_axis(ax)
    ax.grid(False)
    ax.grid(axis="x", color=GRID, linewidth=0.75)
    positions = [2.5, 1.4, 0.3]
    for algorithm, position in zip(ALGORITHMS, positions):
        row = rows[algorithm, max(sizes)]
        for prefix, offset, color in (("baseline", 0.145, BASELINE), ("optimized", -0.145, OPTIMIZED)):
            value = float(row[f"{prefix}_median_seconds"])
            low, high = float(row[f"{prefix}_min_seconds"]), float(row[f"{prefix}_max_seconds"])
            ax.barh(position+offset, value, height=0.235, color=color,
                    xerr=np.array([[value-low], [high-value]]),
                    error_kw={"ecolor": INK, "linewidth": 0.8, "capsize": 2}, zorder=3)
            ax.text(value+40, position+offset, f"{value:,.0f}s", ha="left", va="center", fontsize=9.5, color=INK)
    ax.set_yticks(positions, [NAMES[a] for a in ALGORITHMS])
    ax.set_ylim(-0.2, 3.0)
    ax.set_xlim(0, 2050)
    ax.set_xticks([0, 500, 1000, 1500, 2000], ["0", "500", "1,000", "1,500", "2,000"])
    ax.tick_params(axis="x", labelsize=8.5)
    ax.set_xlabel("Simulator execution time (s)  ↓", labelpad=12)
    ax.set_title("128 MiB runtime", loc="left", pad=38, fontsize=14, weight="bold")
    ax.legend(handles=[Patch(facecolor=BASELINE, label="BookSim 2.0"), Patch(facecolor=OPTIMIZED, label="BookSim-opt")],
              loc="lower left", bbox_to_anchor=(-0.01, 1.005), ncol=2, fontsize=8, frameon=False,
              handlelength=1.0, columnspacing=1.2)

    matrix = cycle_matrix(pairs, sizes)
    matched = sum(int(p["baseline_cycles"]) == int(p["optimized_cycles"]) and
                  int(p["baseline_drain_cycles"]) == int(p["optimized_drain_cycles"]) for p in pairs)
    fig.text(0.075, 0.277, f"Cycle accuracy   ·   {matched}/{len(pairs)} paired comparisons matched", fontsize=14, weight="bold", color=INK)
    fig.text(0.95, 0.278, f"max |Δ| = {int(matrix.max()):,} cycles", ha="right", fontsize=11, color=GREEN, weight="bold")
    ax = fig.add_axes([0.075, 0.117, 0.875, 0.13])
    heatmap(ax, matrix, sizes)
    fig.text(0.075, 0.061, "Cells show the largest absolute difference across collective completion, final credit drain, and all 3 repetitions.", fontsize=8.5, color=MUTED)
    fig.text(0.06, 0.035, "Timing: C++ TrafficManager::Run, including common callbacks/adapters; setup and final validation excluded.\n"
             "Compare baseline vs optimized within each workload: Ring1D/Ring2D use 64 contributors; TTO uses 63 and pads its final wave.",
             fontsize=8.5, color=MUTED, linespacing=1.7)
    return fig


def speedup_figure(rows, sizes, repeats):
    fig, ax = plt.subplots(figsize=(11.5, 6.7))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.82, bottom=0.19)
    plot_speedup(ax, rows, sizes)
    fig.text(0.10, 0.94, "BookSim-opt simulator speedup", fontsize=22, weight="bold", color=INK)
    fig.text(0.10, 0.887, f"BookSim 2.0 runtime / BookSim-opt runtime   ·   {repeats} repetitions   ·   OPT1–OPT5 enabled", fontsize=11, color=MUTED)
    fig.text(0.10, 0.055, "Points: ratio of median C++ execution times. Bands: baseline min / optimized max to baseline max / optimized min.\n"
             "Bands are observed run ranges, not confidence intervals. All input sizes are per contributing rank.", fontsize=9, color=MUTED, linespacing=1.6)
    return fig


def runtime_figure(rows, sizes, repeats):
    fig, axes = plt.subplots(1, 3, figsize=(16, 6.5), sharey=True)
    fig.subplots_adjust(left=0.075, right=0.975, top=0.73, bottom=0.22, wspace=0.15)
    for ax, algorithm in zip(axes, ALGORITHMS):
        style_axis(ax, xgrid=True)
        size_axis(ax, sizes)
        for prefix, label, color, marker, linestyle in (("baseline", "BookSim 2.0", BASELINE, "o", "--"),
                                                      ("optimized", "BookSim-opt", OPTIMIZED, "s", "-")):
            median = values(rows, algorithm, sizes, f"{prefix}_median_seconds")
            low = values(rows, algorithm, sizes, f"{prefix}_min_seconds")
            high = values(rows, algorithm, sizes, f"{prefix}_max_seconds")
            ax.errorbar(sizes, median, yerr=[median-low, high-median], color=color, marker=marker,
                        linestyle=linestyle, linewidth=2, markersize=5.5, capsize=3, elinewidth=1,
                        markeredgecolor="white", markeredgewidth=0.7, label=label)
        ax.set_yscale("log", base=2)
        ax.set_ylim(5.5, 2200)
        ticks = [8, 16, 32, 64, 128, 256, 512, 1024, 2048]
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f"{value:,.0f}"))
        ax.yaxis.set_minor_locator(NullLocator())
        ranks = int(rows[algorithm, sizes[0]]["active_ranks"])
        ax.set_title(f"{NAMES[algorithm]}  ·  {ranks} contributors", color=COLORS[algorithm], fontsize=13, weight="bold", pad=14)
        row = rows[algorithm, max(sizes)]
        ax.text(0.04, 0.95, f"128 MiB median\n{float(row['baseline_median_seconds']):,.1f}s → {float(row['optimized_median_seconds']):,.1f}s",
                transform=ax.transAxes, va="top", fontsize=9.5, color=INK,
                bbox={"boxstyle": "round,pad=0.5", "fc": "white", "ec": GRID, "alpha": 0.97})
    axes[0].set_ylabel("Simulator execution time (s, logarithmic)  ↓", labelpad=12)
    fig.text(0.075, 0.94, "Simulator execution time", fontsize=23, weight="bold", color=INK)
    fig.text(0.075, 0.884, f"Median of {repeats} runs; error bars show min–max. Both axes use logarithmic spacing.", fontsize=11, color=MUTED)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(0.975, 0.91), ncol=2, frameon=False, fontsize=10)
    fig.text(0.075, 0.07, "C++ TrafficManager::Run includes simulation, common callbacks/adapters, online validation, credit drain, and statistics output.\n"
             "Setup, final validation and Python launch overhead are excluded. Compare implementations within each workload; TTO has 63 contributors and padding.",
             fontsize=9, color=MUTED, linespacing=1.6)
    return fig


def accuracy_figure(pairs, sizes, repeats):
    fig, axes = plt.subplots(2, 1, figsize=(12, 7.7))
    fig.subplots_adjust(left=0.13, right=0.96, top=0.77, bottom=0.16, hspace=0.82)
    for ax, kind, title in zip(axes, ("cycles", "drain_cycles"), ("AllReduce data completion", "Completion including final credit drain")):
        matrix = cycle_matrix(pairs, sizes, kind)
        heatmap(ax, matrix, sizes)
        ax.set_title(title, loc="left", pad=15, fontsize=12, weight="bold")
    matching = sum(all(truth(p[field]) for field in ("cycle_match", "traffic_match", "retirement_match", "statistics_match")) for p in pairs)
    fig.text(0.08, 0.94, "Cycle accuracy against BookSim 2.0", fontsize=22, weight="bold", color=INK)
    fig.text(0.08, 0.884, f"{matching}/{len(pairs)} pairs match cycles, traffic, retirement checksums and statistics.", fontsize=11, color=GREEN)
    fig.text(0.08, 0.045, f"Each cell is max |optimized − baseline| in cycles over {repeats} repetitions. Zero means an exact match.\n"
             "These are timing-preservation checks for each workload, not comparisons of collective algorithm efficiency.", fontsize=9, color=MUTED, linespacing=1.6)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    output = (args.output or directory / "plots").resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows, pairs, sizes, repeats, run_count = load_data(directory)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK, "axes.titlecolor": INK, "figure.facecolor": "white",
                         "savefig.facecolor": "auto", "pdf.fonttype": 42, "ps.fonttype": 42,
                         "svg.fonttype": "none", "axes.unicode_minus": True})
    figures = [
        ("overview", overview(rows, pairs, sizes, repeats, run_count)),
        ("speedup", speedup_figure(rows, sizes, repeats)),
        ("runtime", runtime_figure(rows, sizes, repeats)),
        ("cycle_accuracy", accuracy_figure(pairs, sizes, repeats)),
    ]
    with PdfPages(output / "booksim_allreduce_report.pdf", metadata={"Title": "BookSim AllReduce performance and cycle accuracy"}) as pdf:
        for name, figure in figures:
            for extension in ("png", "pdf", "svg"):
                figure.savefig(output / f"{name}.{extension}", dpi=240)
            pdf.savefig(figure)
            plt.close(figure)
    summary = {}
    for algorithm in ALGORITHMS:
        speedups = values(rows, algorithm, sizes, "speedup")
        summary[algorithm] = {"geometric_mean_speedup": math.exp(float(np.mean(np.log(speedups)))),
                              "min_speedup": float(speedups.min()), "max_speedup": float(speedups.max())}
    provenance = {"source_files": {name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                   for name in ("raw.csv", "pairs.csv", "summary.csv", "manifest.json")},
                  "matplotlib": matplotlib.__version__, "numpy": np.__version__, "repetitions": repeats,
                  "input_sizes_mib": sizes, "runs": run_count, "performance": summary,
                  "plot_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "maximum_cycle_difference": int(cycle_matrix(pairs, sizes).max())}
    (output / "plot_metadata.json").write_text(json.dumps(provenance, indent=2)+"\n")
    print(f"Saved {len(figures)} figures as PNG/PDF/SVG and a combined PDF report to {output}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
