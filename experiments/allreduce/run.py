#!/usr/bin/env python3
"""Run paired, serial AllReduce benchmarks and persist raw/paired/summary CSVs."""
import argparse
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time

from build import ROOT, REPOS, BUILD_ROOT, build
from verify_sources import verify

FIELDS = ["algorithm", "input_mib", "input_bytes", "padded_bytes", "active_ranks", "excluded_node",
          "repetition", "backend", "commit", "opt_flags", "cpu", "collective_cycles", "drain_cycles",
          "run_seconds", "collective_seconds", "setup_seconds", "validation_seconds", "process_wall_seconds",
          "user_seconds", "system_seconds", "peak_rss_kib", "packets", "flits", "link_flits",
          "retirement_hash", "packet_latency", "network_latency", "flit_latency", "average_hops",
          "validation_passed", "cycle_match", "traffic_match", "retirement_match", "statistics_match",
          "status", "log_file", "error"]
PAIR_FIELDS = ["algorithm", "input_mib", "repetition", "baseline_cycles", "optimized_cycles",
               "baseline_drain_cycles", "optimized_drain_cycles", "cycle_match", "traffic_match",
               "retirement_match", "statistics_match", "baseline_seconds", "optimized_seconds", "speedup"]
SUMMARY_FIELDS = ["algorithm", "input_mib", "repetitions", "active_ranks", "input_bytes", "padded_bytes",
                  "collective_cycles", "drain_cycles", "all_checks_passed", "baseline_median_seconds",
                  "optimized_median_seconds", "baseline_min_seconds", "baseline_max_seconds",
                  "optimized_min_seconds", "optimized_max_seconds", "speedup", "baseline_cycles_per_second",
                  "optimized_cycles_per_second"]


def save_csv(path, fields, rows):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def truth(value):
    return str(value).lower() == "true"


def compare(baseline, optimized):
    def equal(fields):
        return all(str(baseline[field]) == str(optimized[field]) for field in fields)
    return {
        "cycle_match": equal(["collective_cycles", "drain_cycles"]),
        "traffic_match": equal(["input_bytes", "padded_bytes", "active_ranks", "packets", "flits", "link_flits"]),
        "retirement_match": equal(["retirement_hash"]),
        "statistics_match": equal(["packet_latency", "network_latency", "flit_latency", "average_hops"]),
    }


def require_matching_pairs(pairs):
    if any(not all(truth(pair[field]) for field in
                   ("cycle_match", "traffic_match", "retirement_match", "statistics_match"))
           for pair in pairs):
        raise RuntimeError("Baseline/optimized mismatch; CSV retained, benchmark stopped")


def measure(binary, algorithm, mib, repetition, backend, args, directory, manifest):
    stem = f"{algorithm}_{mib:03d}MiB_r{repetition}_{backend}"
    log_path = directory / "logs" / f"{stem}.log"
    time_path = directory / "logs" / f"{stem}.time.json"
    command = ["/usr/bin/time", "-o", str(time_path), "-f",
               '{"user_seconds":%U,"system_seconds":%S,"peak_rss_kib":%M}',
               str(binary), str(ROOT / "hardware.cfg"), algorithm, str(mib*1024*1024),
               str(args.stripe_bytes), str(args.max_cycles)]
    row = {"algorithm": algorithm, "input_mib": mib, "input_bytes": mib*1024*1024,
           "repetition": repetition, "backend": backend, "commit": manifest["commit"],
           "opt_flags": " ".join(f for f in manifest["flags"] if f.startswith("-D")),
           "cpu": args.cpu, "log_file": str(log_path), "status": "failed", "error": ""}
    start = time.perf_counter()
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   preexec_fn=lambda: os.sched_setaffinity(0, {args.cpu}))
        try:
            code = process.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = -1
            row["error"] = f"Timed out after {args.timeout} seconds"
    row["process_wall_seconds"] = time.perf_counter()-start
    output = log_path.read_text()
    markers = [line[len("EXPERIMENT_RESULT "):] for line in output.splitlines()
               if line.startswith("EXPERIMENT_RESULT ")]
    if code == 0 and len(markers) == 1:
        result = json.loads(markers[0])
        if not result.get("validation_passed"):
            row["error"] = "C++ validation did not pass"
        elif result["input_bytes"] != row["input_bytes"] or result["algorithm"] != algorithm:
            row["error"] = "C++ returned the wrong workload"
        else:
            row.update(result)
            row["status"] = "ok"
    elif not row["error"]:
        row["error"] = f"Exit status {code}; expected exactly one validated result marker"
    if time_path.exists():
        try:
            # GNU time may prepend a signal/error description on failure.
            row.update(json.loads(time_path.read_text().splitlines()[-1]))
        except (json.JSONDecodeError, IndexError):
            pass
    return row


def regenerate(directory, records, repetitions):
    groups = {}
    for row in records:
        groups.setdefault((row["algorithm"], int(row["input_mib"]), int(row["repetition"])), {})[row["backend"]] = row
    pairs = []
    for (algorithm, mib, repetition), backends in sorted(groups.items()):
        if set(backends) != {"baseline", "optimized"}:
            continue
        baseline, optimized = backends["baseline"], backends["optimized"]
        if baseline["status"] != "ok" or optimized["status"] != "ok":
            continue
        checks = compare(baseline, optimized)
        for row in backends.values():
            row.update(checks)
        pairs.append({"algorithm": algorithm, "input_mib": mib, "repetition": repetition,
                      "baseline_cycles": baseline["collective_cycles"], "optimized_cycles": optimized["collective_cycles"],
                      "baseline_drain_cycles": baseline["drain_cycles"], "optimized_drain_cycles": optimized["drain_cycles"],
                      **checks, "baseline_seconds": baseline["run_seconds"], "optimized_seconds": optimized["run_seconds"],
                      "speedup": float(baseline["run_seconds"])/float(optimized["run_seconds"])})
    save_csv(directory / "raw.csv", FIELDS, records)
    save_csv(directory / "pairs.csv", PAIR_FIELDS, pairs)
    summaries = []
    for algorithm, mib in sorted({(row["algorithm"], int(row["input_mib"])) for row in records}):
        selected = [p for p in pairs if p["algorithm"] == algorithm and p["input_mib"] == mib]
        if len(selected) != repetitions:
            continue
        baseline_times = [float(p["baseline_seconds"]) for p in selected]
        optimized_times = [float(p["optimized_seconds"]) for p in selected]
        first = next(r for r in records if r["algorithm"] == algorithm and int(r["input_mib"]) == mib)
        bmedian, omedian = statistics.median(baseline_times), statistics.median(optimized_times)
        summaries.append({"algorithm": algorithm, "input_mib": mib, "repetitions": repetitions,
                          **{f: first[f] for f in ("active_ranks", "input_bytes", "padded_bytes", "collective_cycles", "drain_cycles")},
                          "all_checks_passed": all(all(truth(p[f]) for f in ("cycle_match", "traffic_match", "retirement_match", "statistics_match")) for p in selected),
                          "baseline_median_seconds": bmedian, "optimized_median_seconds": omedian,
                          "baseline_min_seconds": min(baseline_times), "baseline_max_seconds": max(baseline_times),
                          "optimized_min_seconds": min(optimized_times), "optimized_max_seconds": max(optimized_times),
                          "speedup": bmedian/omedian,
                          "baseline_cycles_per_second": int(first["drain_cycles"])/bmedian,
                          "optimized_cycles_per_second": int(first["drain_cycles"])/omedian})
    save_csv(directory / "summary.csv", SUMMARY_FIELDS, summaries)
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes-mib", nargs="+", type=int, default=[1])
    parser.add_argument("--algorithms", nargs="+", choices=["ring1d", "alltoall", "ring2d", "tto"], default=["ring1d", "alltoall"])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--stripe-bytes", type=int, default=16384)
    parser.add_argument("--max-cycles", type=int, default=1000000000)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if any(mib < 1 or mib > 128 for mib in args.sizes_mib) or len(set(args.sizes_mib)) != len(args.sizes_mib):
        parser.error("Sizes must be distinct integer MiB values within 1..128")
    if args.repetitions < 1 or args.cpu not in os.sched_getaffinity(0):
        parser.error("Invalid repetition count or unavailable CPU")
    if len(set(args.algorithms)) != len(args.algorithms):
        parser.error("Algorithms must be distinct")
    if args.stripe_bytes <= 0 or args.stripe_bytes%512:
        parser.error("Stripe size must be a positive multiple of 512 bytes")
    directory = (args.output or ROOT / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    lock = (directory / ".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifests = {}
    for backend in REPOS:
        manifests[backend] = json.loads((BUILD_ROOT / backend / "manifest.json").read_text()) if args.no_build else build(backend, args.jobs)
        binary = Path(manifests[backend]["binary"])
        if hashlib.sha256(binary.read_bytes()).hexdigest() != manifests[backend]["binary_sha256"]:
            raise RuntimeError(f"Binary hash mismatch: {backend}")
    verify()
    manifest = {"sizes_mib": args.sizes_mib, "algorithms": args.algorithms, "repetitions": args.repetitions,
                "stripe_bytes": args.stripe_bytes, "cpu": args.cpu, "max_cycles": args.max_cycles,
                "hardware_sha256": hashlib.sha256((ROOT / "hardware.cfg").read_bytes()).hexdigest(),
                "backend_builds": manifests, "timing_scope": "native TrafficManager::Run including shared experiment callbacks/adapters; excludes setup and final validation",
                "hardware": {"mesh": "8x8", "router_hz": 1000000000, "link_bytes_per_second": 25000000000,
                             "packet_bytes": 512, "flit_bytes": 32, "link_propagation_ns": 21,
                             "vcs": 4, "vc_depth_flits": 318, "flow_control": "conservative empty-VC VCT admission; tail-credit VC reuse",
                             "serialization_ns": 1.28, "serializer_tick_ps": 40, "sampling_ns": 1,
                             "endpoint_propagation_ns": 1, "node_pes": "8x8 metadata; no compute workload",
                             "reduction_compute_cycles": 0, "extra_header_flits": 0,
                             "ports_per_rank": 1},
                "host": {"platform": os.uname()._asdict() if hasattr(os.uname(), "_asdict") else list(os.uname())}}
    manifest_path = directory / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise RuntimeError("Existing output uses a different build/configuration; select a new directory")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    records = read_csv(directory / "raw.csv")
    # A resume must never skip a previously detected mismatch merely because
    # both individual processes had successful exit/validation status.
    require_matching_pairs(regenerate(directory, records, args.repetitions))
    keys = {(r["algorithm"], int(r["input_mib"]), int(r["repetition"]), r["backend"]) for r in records if r["status"] == "ok"}
    total = len(args.algorithms)*len(args.sizes_mib)*args.repetitions*2
    print(f"Output: {directory}\nRuns: {total}; serial execution on CPU {args.cpu}", flush=True)
    for mib in args.sizes_mib:
        for algorithm in args.algorithms:
            for repetition in range(1, args.repetitions+1):
                order = ["baseline", "optimized"] if repetition%2 else ["optimized", "baseline"]
                for backend in order:
                    key = (algorithm, mib, repetition, backend)
                    if key in keys:
                        continue
                    print(f"[{len(keys)+1}/{total}] {algorithm} {mib} MiB r{repetition} {backend} starting", flush=True)
                    row = measure(Path(manifests[backend]["binary"]), algorithm, mib, repetition, backend,
                                  args, directory, manifests[backend])
                    records = [r for r in records if (r["algorithm"], int(r["input_mib"]), int(r["repetition"]), r["backend"]) != key]
                    records.append(row)
                    pairs = regenerate(directory, records, args.repetitions)
                    if row["status"] != "ok":
                        raise RuntimeError(f"Experiment failed: {row['error']}; see {row['log_file']}")
                    keys.add(key)
                    print(f"  {row['run_seconds']:.6f}s; collective {row['collective_cycles']} cycles; validation passed", flush=True)
                    require_matching_pairs(pairs)
    print(f"Complete: {len(keys)} validated runs; {directory / 'summary.csv'}", flush=True)
    verify()


if __name__ == "__main__":
    main()
