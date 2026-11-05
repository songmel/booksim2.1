#!/usr/bin/env python3
"""Build and compare collective workloads from this checkout, without Git or caches."""
import argparse
import datetime
import itertools
import os
from pathlib import Path
import shutil
import statistics

from repro_build import VARIANTS, build
from repro_common import COLLECTIVES, ROOT, check_equal, execute, save_csv, sha, write_json
from verify_layout import verify

COMPARISONS = (("baseline_o3", "optimized_o3"), ("baseline_final", "opt1to6_final"),
               ("baseline_o3", "optimized_native"),
               ("opt1to5_final", "opt1to6_final"), ("baseline_o3", "opt1to6_final"),
               ("baseline_o3", "baseline_final"))


def summarize(rows):
    pairs, summaries = [], []
    workloads = sorted({(r["algorithm"], r["input_mib"]) for r in rows})
    for algorithm, mib in workloads:
        selected = [r for r in rows if (r["algorithm"], r["input_mib"]) == (algorithm, mib)]
        variants = {r["variant"] for r in selected}
        for base, optimized in COMPARISONS:
            if not {base, optimized} <= variants:
                continue
            b = [r for r in selected if r["variant"] == base]
            o = [r for r in selected if r["variant"] == optimized]
            if {r["repetition"] for r in b} != {r["repetition"] for r in o}:
                continue
            for left, right in zip(sorted(b, key=lambda r:r["repetition"]), sorted(o, key=lambda r:r["repetition"])):
                check_equal(left, right)
                pairs.append(dict(algorithm=algorithm, input_mib=mib, repetition=left["repetition"],
                                  baseline=base, optimized=optimized, all_checks_passed=True,
                                  baseline_seconds=left["run_seconds"], optimized_seconds=right["run_seconds"],
                                  speedup=left["run_seconds"]/right["run_seconds"]))
            previous = statistics.median(r["run_seconds"] for r in b)
            current = statistics.median(r["run_seconds"] for r in o)
            summaries.append(dict(algorithm=algorithm, input_mib=mib, baseline=base, optimized=optimized,
                                  repetitions=len(b), baseline_median_seconds=previous,
                                  optimized_median_seconds=current, speedup=previous/current,
                                  time_reduction_pct=100*(1-current/previous),
                                  baseline_min_seconds=min(r["run_seconds"] for r in b),
                                  baseline_max_seconds=max(r["run_seconds"] for r in b),
                                  optimized_min_seconds=min(r["run_seconds"] for r in o),
                                  optimized_max_seconds=max(r["run_seconds"] for r in o),
                                  collective_cycles=b[0]["collective_cycles"], drain_cycles=b[0]["drain_cycles"],
                                  all_checks_passed=True))
    return pairs, summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Fresh result directory")
    parser.add_argument("--config", type=Path, default=COLLECTIVES / "configs/mesh_16GBps_flit16B.cfg")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=["baseline_o3", "opt1to6_final"])
    parser.add_argument("--algorithms", nargs="+", choices=("ring1d", "alltoall", "ring2d", "tto"), default=["ring1d", "alltoall"])
    parser.add_argument("--sizes-mib", nargs="+", type=int, default=[1])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--build-root", type=Path)
    parser.add_argument("--sample", action="store_true", help="Also collect statistical CPU-domain estimates")
    args = parser.parse_args()
    if any(variant in ("optimized_o3", "optimized_native") for variant in args.variants):
        parser.error("Optimized experimental measurements require PGO; choose opt1to6_final or opt1to5_final")
    if args.cpu not in os.sched_getaffinity(0) or args.jobs < 1 or args.repetitions < 1 or any(not 1 <= n <= 128 for n in args.sizes_mib):
        parser.error("Invalid CPU, jobs, repetitions or input size")
    if (len(set(args.variants)) != len(args.variants) or len(set(args.algorithms)) != len(args.algorithms)
            or len(set(args.sizes_mib)) != len(args.sizes_mib)):
        parser.error("Variants, algorithms and sizes must be unique")
    verify()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "logs").mkdir()
    config = directory / "hardware.cfg"
    shutil.copy2(args.config.resolve(), config)
    drivers = directory / "driver_sources"
    drivers.mkdir()
    for name in ("reproduce.py", "repro_build.py", "repro_common.py", "verify_layout.py"):
        shutil.copy2(ROOT / name, drivers / name)
    manifest = dict(schema=1, status="building", created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    variants=args.variants, algorithms=args.algorithms, sizes_mib=args.sizes_mib,
                    repetitions=args.repetitions, cpu=args.cpu, sampled=args.sample,
                    config_sha256=sha(config), builds={},
                    driver_sha256={p.name:sha(p) for p in drivers.iterdir()},
                    timing="TrafficManager::Run wall time; setup and final validation excluded; common controller/rate adapter/online checks/output included.",
                    pgo_policy="Full variants train once per requested algorithm at 1 MiB on the same hardware; training excluded from measurements.")
    write_json(directory / "manifest.json", manifest)
    rows, references = [], {}
    try:
        for variant in args.variants:
            manifest["builds"][variant] = build(variant, config, args.algorithms, args.jobs, args.cpu, args.build_root)
            if (variant in ("opt1to5_final", "opt1to6_final")
                    and not manifest["builds"][variant].get("pgo_enabled")):
                raise RuntimeError("Optimized experimental measurements require a PGO build")
            write_json(directory / "manifest.json", manifest)
        manifest["status"] = "measuring"
        write_json(directory / "manifest.json", manifest)
        for algorithm, mib in itertools.product(args.algorithms, args.sizes_mib):
            for repetition in range(1, args.repetitions+1):
                offset = (repetition-1) % len(args.variants)
                order = args.variants[offset:] + args.variants[:offset]
                for variant in order:
                    artifact = manifest["builds"][variant]
                    binary = Path(artifact["binary"])
                    if sha(binary) != artifact["binary_sha256"]:
                        raise RuntimeError("Measurement binary changed")
                    stem = f"{algorithm}_{mib}MiB_{variant}_r{repetition}"
                    print(f"RUN {stem}", flush=True)
                    row = execute(binary, config, algorithm, mib, args.cpu, directory / "logs" / f"{stem}.log", args.sample)
                    row.update(variant=variant, repetition=repetition, binary_sha256=artifact["binary_sha256"])
                    key = (algorithm, mib)
                    if key in references:
                        check_equal(references[key], row)
                    else:
                        references[key] = row
                    row["comparison_passed"] = True
                    rows.append(row)
                    save_csv(directory / "raw.csv", rows)
                    print(f"  {row['run_seconds']:.6f}s; {row['collective_cycles']} cycles; validated", flush=True)
        pairs, summaries = summarize(rows)
        save_csv(directory / "pairs.csv", pairs)
        save_csv(directory / "summary.csv", summaries)
        verify()
        manifest.update(status="complete", measured_runs=len(rows), all_checks_passed=True)
        write_json(directory / "manifest.json", manifest)
        report = ["# Collective reproduction", "", f"Validated runs: {len(rows)}. Repetitions: {args.repetitions}.", "",
                  "Times below are Run wall-time medians, excluding PGO training, setup and final validation.", "",
                  "| Workload | MiB/rank | Comparison | Previous (s) | Current (s) | Speedup | Time reduction |",
                  "|---|---:|---|---:|---:|---:|---:|"]
        for s in summaries:
            report.append(f"| {s['algorithm']} | {s['input_mib']} | {s['baseline']} → {s['optimized']} | {s['baseline_median_seconds']:.6f} | {s['optimized_median_seconds']:.6f} | {s['speedup']:.4f}× | {s['time_reduction_pct']:.2f}% |")
        report += ["", "All runs passed native receive/traffic/buffer/credit validation and cross-run result comparison.",
                   "Known 1 MiB hardware profiles also check the preserved historical result reference.", "",
                   "[Raw measurements](raw.csv) · [Build/source/PGO manifest](manifest.json)", ""]
        (directory / "report.md").write_text("\n".join(report))
        print(f"COMPLETE: {directory / 'report.md'}", flush=True)
    except BaseException as error:
        manifest.update(status="failed", error=str(error))
        write_json(directory / "manifest.json", manifest)
        raise


if __name__ == "__main__":
    main()
