#!/usr/bin/env python3
"""Validate 1 MiB workloads and estimate exclusive CPU times without editing cores.

Reuses the exact immutable core objects used by the existing sweep. External
experiment and schedule units are compiled and linked in a new directory.
The core-time values are statistical estimates; raw counts are preserved.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time

from run import compare, save_csv, truth
from verify_sources import verify

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "results/table1_1to128MiB_3reps"
ALGORITHMS = ("ring1d", "alltoall")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_profile(backend, output_root=None):
    previous = ROOT / "build" / backend
    manifest = json.loads((previous / "manifest.json").read_text())
    if sha(previous / "collective") != manifest["binary_sha256"]:
        raise RuntimeError(f"Legacy control binary changed: {backend}")
    source = previous / "source/src"
    for name, expected in manifest["core_files_sha256"].items():
        if sha(previous / "source" / name) != expected:
            raise RuntimeError(f"Immutable core snapshot changed: {backend}: {name}")
    # External schedules share schedule.hpp, so rebuild every external unit.
    # Native core objects include no external experiment headers and stay exact.
    directory = (Path(output_root) if output_root else ROOT / "build/collective_profile") / backend
    directory.mkdir(parents=True, exist_ok=True)
    includes = [f"-I{source / p}" for p in (".", "arbiters", "allocators", "routers", "networks", "power")]
    includes.append(f"-I{ROOT / 'src'}")
    binary = directory / "collective"
    external_sources = sorted((ROOT / "src").glob("*.cpp"))
    external_stems = {p.stem for p in external_sources}
    objects = sorted(p for p in (previous / "objects").glob("*.o")
                     if not any(p.name.endswith(f"_{stem}.o") for stem in external_stems))
    external_objects = [directory / f"{p.stem}.o" for p in external_sources]
    commands = [["g++", *manifest["flags"], *includes, "-c", str(src), "-o", str(obj)]
                for src, obj in zip(external_sources, external_objects)]
    commands.append(["g++", *map(str, objects), *map(str, external_objects), "-o", str(binary)])
    for index, command in enumerate(commands):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (directory / f"build_{index}.log").write_text(result.stdout)
        if result.returncode:
            raise RuntimeError(f"Profile build failed; see {directory / f'build_{index}.log'}")
    archive = directory / "experiment_sources"
    archive.mkdir(exist_ok=True)
    for path in sorted((ROOT / "src").glob("*")):
        if path.is_file():
            (archive / path.name).write_bytes(path.read_bytes())
    provenance = {"backend": backend, "commit": manifest["commit"], "flags": manifest["flags"],
                  "legacy_binary": str(previous / "collective"),
                  "legacy_binary_sha256": manifest["binary_sha256"],
                  "binary": str(binary), "binary_sha256": sha(binary), "commands": commands,
                  "core_files_sha256": manifest["core_files_sha256"],
                  "reused_objects_sha256": {str(p): sha(p) for p in objects},
                  "experiment_sources": {p.name: sha(p) for p in sorted((ROOT / "src").glob("*")) if p.is_file()}}
    (directory / "manifest.json").write_text(json.dumps(provenance, indent=2)+"\n")
    print(f"{backend}: linked unchanged core objects into {binary}", flush=True)
    return provenance


def wilson(successes, trials):
    # Sampling uncertainty only: correlation, phase-tag cost and system drift
    # are not captured by this interval. Do not call it total measurement error.
    if trials <= 0:
        raise ValueError("No CPU samples")
    z = 1.959963984540054
    p = successes/trials
    denominator = 1+z*z/trials
    center = (p+z*z/(2*trials))/denominator
    radius = z*math.sqrt(p*(1-p)/trials+z*z/(4*trials*trials))/denominator
    return max(0, center-radius), min(1, center+radius)


def run_one(build, algorithm, repetition, mode, args, directory):
    stem = f"{algorithm}_1MiB_r{repetition}_{build['backend']}_{mode}"
    command = [build["binary"], str(ROOT / "hardware.cfg"), algorithm, "1048576", "16384", "1000000000"]
    if mode == "legacy_control":
        command[0] = build["legacy_binary"]
    if mode == "sampled":
        command.append(f"--profile-us={args.period_us}")
    started = time.monotonic()
    result = subprocess.run(["taskset", "-c", str(args.cpu), *command], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    wall = time.monotonic()-started
    log = directory / "logs" / f"{stem}.log"
    log.write_text(result.stdout)
    records = [line.removeprefix("EXPERIMENT_RESULT ") for line in result.stdout.splitlines()
               if line.startswith("EXPERIMENT_RESULT ")]
    if result.returncode or len(records) != 1:
        raise RuntimeError(f"Run failed: {log}")
    record = json.loads(records[0])
    if not record["validation_passed"]:
        raise RuntimeError(f"Validation failed: {log}")
    record.update(backend=build["backend"], repetition=repetition, mode=mode, input_mib=1,
                  process_wall_seconds=wall, log_file=str(log), cpu=args.cpu)
    return record


def persist(directory, rows):
    fields = sorted({key for row in rows for key in row})
    save_csv(directory / "raw.csv", fields, rows)


def summarize(directory, rows, repetitions):
    pairs = []
    summaries = []
    for algorithm in ALGORITHMS:
        sampled = [r for r in rows if r["algorithm"] == algorithm and r["mode"] == "sampled"]
        if len(sampled) != 2*repetitions:
            continue
        for repetition in range(1, repetitions+1):
            b = next(r for r in sampled if r["backend"] == "baseline" and r["repetition"] == repetition)
            o = next(r for r in sampled if r["backend"] == "optimized" and r["repetition"] == repetition)
            pairs.append({"algorithm": algorithm, "repetition": repetition, **compare(b, o),
                          "baseline_booksim_cpu_seconds_estimate": b["booksim_cpu_seconds_estimate"],
                          "optimized_booksim_cpu_seconds_estimate": o["booksim_cpu_seconds_estimate"],
                          "booksim_speedup_estimate": b["booksim_cpu_seconds_estimate"]/o["booksim_cpu_seconds_estimate"]})
        summary = {"algorithm": algorithm, "input_mib": 1, "repetitions": repetitions,
                   "timing_method": "CPU sampling estimate", "collective_cycles": sampled[0]["collective_cycles"],
                   "drain_cycles": sampled[0]["drain_cycles"],
                   "all_checks_passed": all(all(compare(sampled[0], r).values()) and r["validation_passed"]
                                            and r.get("reference_match") is not False for r in sampled)}
        for backend in ("baseline", "optimized"):
            chosen = [r for r in sampled if r["backend"] == backend]
            counts = sum(r["profile_samples"] for r in chosen)
            native = sum(r["booksim_samples"] for r in chosen)
            cpu = sum(r["profile_cpu_seconds"] for r in chosen)
            low, high = wilson(native, counts)
            for field in ("booksim_cpu_seconds_estimate", "allreduce_cpu_seconds_estimate",
                          "link_model_cpu_seconds_estimate", "output_cpu_seconds_estimate", "run_seconds"):
                summary[f"{backend}_{field}_median"] = statistics.median(r[field] for r in chosen)
            summary[f"{backend}_booksim_fraction"] = native/counts
            summary[f"{backend}_booksim_fraction_sampling_low95"] = low
            summary[f"{backend}_booksim_fraction_sampling_high95"] = high
            summary[f"{backend}_profile_samples"] = counts
            summary[f"{backend}_observed_cpu_us_per_sample"] = cpu*1e6/counts
            # Bounds describe the uncertainty of the pooled CPU-time estimate
            # (per-run mean), not the median or a full speedup confidence bound.
            summary[f"{backend}_booksim_mean_sampling_low95_seconds"] = cpu/repetitions*low
            summary[f"{backend}_booksim_mean_sampling_high95_seconds"] = cpu/repetitions*high
            controls = [r for r in rows if r["algorithm"] == algorithm and r["backend"] == backend and r["mode"] == "control"]
            if controls:
                control = statistics.median(r["run_seconds"] for r in controls)
                summary[f"{backend}_control_run_seconds_median"] = control
                summary[f"{backend}_sampler_wall_overhead_pct"] = 100*(summary[f"{backend}_run_seconds_median"]/control-1)
            legacy = [r for r in rows if r["algorithm"] == algorithm and r["backend"] == backend and r["mode"] == "legacy_control"]
            if legacy:
                control = statistics.median(r["run_seconds"] for r in legacy)
                summary[f"{backend}_legacy_run_seconds"] = control
                summary[f"{backend}_profile_vs_legacy_wall_change_pct"] = 100*(summary[f"{backend}_run_seconds_median"]/control-1)
        b = summary["baseline_booksim_cpu_seconds_estimate_median"]
        o = summary["optimized_booksim_cpu_seconds_estimate_median"]
        summary["booksim_speedup_estimate"] = b/o
        summary["booksim_time_reduction_pct_estimate"] = 100*(1-o/b)
        summary["total_run_speedup_sampled"] = summary["baseline_run_seconds_median"]/summary["optimized_run_seconds_median"]
        summaries.append(summary)
    if pairs:
        save_csv(directory / "pairs.csv", list(pairs[0]), pairs)
    if summaries:
        save_csv(directory / "summary.csv", sorted({k for row in summaries for k in row}), summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/ring1d_alltoall_1MiB_phase_profile_3reps")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--period-us", type=int, default=1000)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    if args.repetitions < 1 or args.period_us < 1000 or args.cpu not in os.sched_getaffinity(0):
        parser.error("Invalid repetition count, sampling period or CPU")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "raw.csv").exists():
        raise RuntimeError("Choose a new output directory; this pilot never overwrites measurements")
    (directory / "logs").mkdir(exist_ok=True)
    verify()
    builds = {b: build_profile(b) for b in ("baseline", "optimized")}
    reference = list(csv.DictReader((REFERENCE / "raw.csv").open()))
    manifest = {"builds": builds, "cpu": args.cpu, "period_us": args.period_us,
                "repetitions": args.repetitions, "input_mib": 1,
                "hardware_sha256": sha(ROOT / "hardware.cfg"), "script_sha256": sha(Path(__file__)),
                "reference_raw_sha256": sha(REFERENCE / "raw.csv"),
                "method": "ITIMER_PROF; exclusive execution-domain samples times measured process CPU time",
                "limitations": "Sampling estimates, not exact phase timers. Wilson intervals cover counting uncertainty only. Requested period may be rounded by the kernel; observed CPU us/sample is saved. Phase tags and signal handling have residual overhead; off/on and original-binary controls are saved.",
                "core_scope": "Native simulation, statistics accounting and native-equivalent flit generation/registration. Excludes AllReduce control/validation, external channel code and DisplayStats/DisplayOverallStats.",
                "model": "Original source/objects unchanged; identical external 25 GB/s channel model retained in both backends."}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    rows = []
    # Each condition has an off/on control from this exact binary. Both tags
    # and the sampler are disabled in control mode; backends alternate order.
    for algorithm in ALGORITHMS:
        for repetition in range(1, args.repetitions+1):
            order = ("baseline", "optimized") if repetition%2 else ("optimized", "baseline")
            for backend in order:
                modes = (("legacy_control", "control", "sampled") if algorithm == "ring1d" else ("control", "sampled")) if repetition == 1 else ("sampled",)
                for mode in modes:
                    print(f"{algorithm} {backend} r{repetition} {mode}: starting", flush=True)
                    row = run_one(builds[backend], algorithm, repetition, mode, args, directory)
                    old = next((r for r in reference if r["algorithm"] == algorithm and int(r["input_mib"]) == 1 and r["backend"] == backend), None)
                    row["reference_available"] = old is not None
                    row["reference_match"] = all(compare(row, old).values()) if old else None
                    rows.append(row)
                    persist(directory, rows)
                    if row["reference_match"] is False:
                        raise RuntimeError(f"Reference mismatch: {row['log_file']}")
                    if mode == "sampled" and row.get("profile_samples", 0) < 100:
                        raise RuntimeError("Insufficient CPU samples")
                    print(f"  {row['run_seconds']:.6f}s, {row['collective_cycles']} cycles; validation passed", flush=True)
                    summarize(directory, rows, args.repetitions)
    summaries = summarize(directory, rows, args.repetitions)
    if len(summaries) != len(ALGORITHMS) or not all(s["all_checks_passed"] for s in summaries):
        raise RuntimeError("Incomplete or mismatching pilot")
    verify()
    for summary in summaries:
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
