#!/usr/bin/env python3
"""Refine packet length on mesh, Ring1D/Ring2D at 1 MiB only."""
import argparse
import csv
import json
import os
from pathlib import Path

from profile import ROOT, sha
import sweep_hardware as sweep
from verify_sources import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/ring_1MiB_mesh_refinement")
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "raw.csv").exists():
        raise RuntimeError("Choose a new output directory")
    for sub in ("logs", "configs"):
        (directory / sub).mkdir(exist_ok=True)
    verify()
    builds = {b: json.loads((ROOT / "build/hardware_search" / b / "manifest.json").read_text())
              for b in ("baseline", "optimized")}
    for build in builds.values():
        if sha(Path(build["binary"])) != build["binary_sha256"]:
            raise RuntimeError("Binary hash mismatch")
    args.reference = list(csv.DictReader((ROOT / "results/table1_1to128MiB_3reps/raw.csv").open()))
    base = dict(sweep.cases())["native_1vc_packet128B"]
    candidates = [(f"native_1vc_packet{size}B", {**base, "packet_size": size//16}) for size in (256, 512, 1024)]
    manifest = {"builds": builds, "cpu": args.cpu, "input_mib": 1, "topology": "mesh",
                "candidates": candidates, "screen_algorithm": "ring2d",
                "reason": "Refine intermediate packet lengths after initial mesh sweep; torus explicitly disallowed and excluded",
                "rule": "Select maximum Ring2D screen speedup. If >=1.35, confirm both algorithms with 3 fresh repetitions; otherwise retain prior confirmed results and report no new threshold crossing.",
                "script_sha256": sha(Path(__file__)), "helper_sha256": sha(ROOT / "sweep_hardware.py")}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    rows, pairs = [], []
    sweep.ALGORITHMS = ("ring2d",)
    for name, settings in candidates:
        sweep.run_case(name, settings, "screen", 1, builds, args, directory, rows, pairs)
    summaries = sweep.save(directory, rows, pairs)
    best = max(summaries, key=lambda row: row["core_speedup_estimate"])
    manifest["selected_hardware"] = best["hardware"]
    manifest["confirmation_triggered"] = best["core_speedup_estimate"] >= 1.35
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    if manifest["confirmation_triggered"]:
        sweep.ALGORITHMS = ("ring1d", "ring2d")
        sweep.run_case(best["hardware"], dict(candidates)[best["hardware"]], "confirmation", 3,
                       builds, args, directory, rows, pairs)
    verify()
    for summary in sweep.save(directory, rows, pairs):
        print(json.dumps(summary), flush=True)
    print(f"Complete: {directory}", flush=True)


if __name__ == "__main__":
    main()
