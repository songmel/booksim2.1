#!/usr/bin/env python3
"""Measure OPT6 on 1 MiB AllToAll against matching GitHub-based core builds."""
import argparse
import concurrent.futures
import csv
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tarfile

from compiler_pilot import write_json
from profile import ROOT, sha
from run import compare, save_csv
from table_pilot import execute, check_preserved

WORKSPACE = ROOT.parents[1]
VARIANTS = ("baseline", "opt1to5", "opt1to6")
OVERLAY = ("src/Makefile", "src/implementation_opts.hpp",
           "src/allocators/allocator.hpp", "src/allocators/allocator.cpp",
           "src/allocators/islip_compact.hpp", "src/allocators/islip_compact.cpp",
           "src/routers/iq_router.cpp")


def save_rows(path, rows):
    if rows:
        save_csv(path, sorted({k for row in rows for k in row}), rows)


def sources_hashes(repo):
    return {str(p.relative_to(repo)): sha(p) for p in sorted((repo / "src").rglob("*"))
            if p.is_file() and (p.suffix in (".cpp", ".hpp", ".l", ".y") or p.name == "Makefile")}


def command(argv, cwd, log):
    process = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=600)
    log.write_text(process.stdout)
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {log}\n{process.stdout[-2000:]}")


def build(variant, args, directory, config, reference, training):
    repo = WORKSPACE / ("booksim2" if variant == "baseline" else "booksim-opt")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    archive = subprocess.check_output(["git", "archive", commit, "src"], cwd=repo)
    destination = directory / "build" / variant
    source_root = destination / "source"
    source_root.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        snapshot.extractall(source_root)
    if variant != "baseline":
        for name in OVERLAY:
            shutil.copy2(repo / name, source_root / name)
    external = destination / "experiment_sources"
    shutil.copytree(ROOT / "src", external)
    source = source_root / "src"
    objects, logs, profiles = (destination / name for name in ("objects", "logs", "profiles"))
    for path in (objects, logs, profiles):
        path.mkdir()
    command(["bison", "-y", "-d", "config.y"], source, logs / "bison.log")
    command(["flex", "config.l"], source, logs / "flex.log")
    units = sorted(p for p in source.glob("*.cpp") if p.name != "main.cpp")
    units += sorted(source.glob("*/*.cpp")) + sorted(external.glob("*.cpp"))
    units += [source / "lex.yy.c", source / "y.tab.c"]
    object_paths = [objects / f"{i:03d}_{p.stem}.o" for i, p in enumerate(units)]
    common = ["-O3", "-g", "-Wall", "-march=native", "-flto=auto"]
    switches = [f"-DOPT{i}={int(variant != 'baseline')}" for i in range(1, 6)]
    switches += [f"-DOPT6={int(variant == 'opt1to6')}"]
    cxx_flags = ["-std=c++11", "-fno-exceptions", *switches]
    includes = [f"-I{source / name}" for name in (".", "allocators", "arbiters", "routers", "networks", "power")]
    includes += [f"-I{external}"]
    binary = destination / "collective"
    record = dict(variant=variant, backend="baseline" if variant == "baseline" else "optimized",
                  commit=commit, binary=str(binary), flags=[*common, *cxx_flags, f"-fprofile-use={profiles}"],
                  archive_sha256=hashlib.sha256(archive).hexdigest(),
                  core_files_sha256=sources_hashes(source_root),
                  external_sources_sha256={p.name:sha(p) for p in external.iterdir() if p.is_file()},
                  intentional_overlay=[] if variant == "baseline" else list(OVERLAY), commands=[], stages={})

    def compile_stage(stage):
        feedback = [f"-fprofile-generate={profiles}"] if stage == "generate" else [
            f"-fprofile-use={profiles}", "-Werror=missing-profile", "-Werror=coverage-mismatch"]
        commands = [["g++" if src.suffix == ".cpp" else "gcc", *common,
                     *(cxx_flags if src.suffix == ".cpp" else []), *feedback, *includes,
                     "-c", str(src), "-o", str(obj)] for src, obj in zip(units, object_paths)]
        record["commands"].extend(dict(stage=stage, argv=c) for c in commands)
        write_json(destination / "manifest.json", record)
        print(f"BUILD {variant}: PGO {stage}, {len(commands)} units", flush=True)
        def one(item):
            i, argv = item
            command(argv, source, logs / f"{stage}_{i:03d}.log")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(one, enumerate(commands)))
        link = ["g++", *common, "-fno-exceptions", *feedback, *map(str, object_paths), "-o", str(binary)]
        record["commands"].append(dict(stage=stage, argv=link))
        command(link, source, logs / f"{stage}_link.log")
        record["stages"][stage] = dict(binary_sha256=sha(binary), objects_sha256={p.name:sha(p) for p in object_paths})
        write_json(destination / "manifest.json", record)

    compile_stage("generate")
    shutil.copy2(binary, destination / "collective.profile_generate")
    record["binary_sha256"] = sha(binary)
    row = execute(record, variant, config, "alltoall", 0, True, args, directory, reference)
    row.update(is_timing_measurement=False, stage="pgo_training")
    training.append(row)
    save_rows(directory / "training.csv", training)
    feedback = sorted(profiles.rglob("*.gcda"))
    if not feedback:
        raise RuntimeError(f"PGO feedback is missing: {variant}")
    record["profiles_sha256"] = {str(p.relative_to(profiles)):sha(p) for p in feedback}
    record["profile_file_count"] = len(feedback)
    compile_stage("use")
    record["binary_sha256"] = sha(binary)
    record["profile_use_strict_checks_passed"] = True
    if sources_hashes(source_root) != record["core_files_sha256"]:
        raise RuntimeError("Candidate core source changed during build")
    check_preserved({variant:record})
    write_json(destination / "manifest.json", record)
    return record


def summarize(directory, rows, repetitions):
    summaries = []
    for mode, metric in (("unsampled_control", "run_seconds"),
                         ("sampled", "booksim_cpu_seconds_estimate"),
                         ("sampled", "run_seconds")):
        medians = {}
        for variant in VARIANTS:
            group = [r for r in rows if r["variant"] == variant and r["mode"] == mode]
            if len(group) != repetitions:
                raise RuntimeError("Incomplete timing matrix")
            values = [r[metric] for r in group]
            medians[variant] = statistics.median(values)
        for base in ("baseline", "opt1to5"):
            previous, current = medians[base], medians["opt1to6"]
            group = [r for r in rows if r["variant"] == "opt1to6" and r["mode"] == mode]
            summaries.append(dict(comparison=f"{base}_vs_opt1to6", mode=mode, metric=metric,
                                  algorithm="alltoall", input_mib=1, repetitions=repetitions,
                                  baseline_median_seconds=previous, optimized_median_seconds=current,
                                  speedup=previous/current, speed_increase_pct=100*(previous/current-1),
                                  time_reduction_pct=100*(1-current/previous),
                                  optimized_min_seconds=min(r[metric] for r in group),
                                  optimized_max_seconds=max(r[metric] for r in group),
                                  all_checks_passed=True))
    save_rows(directory / "summary.csv", summaries)
    pairs = []
    for mode in ("sampled", "unsampled_control"):
        for rep in range(1, repetitions+1):
            group = {r["variant"]:r for r in rows if r["mode"] == mode and r["repetition"] == rep}
            for base in ("baseline", "opt1to5"):
                checks = compare(group[base], group["opt1to6"])
                if not all(checks.values()):
                    raise RuntimeError("Pair validation mismatch")
                pairs.append(dict(comparison=f"{base}_vs_opt1to6", mode=mode, repetition=rep, **checks,
                                  run_speedup=group[base]["run_seconds"]/group["opt1to6"]["run_seconds"]))
    save_rows(directory / "pairs.csv", pairs)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0) or args.jobs < 1 or args.repetitions < 3:
        parser.error("Use an available CPU, positive job count, and at least three repetitions")
    args.output = directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    for name in ("logs", "configs", "driver_sources"):
        (directory / name).mkdir()
    args.input_mib = 1
    prior = ROOT / "results/table1_mesh_16GBps_flit16B_ring1d_alltoall_final_1MiB_3reps"
    previous = json.loads((prior / "manifest.json").read_text())
    if previous["status"] != "complete":
        raise RuntimeError("Historical hardware reference is incomplete")
    check_preserved(previous["builds"])
    args.hardware_config = previous["effective_config"]
    args.hardware_name = previous["hardware"]
    config = directory / "configs" / "alltoall_1MiB.cfg"
    shutil.copy2(next((prior / "configs").glob("*.cfg")), config)
    reference = next(r for r in csv.DictReader((prior / "raw.csv").open()) if r["algorithm"] == "alltoall")
    preserved = {name:sources_hashes(WORKSPACE / name) for name in ("booksim2", "booksim-opt")}
    manifest = dict(status="building", created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    algorithm="alltoall", input_mib=1, repetitions=args.repetitions, cpu=args.cpu,
                    hardware=args.hardware_name, effective_config=args.hardware_config, config_sha256=sha(config),
                    variants=VARIANTS, source_policy="Git HEAD snapshots; OPT6 overlay only on optimized cores",
                    source_inputs_sha256=preserved, historical_reference_directory=str(prior),
                    historical_manifest_sha256=sha(prior / "manifest.json"),
                    historical_builds=previous["builds"],
                    compiler=subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
                    pgo_policy="Fresh per-variant feedback from one sampled 1 MiB AllToAll run on identical hardware; excluded from timing. No held-out-workload claim.",
                    timing="Primary: three-run median unsampled TrafficManager::Run wall time. Also report separately BookSim CPU sampling estimates and sampled total runtime.",
                    plan={mode:[[VARIANTS[(rep+shift)%3] for shift in range(3)] for rep in range(args.repetitions)]
                          for mode in ("sampled", "unsampled_control")},
                    builds={}, profile_period_us=1000)
    for name in ("opt6_pilot.py", "table_pilot.py", "compiler_pilot.py", "run.py", "profile.py"):
        shutil.copy2(ROOT / name, directory / "driver_sources" / name)
    manifest["driver_sha256"] = {p.name:sha(p) for p in (directory / "driver_sources").iterdir()}
    write_json(directory / "manifest.json", manifest)
    rows, training = [], []
    try:
        for variant in VARIANTS:
            manifest["builds"][variant] = build(variant, args, directory, config, reference, training)
            write_json(directory / "manifest.json", manifest)
        manifest["status"] = "measuring"
        write_json(directory / "manifest.json", manifest)
        for mode, rounds in manifest["plan"].items():
            for rep, order in enumerate(rounds, 1):
                for variant in order:
                    row = execute(manifest["builds"][variant], variant, config, "alltoall", rep,
                                  mode == "sampled", args, directory, reference)
                    row.update(stage="measurement", is_timing_measurement=True)
                    rows.append(row)
                    save_rows(directory / "raw.csv", rows)
        summaries = summarize(directory, rows, args.repetitions)
        check_preserved(manifest["builds"])
        check_preserved(previous["builds"])
        if any(sources_hashes(WORKSPACE / name) != values for name, values in preserved.items()):
            raise RuntimeError("Working sources changed during the experiment")
        manifest.update(status="complete", all_checks_passed=True, training_runs=len(training),
                        sampled_runs=sum(r["mode"] == "sampled" for r in rows),
                        unsampled_runs=sum(r["mode"] != "sampled" for r in rows),
                        preserved_sources_and_archived_builds=True)
        write_json(directory / "manifest.json", manifest)
        print(json.dumps(summaries, indent=2), flush=True)
    except BaseException as error:
        manifest.update(status="failed", error=str(error))
        write_json(directory / "manifest.json", manifest)
        raise


if __name__ == "__main__":
    main()
