"""Shared validation and output helpers for portable collective experiments."""
import csv
import hashlib
import json
import os
import resource
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
COLLECTIVES = ROOT / "collectives"
RESULT_FIELDS = ("algorithm", "input_bytes", "padded_bytes", "active_ranks",
                 "collective_cycles", "drain_cycles", "packets", "flits", "link_flits",
                 "retirement_hash", "packet_latency", "network_latency", "flit_latency", "average_hops")
GENERATED = {"booksim", "lex.yy.c", "y.tab.c", "y.tab.h", ".opt_config", ".opt_config.tmp"}
GENERATED_SUFFIXES = {".o", ".d", ".pyc", ".gcda", ".gcno"}
GENERATED_DIRECTORIES = {"__pycache__", ".pgo"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(repository):
    return sorted(p for p in (repository / "src").rglob("*") if p.is_file()
                  and p.name not in GENERATED and p.suffix not in GENERATED_SUFFIXES
                  and not GENERATED_DIRECTORIES.intersection(p.parts))


def core_hashes(repository):
    return {str(p.relative_to(repository)):sha(p) for p in source_files(repository)}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def save_csv(path, rows):
    if not rows:
        return
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def check_equal(reference, actual):
    changed = [field for field in RESULT_FIELDS if reference[field] != actual[field]]
    if changed:
        raise RuntimeError("Collective result mismatch: " + ", ".join(changed))


def check_reference(row, configuration):
    for path in sorted((COLLECTIVES / "references").glob("*.json")):
        reference = json.loads(path.read_text())
        if reference["config_sha256"] != sha(configuration):
            continue
        for expected in reference["records"]:
            if (expected["algorithm"], expected["input_bytes"]) == (row["algorithm"], row["input_bytes"]):
                check_equal(expected, row)
                return path.name
    return None


def execute(binary, configuration, algorithm, input_mib, cpu, log, sampled=False, timeout=None):
    # Large workloads can legitimately exceed the old fixed 15-minute limit.
    if timeout is None:
        timeout = max(900, input_mib * 90)
    argv = ["taskset", "-c", str(cpu), str(binary), str(configuration), algorithm,
            str(input_mib * 1048576), "16384", "1000000000"]
    if sampled:
        argv.append("--profile-us=1000")
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        partial = error.stdout or b""
        log.write_text(partial.decode(errors="replace") if isinstance(partial, bytes) else partial)
        raise RuntimeError(f"Collective execution timed out after {timeout}s: {log}") from error
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    log.write_text(process.stdout)
    records = [json.loads(line[len("EXPERIMENT_RESULT "):])
               for line in process.stdout.splitlines() if line.startswith("EXPERIMENT_RESULT ")]
    if process.returncode or len(records) != 1:
        raise RuntimeError(f"Collective execution failed: {log}")
    row = records[0]
    if not row.get("validation_passed") or row["algorithm"] != algorithm or row["input_bytes"] != input_mib*1048576:
        raise RuntimeError(f"Incorrect or unvalidated workload: {log}")
    row.update(cpu=cpu, input_mib=input_mib, sampled=sampled, log_file=str(log),
               process_wall_seconds=time.monotonic()-started, command=argv,
               process_user_seconds=usage_after.ru_utime-usage_before.ru_utime,
               process_system_seconds=usage_after.ru_stime-usage_before.ru_stime,
               historical_reference=check_reference(row, configuration))
    row["process_cpu_seconds"] = row["process_user_seconds"] + row["process_system_seconds"]
    return row
