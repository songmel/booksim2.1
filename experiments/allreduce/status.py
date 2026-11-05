#!/usr/bin/env python3
"""Show progress and a workload-weighted remaining-time estimate."""
import argparse
import csv
import json
from pathlib import Path
import statistics

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
manifest = json.loads((args.directory / "manifest.json").read_text())
raw = args.directory / "raw.csv"
records = list(csv.DictReader(raw.open())) if raw.exists() else []
successful = [r for r in records if r["status"] == "ok"]
total = len(manifest["sizes_mib"])*len(manifest["algorithms"])*manifest["repetitions"]*2
print(f"Completed: {len(successful)}/{total} runs")
failed = [r for r in records if r["status"] != "ok"]
if failed:
    print(f"Failed: {len(failed)}; last error: {failed[-1]['error']}")
keys = {(r["algorithm"], int(r["input_mib"]), int(r["repetition"]), r["backend"]) for r in successful}
remaining = 0
covered = True
for algorithm in manifest["algorithms"]:
    for backend in ("baseline", "optimized"):
        samples = [float(r["run_seconds"])/int(r["input_mib"]) for r in successful
                   if r["algorithm"] == algorithm and r["backend"] == backend]
        if not samples:
            covered = False
            continue
        seconds_per_mib = statistics.median(samples)
        for mib in manifest["sizes_mib"]:
            for repetition in range(1, manifest["repetitions"]+1):
                if (algorithm, mib, repetition, backend) not in keys:
                    remaining += mib*seconds_per_mib
if covered:
    print(f"Estimated remaining: {remaining/3600:.2f} hours (linear payload estimate)")
summary = args.directory / "summary.csv"
if summary.exists():
    for row in csv.DictReader(summary.open()):
        print(f"{row['algorithm']:6} {row['input_mib']:>3} MiB: {float(row['speedup']):.3f}x; checks={row['all_checks_passed']}")
