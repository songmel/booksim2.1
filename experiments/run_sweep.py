#!/usr/bin/env python3
"""Compare BookSim 2.0 and fully optimized BookSim Opt on AllReduce and AllToAll."""
import argparse
import csv
import datetime
import fcntl
import itertools
import json
import os
from pathlib import Path
import shutil
import statistics

from repro_build import build
from repro_common import (ALGORITHMS, COLLECTIVES, ROOT, VARIANTS, check_equal,
                          core_hashes, execute, sha, summarize, write_json)
from verify_layout import verify

SOFTWARE = dict(baseline_o3="BookSim2", opt1to6_final="BookSim-Opt")
COLLECTIVE = dict(alltoall="AllToAll", ring1d="AllReduce")


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def report(directory, message):
    print(message, flush=True)
    with (directory / "runner.log").open("a") as stream:
        stream.write(f"{utc_now()} {message}\n")


def run_plan(sizes, repetitions):
    result = []
    for index, (mib, algorithm) in enumerate(itertools.product(sizes, ALGORITHMS)):
        for repetition in range(1, repetitions + 1):
            offset = (index + repetition - 1) % len(VARIANTS)
            for variant in VARIANTS[offset:] + VARIANTS[:offset]:
                result.append((algorithm, mib, repetition, variant))
    return result


def run_key(row):
    return row["algorithm"], row["input_mib"], row["repetition"], row["variant"]


def build_id(variant, algorithm):
    return variant if variant == "baseline_o3" else f"{variant}_{algorithm}"


def require_pgo(artifact, algorithm, configuration_sha256):
    """Reject an untrained executable instead of silently benchmarking it."""
    training = artifact.get("pgo_training") or {}
    feedback = artifact.get("profiles_sha256") or {}
    use = artifact.get("stages", {}).get("use", {})
    commands = [c["argv"] for c in artifact.get("commands", []) if c["stage"] == "use"]
    if (artifact.get("status") != "complete" or artifact.get("pgo_enabled") is not True
            or training != dict(config_sha256=configuration_sha256, algorithms=[algorithm], input_mib=1)
            or not feedback or use.get("binary_sha256") != artifact["binary_sha256"]
            or not commands or any(not any(flag.startswith("-fprofile-use=") for flag in command)
                                   for command in commands)):
        raise RuntimeError(f"Optimized {algorithm} benchmark requires a validated, workload-trained PGO build")
    records = artifact.get("training", [])
    if (len(records) != 1 or records[0].get("algorithm") != algorithm
            or records[0].get("input_mib") != 1
            or records[0].get("validation_passed") is not True
            or records[0].get("is_timing_measurement") is not False):
        raise RuntimeError("Missing or invalid PGO training record")
    profiles = Path(artifact["binary"]).parent / "profiles"
    if any(not (profiles / name).is_file() or sha(profiles / name) != digest
           for name, digest in feedback.items()):
        raise RuntimeError("PGO training data changed or is missing")


def write_csv(path, rows, fields=None):
    fields = fields or sorted({key for row in rows for key in row})
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True)
                             if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def export_results(directory, rows, repetitions):
    """Write partial results honestly: complete means all requested repeats exist."""
    if any(row["variant"] not in VARIANTS or row["algorithm"] not in ALGORITHMS for row in rows):
        raise RuntimeError("Only BookSim 2.0 and BookSim Opt AllReduce/AllToAll measurements are supported")
    if any(row.get("pgo_enabled") != (row["variant"] == "opt1to6_final") for row in rows):
        raise RuntimeError("Measurements require original BookSim O3 or fully optimized BookSim Opt with PGO")
    if rows:
        write_csv(directory / "raw.csv", rows)
    # Keep completed pairs visible while the next repetition is still running.
    keys = {run_key(row) for row in rows}
    paired = [row for row in rows if all(
        (row["algorithm"], row["input_mib"], row["repetition"], variant) in keys for variant in VARIANTS)]
    pairs, summaries = summarize(paired)
    for summary in summaries:
        summary["collective"] = COLLECTIVE[summary["algorithm"]]
        summary["measurement_complete"] = summary["repetitions"] == repetitions
    write_csv(directory / "summary.csv", summaries,
              None if summaries else ["collective", "input_mib", "repetitions", "measurement_complete"])
    write_csv(directory / "pairs.csv", pairs,
              None if pairs else ["algorithm", "input_mib", "repetition", "baseline_seconds", "optimized_seconds"])
    metrics = []
    for algorithm, mib, variant in sorted({(r["algorithm"], r["input_mib"], r["variant"]) for r in rows}):
        selected = [r for r in rows if (r["algorithm"], r["input_mib"], r["variant"]) == (algorithm, mib, variant)]
        first = selected[0]
        seconds = [r["run_seconds"] for r in selected]
        metric = {key: first[key] for key in (
            "active_ranks", "collective_cycles", "drain_cycles", "packets", "flits",
            "link_flits", "packet_latency", "network_latency", "flit_latency", "average_hops",
            "retirement_hash", "flit_bytes", "packet_bytes", "num_vcs", "vc_depth",
            "link_latency_ns", "bandwidth_gbps", "network_bytes_per_rank", "cpu")}
        metric.update(collective=COLLECTIVE[algorithm], algorithm=algorithm,
                      software=SOFTWARE[variant], variant=variant, input_mib=mib,
                      pgo_enabled=first["pgo_enabled"],
                      input_bytes=mib * 1048576, repetitions=len(selected),
                      measurement_complete=len(selected) == repetitions,
                      run_median_seconds=statistics.median(seconds),
                      run_min_seconds=min(seconds), run_max_seconds=max(seconds),
                      run_stdev_seconds=statistics.stdev(seconds) if len(seconds) > 1 else 0,
                      simulated_collective_seconds=first["collective_cycles"] * 1e-9,
                      simulated_drain_seconds=first["drain_cycles"] * 1e-9,
                      total_network_bytes=first["flits"] * first["flit_bytes"],
                      total_link_bytes=first["link_flits"] * first["flit_bytes"],
                      simulated_cycles_per_host_second=first["drain_cycles"] / statistics.median(seconds),
                      validation_passed=all(r["validation_passed"] and r["comparison_passed"] for r in selected))
        for field in ("process_wall_seconds", "process_cpu_seconds", "process_user_seconds",
                      "process_system_seconds", "collective_seconds", "setup_seconds", "validation_seconds"):
            metric[field.replace("_seconds", "_median_seconds")] = statistics.median(r[field] for r in selected)
        metrics.append(metric)
    write_csv(directory / "metrics.csv", metrics,
              None if metrics else ["collective", "software", "input_mib", "repetitions", "measurement_complete"])
    for algorithm in ALGORITHMS:
        times, cycles = [], []
        for summary in summaries:
            if summary["algorithm"] != algorithm:
                continue
            common = dict(input_mib=summary["input_mib"], input_bytes=summary["input_mib"] * 1048576,
                          repetitions=summary["repetitions"], measurement_complete=summary["measurement_complete"],
                          booksim_opt_pgo_enabled=True,
                          all_checks_passed=summary["all_checks_passed"])
            timing = dict(common)
            for prefix, name in (("baseline", "booksim2"), ("optimized", "booksim_opt")):
                for stat in ("median", "min", "max"):
                    timing[f"{name}_{stat}_seconds"] = summary[f"{prefix}_{stat}_seconds"]
            timing.update(speedup=summary["speedup"], time_reduction_pct=summary["time_reduction_pct"])
            times.append(timing)
            cycle = dict(common, clock_period_ns=1)
            for variant, name in (("baseline_o3", "booksim2"), ("opt1to6_final", "booksim_opt")):
                metric = next(r for r in metrics if (r["algorithm"], r["input_mib"], r["variant"]) ==
                              (algorithm, summary["input_mib"], variant))
                for field in ("collective_cycles", "drain_cycles"):
                    cycle[f"{name}_{field}"] = metric[field]
            cycle["cycles_equal"] = cycle["booksim2_collective_cycles"] == cycle["booksim_opt_collective_cycles"]
            cycles.append(cycle)
        stem = "allreduce" if algorithm == "ring1d" else "alltoall"
        write_csv(directory / f"{stem}_runtime.csv", times, [
            "input_mib", "input_bytes", "repetitions", "measurement_complete", "booksim_opt_pgo_enabled",
            "booksim2_median_seconds", "booksim2_min_seconds", "booksim2_max_seconds",
            "booksim_opt_median_seconds", "booksim_opt_min_seconds", "booksim_opt_max_seconds",
            "speedup", "time_reduction_pct", "all_checks_passed"])
        write_csv(directory / f"{stem}_cycles.csv", cycles, [
            "input_mib", "input_bytes", "repetitions", "measurement_complete", "booksim_opt_pgo_enabled", "clock_period_ns",
            "booksim2_collective_cycles", "booksim_opt_collective_cycles",
            "booksim2_drain_cycles", "booksim_opt_drain_cycles", "cycles_equal", "all_checks_passed"])


def progress(directory, manifest, rows, plan, current=None):
    done = {run_key(row) for row in rows}
    # Historical 1 MiB timings only seed the ETA; every CSV value is a fresh run.
    historical = {("alltoall", "baseline_o3"): 23.73, ("alltoall", "opt1to6_final"): 17.05,
                  ("ring1d", "baseline_o3"): 13.49, ("ring1d", "opt1to6_final"): 7.87}
    rates = {}
    for algorithm, variant in itertools.product(ALGORITHMS, VARIANTS):
        observed = [r["process_wall_seconds"] / r["input_mib"] for r in rows
                    if (r["algorithm"], r["variant"]) == (algorithm, variant)]
        rates[algorithm, variant] = statistics.median(observed) if observed else historical[algorithm, variant]
    remaining = sum(rates[algorithm, variant] * mib for algorithm, mib, repetition, variant in plan
                    if (algorithm, mib, repetition, variant) not in done)
    now = datetime.datetime.now(datetime.timezone.utc)
    record = dict(status=manifest["status"], updated_utc=now.isoformat(),
                  completed_runs=len(rows), total_runs=len(plan), current_run=current,
                  estimated_remaining_seconds=remaining,
                  estimated_finish_utc=(now + datetime.timedelta(seconds=remaining)).isoformat(),
                  estimate_method="Linear scaling from median observed process seconds/MiB; historical 1MiB timings fill unmeasured groups.")
    write_json(directory / "progress.json", record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes-mib", nargs="+", type=int, default=[1, 2, 4, 8, 16, 32, 64])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if (args.cpu not in os.sched_getaffinity(0) or args.jobs < 1 or args.repetitions < 1
            or not args.sizes_mib or len(set(args.sizes_mib)) != len(args.sizes_mib)
            or any(not 1 <= size <= 128 for size in args.sizes_mib)):
        parser.error("Invalid CPU, jobs, sizes or repetition count")
    verify()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".sweep.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another sweep is already using this output directory")
        run(args, directory, parser)


def run(args, directory, parser):
    manifest_path = directory / "manifest.json"
    settings = dict(variants=list(VARIANTS), algorithms=list(ALGORITHMS), sizes_mib=args.sizes_mib,
                    repetitions=args.repetitions, cpu=args.cpu, sampled=False)
    config = directory / "hardware.cfg"
    if args.resume:
        manifest = json.loads(manifest_path.read_text())
        if any(manifest.get(key) != value for key, value in settings.items()):
            parser.error("Resume settings differ from the saved experiment")
        if sha(config) != manifest["config_sha256"]:
            parser.error("Saved hardware configuration changed")
    else:
        if any(path.name != ".sweep.lock" for path in directory.iterdir()):
            parser.error("Output must be empty; use --resume for an existing experiment")
        for name in ("logs", "records", "driver_sources"):
            (directory / name).mkdir()
        shutil.copy2(COLLECTIVES / "configs/mesh_16GBps_flit16B.cfg", config)
        for name in ("run_sweep.py", "reproduce.py", "repro_build.py", "repro_common.py", "verify_layout.py"):
            shutil.copy2(ROOT / name, directory / "driver_sources" / name)
        manifest = dict(schema=1, status="building", created_utc=utc_now(), **settings,
                        config_sha256=sha(config), builds={},
                        driver_sha256={p.name: sha(p) for p in (directory / "driver_sources").iterdir()},
                        timing="TrafficManager::Run wall seconds; setup and final validation excluded; controller/link/online validation/stats included.",
                        simulated_clock_period_ns=1,
                        compiler_comparison="BookSim2: O3. BookSim-Opt: OPT1..6, O3/native/LTO-auto/no-exceptions/PGO. PGO is mandatory for every optimized measurement.",
                        pgo_policy="Separate optimized executable per algorithm, each trained once at 1 MiB with the same hardware. Training is excluded from measurements; the trained executable is fixed across all measured sizes.",
                        measured_runs=0,
                        allreduce_algorithm="Ring1D ReduceScatter + AllGather, 64 ranks",
                        input_semantics="MiB per rank; AllToAll total send buffer includes the self block.",
                        execution="Serial runs pinned to one logical CPU; no concurrent benchmark jobs.",
                        timeout_policy="max(900, 90 * input_mib) wall seconds per process")
        write_json(manifest_path, manifest)
    plan = run_plan(args.sizes_mib, args.repetitions)
    rows, references = [], {}
    try:
        for path in sorted((directory / "records").glob("*.json")):
            row = json.loads(path.read_text())
            if run_key(row) not in plan or any(run_key(old) == run_key(row) for old in rows):
                raise RuntimeError(f"Unexpected or duplicate saved measurement: {path}")
            if not row.get("validation_passed") or not row.get("comparison_passed"):
                raise RuntimeError(f"Unvalidated saved measurement: {path}")
            if row.get("pgo_enabled") != (row["variant"] == "opt1to6_final"):
                raise RuntimeError(f"Saved measurement does not satisfy the PGO policy: {path}")
            key = row["algorithm"], row["input_mib"]
            if key in references:
                check_equal(references[key], row)
            references[key] = row
            rows.append(row)
        export_results(directory, rows, args.repetitions)
        configurations = [("baseline_o3", None), *(('opt1to6_final', algorithm) for algorithm in ALGORITHMS)]
        for variant, algorithm in configurations:
            identifier = build_id(variant, algorithm)
            progress(directory, manifest, rows, plan, current=dict(stage="build_and_train", variant=variant, algorithm=algorithm))
            if identifier in manifest["builds"]:
                artifact = manifest["builds"][identifier]
                repository = "booksim2" if variant == "baseline_o3" else "booksim-opt"
                if core_hashes(ROOT.parent / repository) != artifact["source_sha256"]:
                    raise RuntimeError("Core changed since the saved build; start a new experiment")
            else:
                # A new result directory gets fresh builds and fresh PGO data.
                # Each optimized algorithm trains its own 1 MiB workload.
                artifact = build(variant, config, [algorithm] if algorithm else list(ALGORITHMS),
                                 args.jobs, args.cpu, directory / "build")
                manifest["builds"][identifier] = artifact
                write_json(manifest_path, manifest)
            if sha(Path(artifact["binary"])) != artifact["binary_sha256"]:
                raise RuntimeError("Saved measurement binary changed")
            if variant == "opt1to6_final":
                require_pgo(artifact, algorithm, manifest["config_sha256"])
            for row in rows:
                if (row["variant"] == variant and (algorithm is None or row["algorithm"] == algorithm)
                        and row["binary_sha256"] != artifact["binary_sha256"]):
                    raise RuntimeError("Saved row belongs to a different measurement binary")
        manifest.update(status="measuring", resumed_utc=utc_now() if args.resume else None)
        write_json(manifest_path, manifest)
        completed = {run_key(row) for row in rows}
        for algorithm, mib, repetition, variant in plan:
            key = algorithm, mib, repetition, variant
            if key in completed:
                continue
            stem = f"{algorithm}_{mib}MiB_{variant}_r{repetition}"
            progress(directory, manifest, rows, plan, current=dict(
                algorithm=algorithm, input_mib=mib, repetition=repetition, variant=variant, started_utc=utc_now()))
            report(directory, f"[{len(rows)+1}/{len(plan)}] RUN {stem}")
            identifier = build_id(variant, algorithm)
            artifact = manifest["builds"][identifier]
            binary = Path(artifact["binary"])
            if sha(binary) != artifact["binary_sha256"] or sha(config) != manifest["config_sha256"]:
                raise RuntimeError("Measurement binary or configuration changed")
            started = utc_now()
            row = execute(binary, config, algorithm, mib, args.cpu, directory / "logs" / f"{stem}.log")
            reference_key = algorithm, mib
            if reference_key in references:
                check_equal(references[reference_key], row)
            references[reference_key] = row
            row.update(variant=variant, software=SOFTWARE[variant], collective=COLLECTIVE[algorithm],
                       repetition=repetition, binary_sha256=artifact["binary_sha256"],
                       pgo_enabled=artifact["pgo_enabled"], build_id=identifier,
                       pgo_training_algorithm=algorithm if artifact["pgo_enabled"] else None,
                       started_utc=started, finished_utc=utc_now(), comparison_passed=True)
            # One durable JSON per completed measurement makes retries resumable.
            write_json(directory / "records" / f"{stem}.json", row)
            rows.append(row)
            export_results(directory, rows, args.repetitions)
            manifest["measured_runs"] = len(rows)
            write_json(manifest_path, manifest)
            progress(directory, manifest, rows, plan)
            report(directory, f"  {row['run_seconds']:.6f} s; {row['collective_cycles']} cycles; validated")
        manifest.update(status="complete", finished_utc=utc_now(), measured_runs=len(rows), all_checks_passed=True)
        write_json(manifest_path, manifest)
        progress(directory, manifest, rows, plan)
        report(directory, f"COMPLETE {len(rows)} validated measurements: {directory}")
    except BaseException as error:
        manifest.update(status="failed", error=str(error), failed_utc=utc_now(), measured_runs=len(rows))
        write_json(manifest_path, manifest)
        progress(directory, manifest, rows, plan)
        raise


if __name__ == "__main__":
    main()
