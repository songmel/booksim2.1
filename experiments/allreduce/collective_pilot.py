#!/usr/bin/env python3
"""Ring1D + equal-size direct AllToAll; 1 MiB/rank, unchanged simulator cores."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import subprocess

from profile import ROOT, build_profile, sha
from run import compare, save_csv
import sweep_hardware as sweep
from verify_sources import verify

ALGORITHMS = ("ring1d", "alltoall")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/ring1d_alltoall_1MiB_mesh_3reps")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--hardware", choices=("mesh_native", "table"), default="mesh_native")
    args = parser.parse_args()
    if args.repetitions < 1 or args.cpu not in os.sched_getaffinity(0):
        parser.error("Invalid repetitions or CPU")
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / ".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (directory / "manifest.json").exists():
        raise RuntimeError("Choose a new output directory; measurements are never overwritten")
    for sub in ("logs", "configs", "driver_sources"):
        (directory / sub).mkdir(exist_ok=True)
    verify()
    builds = {b: build_profile(b, ROOT / "build/current_workloads") for b in ("baseline", "optimized")}
    options = dict(sweep.cases())["native_1vc_packet128B" if args.hardware == "mesh_native" else "table_rate_anchor"]
    if args.hardware == "mesh_native":
        options = {**options, "packet_size":32, "experiment_bandwidth_gbps":16}
    hardware = "mesh_native_1vc_packet512B" if args.hardware == "mesh_native" else "table_rate_25GBps"
    cfg = directory / "configs" / f"{hardware}.cfg"
    cfg.write_text(sweep.config_text(options))
    manifest = {"builds":builds, "algorithms":ALGORITHMS, "input_mib":1, "cpu":args.cpu,
                "repetitions":args.repetitions, "topology":"mesh", "hardware":hardware,
                "effective_config":options, "config_sha256":sha(cfg), "status":"running",
                "alltoall":{"schedule":"direct_exchange", "size_semantics":"total send/receive buffer per rank, including self block",
                             "ranks":64, "peer_block_bytes":16384, "network_bytes_per_rank":1032192,
                             "local_copy_bytes":16384, "self_copy_cycles":0,
                             "packet_service":"round-robin across all eligible peer messages",
                             "payload_validation":"per-packet source/destination/index, per-source receive-slot provenance; no numeric tensor simulation",
                             "reference":"https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/node109.htm"},
                "measurement":"Exclusive BookSim CPU sampling estimates, 1000us requested. Same hardware for each baseline/optimized pair. Controller/output/custom adapter work excluded; native channels included.",
                "limitations":"Existing mesh exploration hardware, not the proposed data-center model. Different collectives have different network traffic; speedups compare simulator backends within each workload.",
                "driver_sha256":{name:sha(ROOT/name) for name in ("collective_pilot.py","profile.py","sweep_hardware.py","run.py")}}
    for name in manifest["driver_sha256"]:
        (directory / "driver_sources" / name).write_bytes((ROOT/name).read_bytes())
    def save_manifest():
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    save_manifest()
    rows, pairs, controls = [], [], []
    sweep.ALGORITHMS = ALGORITHMS
    try:
        sweep.run_case(hardware, options, "measurement", args.repetitions, builds, args, directory, rows, pairs)
        # The new wrapper must preserve the earlier Ring1D network result.
        old_dir = "ring_1MiB_mesh_refinement" if args.hardware == "mesh_native" else "table1_1to128MiB_3reps"
        old = list(csv.DictReader((ROOT/"results"/old_dir/"raw.csv").open()))
        for row in rows:
            if row["algorithm"] != "ring1d":
                continue
            reference = next(r for r in old if r["algorithm"] == "ring1d" and r["backend"] == row["backend"] and
                             (r.get("stage")=="confirmation" if args.hardware=="mesh_native" else r["input_mib"]=="1"))
            if not all(compare(row, reference).values()):
                raise RuntimeError("Ring1D historical reference changed")
        manifest["ring1d_historical_reference_match"] = True
        for algorithm in ALGORITHMS:
            for backend in ("baseline", "optimized"):
                log = directory/"logs"/f"control_{hardware}_{algorithm}_{backend}.log"
                command = ["taskset", "-c", str(args.cpu), builds[backend]["binary"], str(cfg), algorithm,
                           "1048576", "16384", "1000000000"]
                print(f"Unsampled control: {algorithm} {backend}", flush=True)
                process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
                log.write_text(process.stdout)
                markers = [s.removeprefix("EXPERIMENT_RESULT ") for s in process.stdout.splitlines() if s.startswith("EXPERIMENT_RESULT ")]
                if process.returncode or len(markers)!=1:
                    raise RuntimeError(f"Control failed: {log}")
                row = json.loads(markers[0])
                reference = next(r for r in rows if r["algorithm"]==algorithm and r["backend"]==backend)
                checks = compare(row, reference)
                row.update(backend=backend, mode="unsampled_control", hardware=hardware, log_file=str(log), **checks)
                controls.append(row)
                save_csv(directory/"controls.csv", sorted({k for r in controls for k in r}), controls)
                if not row["validation_passed"] or not all(checks.values()):
                    raise RuntimeError(f"Control mismatch: {log}")
        for backend, build in builds.items():
            if sha(Path(build["binary"])) != build["binary_sha256"]:
                raise RuntimeError(f"Binary changed: {backend}")
        verify()
        manifest["status"] = "complete"
        manifest["sampled_runs"] = len(rows)
        manifest["unsampled_controls"] = len(controls)
        manifest["all_checks_passed"] = True
    except BaseException as error:
        manifest["status"] = "failed_or_interrupted"
        manifest["error"] = repr(error)
        raise
    finally:
        save_manifest()
    print(f"Complete: {directory}", flush=True)


if __name__ == "__main__":
    main()
