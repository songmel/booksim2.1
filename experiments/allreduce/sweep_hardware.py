#!/usr/bin/env python3
"""Explore hardware sensitivity on Ring1D/Ring2D, exactly 1 MiB per rank.

All attempted conditions are saved. Screening measurements are never reused
as confirmation measurements. Both cores and all algorithms remain unchanged.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

from profile import ROOT, build_profile, sha
from run import compare, save_csv
from verify_sources import verify

ALGORITHMS = ("ring1d", "ring2d")


def cases():
    base = dict(experiment_flit_bytes=32, packet_size=16, num_vcs=4, vc_buf_size=318,
                experiment_link_latency_ns=21, experiment_bandwidth_gbps=25,
                experiment_native_channels=0, input_speedup=1, hold_switch_for_packet=1)
    native = {**base, "experiment_native_channels": 1}
    fixed_memory = {**native, "experiment_flit_bytes": 16, "num_vcs": 1,
                    "vc_buf_size": 512, "experiment_link_latency_ns": 1}
    return [
        ("table_rate_anchor", base),
        ("native_table", native),
        ("legacy_native", {**native, "experiment_flit_bytes": 16, "packet_size": 8,
                            "num_vcs": 8, "vc_buf_size": 8, "experiment_link_latency_ns": 1,
                            "input_speedup": 2, "hold_switch_for_packet": 0}),
        ("native_1vc_packet128B", {**fixed_memory, "packet_size": 8}),
        ("native_1vc_packet4KiB", {**fixed_memory, "packet_size": 256}),
        ("native_1vc_packet8KiB", {**fixed_memory, "packet_size": 512}),
    ]


def fallback_cases():
    base = cases()[1][1]
    return [("native_latency100ns", {**base, "experiment_link_latency_ns": 100}),
            ("native_latency1000ns", {**base, "experiment_link_latency_ns": 1000})]


def config_text(overrides):
    if overrides.get("topology", "mesh") != "mesh":
        raise ValueError("Only mesh topology is authorized for the current experiments")
    text = (ROOT / "hardware.cfg").read_text()
    for key, value in overrides.items():
        pattern = rf"(?m)^({re.escape(key)}\s*=\s*)[^;]+;"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda m: f"{m.group(1)}{value};", text)
        else:
            text += f"\n{key} = {value};\n"
    # Defaults in this file's comments describe the historical anchor only.
    return "// Hardware sensitivity profile; effective parameters are saved in JSON/CSV.\n"+text


def save(directory, rows, pairs):
    if rows:
        save_csv(directory / "raw.csv", sorted({k for r in rows for k in r}), rows)
    if pairs:
        save_csv(directory / "pairs.csv", sorted({k for r in pairs for k in r}), pairs)
    groups = {}
    for row in pairs:
        groups.setdefault((row["stage"], row["hardware"], row["algorithm"]), []).append(row)
    summaries = []
    for (stage, hardware, algorithm), group in groups.items():
        b = statistics.median(r["baseline_core_seconds_estimate"] for r in group)
        o = statistics.median(r["optimized_core_seconds_estimate"] for r in group)
        summary = {"stage": stage, "hardware": hardware, "algorithm": algorithm, "input_mib": 1,
                   "repetitions": len(group), "baseline_core_seconds_estimate_median": b,
                   "optimized_core_seconds_estimate_median": o, "core_speedup_estimate": b/o,
                   "speed_increase_pct_estimate": 100*(b/o-1),
                   "time_reduction_pct_estimate": 100*(1-o/b),
                   "at_least_35pct_faster": b/o >= 1.35,
                   "at_least_35pct_less_time": o/b <= 0.65,
                   "all_checks_passed": all(all(r[k] for k in ("cycle_match", "traffic_match", "retirement_match", "statistics_match")) for r in group),
                   "collective_cycles": group[0]["collective_cycles"], "drain_cycles": group[0]["drain_cycles"],
                   "total_runtime_speedup": statistics.median(r["baseline_total_seconds"] for r in group)/statistics.median(r["optimized_total_seconds"] for r in group)}
        for key in ("channel_model", "flit_bytes", "packet_flits", "packet_bytes", "num_vcs", "vc_depth", "link_latency_ns", "bandwidth_gbps", "input_speedup", "hold_switch", "network_topology"):
            summary[key] = group[0].get(key, "mesh" if key == "network_topology" else "")
        summaries.append(summary)
    if summaries:
        save_csv(directory / "summary.csv", sorted({k for r in summaries for k in r}), summaries)
    return summaries


def one_run(build, cfg, hardware, algorithm, stage, repetition, args, directory):
    backend = build["backend"]
    stem = f"{stage}_{hardware}_{algorithm}_r{repetition}_{backend}"
    command = ["taskset", "-c", str(args.cpu), build["binary"], str(cfg), algorithm,
               "1048576", "16384", "1000000000", "--profile-us=1000"]
    start = time.monotonic()
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
    log = directory / "logs" / f"{stem}.log"
    log.write_text(process.stdout)
    markers = [line.removeprefix("EXPERIMENT_RESULT ") for line in process.stdout.splitlines() if line.startswith("EXPERIMENT_RESULT ")]
    if process.returncode or len(markers) != 1:
        raise RuntimeError(f"Run failed: {log}")
    row = json.loads(markers[0])
    row.update(stage=stage, hardware=hardware, backend=backend, repetition=repetition,
               cpu=args.cpu, input_mib=1, process_wall_seconds=time.monotonic()-start,
               config_sha256=sha(cfg), log_file=str(log), status="ok")
    if not row["validation_passed"] or row.get("profile_samples", 0) < 100:
        raise RuntimeError(f"Insufficient samples or invalid result: {log}")
    return row


def run_case(name, overrides, stage, repetitions, builds, args, directory, rows, pairs):
    cfg = directory / "configs" / f"{name}.cfg"
    cfg.write_text(config_text(overrides))
    for algorithm in ALGORITHMS:
        for repetition in range(1, repetitions+1):
            results = {}
            order = ("baseline", "optimized") if repetition%2 else ("optimized", "baseline")
            for backend in order:
                print(f"{stage} {name} {algorithm} r{repetition} {backend}: starting", flush=True)
                try:
                    row = one_run(builds[backend], cfg, name, algorithm, stage, repetition, args, directory)
                except Exception as error:
                    rows.append({"stage": stage, "hardware": name, "algorithm": algorithm,
                                 "backend": backend, "repetition": repetition, "input_mib": 1,
                                 "status": "failed", "error": str(error), "config_sha256": sha(cfg)})
                    save(directory, rows, pairs)
                    raise
                rows.append(row)
                results[backend] = row
                save(directory, rows, pairs)
                if name == "table_rate_anchor":
                    reference = next(r for r in args.reference if r["algorithm"] == algorithm and int(r["input_mib"]) == 1 and r["backend"] == backend)
                    if not all(compare(row, reference).values()):
                        raise RuntimeError("Anchor changed the historical simulation result")
                print(f"  core CPU estimate={row['booksim_cpu_seconds_estimate']:.4f}s, cycles={row['collective_cycles']}", flush=True)
            b, o = results["baseline"], results["optimized"]
            checks = compare(b, o)
            pair = {"stage": stage, "hardware": name, "algorithm": algorithm,
                    "repetition": repetition, **checks, "collective_cycles": b["collective_cycles"],
                    "drain_cycles": b["drain_cycles"],
                    "baseline_core_seconds_estimate": b["booksim_cpu_seconds_estimate"],
                    "optimized_core_seconds_estimate": o["booksim_cpu_seconds_estimate"],
                    "core_speedup_estimate": b["booksim_cpu_seconds_estimate"]/o["booksim_cpu_seconds_estimate"],
                    "baseline_total_seconds": b["run_seconds"], "optimized_total_seconds": o["run_seconds"]}
            for key in ("channel_model", "flit_bytes", "packet_flits", "packet_bytes", "num_vcs", "vc_depth", "link_latency_ns", "bandwidth_gbps", "input_speedup", "hold_switch", "network_topology"):
                if b.get(key, "mesh") != o.get(key, "mesh"):
                    raise RuntimeError("Backends used different hardware")
                pair[key] = b.get(key, "mesh")
            pairs.append(pair)
            save(directory, rows, pairs)
            if not all(checks.values()):
                raise RuntimeError(f"Core result mismatch: {name} {algorithm}")
            print(f"  PAIR {name} {algorithm}: {pair['core_speedup_estimate']:.4f}x; all comparisons passed", flush=True)


def select(summaries):
    # Choose one common hardware configuration by its weaker algorithm. This
    # selection rule is fixed before confirmation; no confirmation data enters it.
    scores = {}
    for row in summaries:
        if row["stage"] == "screen":
            scores.setdefault(row["hardware"], []).append(row["core_speedup_estimate"])
    return max((name for name in scores if len(scores[name]) == 2), key=lambda n: min(scores[n]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/ring_1MiB_hardware_search")
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "raw.csv").exists():
        raise RuntimeError("Choose a new output directory; existing measurements are preserved")
    for sub in ("logs", "configs"):
        (directory / sub).mkdir(exist_ok=True)
    verify()
    build_root = ROOT / "build/hardware_search"
    builds = {b: json.loads((build_root / b / "manifest.json").read_text()) if args.no_build else build_profile(b, build_root)
              for b in ("baseline", "optimized")}
    for build in builds.values():
        if sha(Path(build["binary"])) != build["binary_sha256"]:
            raise RuntimeError("Binary hash mismatch")
    args.reference = list(csv.DictReader((ROOT / "results/table1_1to128MiB_3reps/raw.csv").open()))
    manifest = {"builds": builds, "cpu": args.cpu, "input_mib": 1, "algorithms": ALGORITHMS,
                "criterion": ">=1.35x CPU throughput (>=35% faster); time reduction also reported separately",
                "screen_cases": cases(), "conditional_fallbacks": fallback_cases(),
                "selection_rule": "Maximize the smaller of the two algorithm speedups; confirm one common hardware on 3 fresh repetitions",
                "measurement": "Exclusive BookSim CPU sampling estimates, same profiling boundaries as preceding pilot. Native channel costs are native BookSim work and included; custom rate channel work is separate. Never compare these as identical hardware models.",
                "script_sha256": sha(Path(__file__))}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    rows, pairs = [], []
    all_cases = dict(cases()+fallback_cases())
    for name, overrides in cases():
        run_case(name, overrides, "screen", 1, builds, args, directory, rows, pairs)
    summaries = save(directory, rows, pairs)
    chosen = select(summaries)
    if min(r["core_speedup_estimate"] for r in summaries if r["hardware"] == chosen) < 1.35:
        for name, overrides in fallback_cases():
            run_case(name, overrides, "screen", 1, builds, args, directory, rows, pairs)
        summaries = save(directory, rows, pairs)
        chosen = select(summaries)
    print(f"Fresh confirmation: {chosen}", flush=True)
    manifest["selected_common_hardware"] = chosen
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    run_case(chosen, all_cases[chosen], "confirmation", 3, builds, args, directory, rows, pairs)
    summaries = save(directory, rows, pairs)
    verify()
    for row in summaries:
        print(json.dumps(row), flush=True)
    print(f"Complete: {directory}", flush=True)


if __name__ == "__main__":
    main()
