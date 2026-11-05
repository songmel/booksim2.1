#!/usr/bin/env python3
"""Evaluate the system table and bandwidth/flit variants using frozen binaries."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

from compiler_pilot import write_json
from profile import ROOT, sha
from run import compare, save_csv
from sweep_hardware import config_text
from verify_sources import verify

ALGORITHMS = ("ring1d", "alltoall")
CELLS = ("baseline_o3", "baseline_final", "optimized_final")
HARDWARE = dict(experiment_flit_bytes=32, packet_size=16, num_vcs=4,
                vc_buf_size=318, experiment_link_latency_ns=21,
                experiment_bandwidth_gbps=25, experiment_native_channels=0,
                input_speedup=1, hold_switch_for_packet=1)


def save_rows(path, rows):
    if rows:
        save_csv(path, sorted({k for r in rows for k in r}), rows)


def check_hardware(row, hardware=None, input_mib=1):
    hardware = HARDWARE if hardware is None else hardware
    flit_bytes = hardware["experiment_flit_bytes"]
    expected = dict(active_ranks=64, flit_bytes=flit_bytes,
                    packet_flits=hardware["packet_size"],
                    packet_bytes=512, num_vcs=4, vc_depth=318,
                    link_latency_ns=21, bandwidth_gbps=hardware["experiment_bandwidth_gbps"], input_speedup=1,
                    hold_switch=1, network_topology="mesh", channel_model="rate",
                    input_bytes=input_mib*1048576)
    if any(str(row[k]) != str(v) for k, v in expected.items()):
        raise RuntimeError("Effective hardware does not match the requested profile")
    if row["algorithm"] == "alltoall":
        counts = dict(packets=129024*input_mib, flits=66060288*input_mib//flit_bytes,
                      link_flits=352321536*input_mib//flit_bytes,
                      network_bytes_per_rank=1032192*input_mib,
                      self_copy_bytes_per_rank=16384*input_mib,
                      peer_block_bytes=16384*input_mib)
    else:
        counts = dict(packets=258048*input_mib, flits=132120576*input_mib//flit_bytes,
                      link_flits=132120576*input_mib//flit_bytes,
                      network_bytes_per_rank=2064384*input_mib)
    if any(row[k] != v for k, v in counts.items()):
        raise RuntimeError("Independent communication accounting mismatch")


def execute(build, cell, cfg, algorithm, repetition, sampled, args, directory, reference):
    stem = f"{algorithm}_{cell}_r{repetition}_{'sampled' if sampled else 'control'}"
    command = ["taskset", "-c", str(args.cpu), build["binary"], str(cfg),
               algorithm, str(args.input_mib*1048576), "16384", "1000000000"]
    if sampled:
        command.append("--profile-us=1000")
    print(f"RUN {stem}", flush=True)
    started = time.monotonic()
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=900)
    log = directory/"logs"/f"{stem}.log"
    log.write_text(process.stdout)
    markers = [json.loads(s.removeprefix("EXPERIMENT_RESULT "))
               for s in process.stdout.splitlines() if s.startswith("EXPERIMENT_RESULT ")]
    if process.returncode or len(markers) != 1:
        raise RuntimeError(f"Experiment failed: {log}")
    row = markers[0]
    if not row["validation_passed"]:
        raise RuntimeError(f"Receive validation failed: {log}")
    check_hardware(row, args.hardware_config, args.input_mib)
    row.update(variant=cell, backend=build["backend"], repetition=repetition,
               mode="sampled" if sampled else "unsampled_control", input_mib=args.input_mib,
               cpu=args.cpu, hardware=args.hardware_name, config_sha256=sha(cfg),
               binary_sha256=build["binary_sha256"], log_file=str(log),
               process_wall_seconds=time.monotonic()-started, status="ok")
    if reference is not None and not all(compare(row, reference).values()):
        raise RuntimeError(f"Cycles/traffic/retirement/statistics changed: {log}")
    row["reference_match"] = reference is not None
    if sampled and row["profile_samples"] < 100:
        raise RuntimeError(f"Insufficient CPU samples: {log}")
    print(f"  {row.get('booksim_cpu_seconds_estimate', row['run_seconds']):.4f}s; "
          f"{row['collective_cycles']} cycles; validation passed", flush=True)
    return row


def summarize(directory, rows, repetitions):
    pairs, summaries = [], []
    for algorithm in ALGORITHMS:
        selected = [r for r in rows if r["algorithm"] == algorithm]
        for repetition in range(1, repetitions+1):
            group = {r["variant"]:r for r in selected if r["repetition"] == repetition}
            if set(group) != set(CELLS):
                continue
            for kind, base in (("combined_upgrade", "baseline_o3"),
                               ("matching_final_flags", "baseline_final")):
                b, o = group[base], group["optimized_final"]
                checks = compare(b, o)
                if not all(checks.values()):
                    raise RuntimeError("Baseline/opt comparison mismatch")
                pairs.append(dict(algorithm=algorithm, repetition=repetition, comparison=kind,
                                  **checks, collective_cycles=b["collective_cycles"],
                                  drain_cycles=b["drain_cycles"],
                                  baseline_cpu_seconds=b["booksim_cpu_seconds_estimate"],
                                  optimized_cpu_seconds=o["booksim_cpu_seconds_estimate"],
                                  speedup=b["booksim_cpu_seconds_estimate"]/o["booksim_cpu_seconds_estimate"]))
        if len(selected) != len(CELLS)*repetitions:
            continue
        summary = dict(algorithm=algorithm, input_mib=selected[0]["input_mib"], repetitions=repetitions,
                       collective_cycles=selected[0]["collective_cycles"],
                       drain_cycles=selected[0]["drain_cycles"], all_checks_passed=True)
        for key in ("flit_bytes", "packet_bytes", "packet_flits", "num_vcs", "vc_depth",
                    "link_latency_ns", "bandwidth_gbps", "channel_model", "hardware"):
            summary[key] = selected[0][key]
        for cell in CELLS:
            chosen = [r for r in selected if r["variant"] == cell]
            for field in ("booksim_cpu_seconds_estimate", "link_model_cpu_seconds_estimate",
                          "collective_controller_cpu_seconds_estimate", "run_seconds"):
                summary[f"{cell}_{field}_median"] = statistics.median(r[field] for r in chosen)
        opt = summary["optimized_final_booksim_cpu_seconds_estimate_median"]
        for kind, cell in (("combined_upgrade", "baseline_o3"),
                           ("matching_final_flags", "baseline_final")):
            baseline = summary[f"{cell}_booksim_cpu_seconds_estimate_median"]
            summary[f"{kind}_speedup"] = baseline/opt
            summary[f"{kind}_time_reduction_pct"] = 100*(1-opt/baseline)
        summaries.append(summary)
    save_rows(directory/"raw.csv", rows)
    save_rows(directory/"pairs.csv", pairs)
    save_rows(directory/"summary.csv", summaries)
    return pairs, summaries


def check_preserved(builds):
    for b in builds.values():
        if sha(Path(b["binary"])) != b["binary_sha256"]:
            raise RuntimeError("Frozen executable changed")
        for name, expected in b.get("profiles_sha256", {}).items():
            if sha(Path(b["binary"]).parent/"profiles"/name) != expected:
                raise RuntimeError("Frozen PGO profiles changed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--input-mib", type=int, default=1, help="Total input buffer per rank in MiB")
    parser.add_argument("--bandwidth-gbytes", type=int, default=25, help="Link bandwidth in GB/s")
    parser.add_argument("--flit-bytes", type=int, choices=(16, 32), default=32)
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0) or args.repetitions < 1 or not 1 <= args.bandwidth_gbytes <= 256:
        parser.error("Invalid CPU, repetition count or bandwidth")
    if not 1 <= args.input_mib <= 128:
        parser.error("Input size must be within 1..128 MiB per rank")
    if args.bandwidth_gbytes > args.flit_bytes:
        parser.error("At 1 GHz and one flit/cycle, requested bandwidth exceeds the interface capacity")
    args.hardware_config = {**HARDWARE, "experiment_flit_bytes":args.flit_bytes,
                            "packet_size":512//args.flit_bytes,
                            "experiment_bandwidth_gbps":args.bandwidth_gbytes}
    original_table = args.hardware_config == HARDWARE
    args.hardware_name = "table1_mesh_25GBps" if original_table else f"table1_mesh_{args.bandwidth_gbytes}GBps_flit{args.flit_bytes}B"
    if args.output is None:
        prefix = "table1" if original_table else args.hardware_name
        name = f"{prefix}_ring1d_alltoall_final_{args.input_mib}MiB_{args.repetitions}reps"
        args.output = ROOT/"results"/name
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = (args.output/".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (args.output/"manifest.json").exists():
        raise RuntimeError("Choose a new output directory; previous results are preserved")
    for sub in ("logs", "configs", "driver_sources"):
        (args.output/sub).mkdir()
    verify()
    previous = ROOT/"results/ring1d_alltoall_native_lto_pgo_1MiB_3reps/manifest.json"
    final = ROOT/"results/ring1d_alltoall_lto_auto_noexceptions_pgo_1MiB_3reps/manifest.json"
    old, last = json.loads(previous.read_text()), json.loads(final.read_text())
    assert old["status"] == last["status"] == "complete"
    builds = {"baseline_o3":old["o3_builds"]["baseline"],
              "baseline_final":last["compiler_builds"]["baseline"],
              "optimized_final":last["compiler_builds"]["optimized"]}
    check_preserved(builds)
    cfg = args.output/"configs"/f"{args.hardware_name}.cfg"
    text = config_text(args.hardware_config)
    # Replace the original table's explanatory serialization comment too.
    text = text.replace("Link serialization (32 B / 25 GB/s = 1.28 ns)",
                        f"Link serialization ({args.flit_bytes} B / {args.bandwidth_gbytes} GB/s = {args.flit_bytes/args.bandwidth_gbytes:g} ns)")
    cfg.write_text(text)
    historical = ROOT/"results/table1_1to128MiB_3reps/raw.csv"
    reference_rows = list(csv.DictReader(historical.open()))
    ring_reference = next((r for r in reference_rows if r["algorithm"] == "ring1d" and r["input_mib"] == str(args.input_mib)), None) if original_table else None
    references = {"ring1d":ring_reference} if ring_reference is not None else {}
    manifest = dict(status="running", algorithms=ALGORITHMS, input_mib=args.input_mib, repetitions=args.repetitions,
                    cpu=args.cpu, topology="mesh", hardware=args.hardware_name, effective_config=args.hardware_config,
                    router_clock_hz=1000000000, config_sha256=sha(cfg), builds=builds,
                    source_manifests={str(previous):sha(previous), str(final):sha(final)},
                    ring1d_historical_reference_sha256=sha(historical) if ring_reference is not None else None,
                    hardware_changes_from_table={k:{"previous":HARDWARE[k], "new":v}
                                                 for k,v in args.hardware_config.items() if HARDWARE[k]!=v},
                    binary_policy="Reuse exact archived executables; no recompilation, retraining or original core/Makefile changes.",
                    pgo_policy="Final binaries retain the earlier mixed Ring1D/AllToAll PGO trained at 1 MiB on the native mesh configuration; no hardware- or input-size-specific retraining.",
                    comparisons={"combined_upgrade":"O3 BookSim2 baseline vs final compiled BookSim-opt",
                                 "matching_final_flags":"Both backends with native/LTO-auto/no-exceptions/PGO"},
                    link_model=f"Existing external rate adapter: {args.flit_bytes} B / {args.bandwidth_gbytes} GB/s serialization, 21 ns inter-router propagation; 1 ns injection/ejection propagation. Adapter retained even at one flit/cycle for comparison with the table. Native routers run at 1 GHz. Empty-VC whole-packet admission, audited credit return.",
                    timing="BookSim CPU sampling estimates; custom rate adapter, collective controller, output excluded and recorded separately. Three-run medians; residual sampling/measurement overhead remains.",
                    flow_control=f"Conservative VCT model using wait_for_tail_credit=1 and hold_switch_for_packet=1; {512//args.flit_bytes}-flit packets fit in empty 318-flit VCs. Native router allocation and credit logic retained.")
    anchor = ROOT/"results/table1_ring1d_alltoall_final_1MiB_3reps"
    if not original_table:
        manifest["previous_hardware_directory"] = str(anchor)
        manifest["previous_hardware_manifest_sha256"] = sha(anchor/"manifest.json")
    if args.input_mib != 1:
        prefix = "table1" if original_table else args.hardware_name
        previous_input = ROOT/"results"/f"{prefix}_ring1d_alltoall_final_1MiB_3reps"
        if (previous_input/"manifest.json").exists():
            previous_input_manifest = json.loads((previous_input/"manifest.json").read_text())
            if previous_input_manifest["status"] != "complete" or previous_input_manifest["builds"] != builds or previous_input_manifest["config_sha256"] != sha(cfg):
                raise RuntimeError("The 1 MiB comparison must use identical binaries and hardware")
            manifest["previous_input_directory"] = str(previous_input)
            manifest["previous_input_manifest_sha256"] = sha(previous_input/"manifest.json")
    names = ("table_pilot.py", "compiler_pilot.py", "profile.py", "run.py", "sweep_hardware.py", "verify_sources.py")
    manifest["driver_sha256"] = {n:sha(ROOT/n) for n in names}
    for name in names:
        (args.output/"driver_sources"/name).write_bytes((ROOT/name).read_bytes())
    write_json(args.output/"manifest.json", manifest)
    rows, controls = [], []
    try:
        for algorithm in ALGORITHMS:
            for repetition in range(1, args.repetitions+1):
                offset = (repetition-1) % len(CELLS)
                order = CELLS[offset:]+CELLS[:offset]
                for cell in order:
                    row = execute(builds[cell], cell, cfg, algorithm, repetition, True,
                                  args, args.output, references.get(algorithm))
                    if algorithm not in references:
                        references[algorithm] = row.copy()
                        row["reference_match"] = True
                        row["reference_origin"] = "First validated baseline; independent traffic accounting also checked"
                    rows.append(row)
                    summarize(args.output, rows, args.repetitions)
        for algorithm in ALGORITHMS:
            for cell in CELLS:
                controls.append(execute(builds[cell], cell, cfg, algorithm, 0, False,
                                        args, args.output, references[algorithm]))
                save_rows(args.output/"controls.csv", controls)
        pairs, summaries = summarize(args.output, rows, args.repetitions)
        assert len(rows) == 2*3*args.repetitions and len(pairs) == 2*2*args.repetitions
        assert len(summaries) == 2 and len(controls) == 6
        check_preserved(builds)
        verify()
        manifest.update(status="complete", sampled_runs=len(rows), matched_pairs=len(pairs),
                        unsampled_controls=len(controls), training_runs=0, all_checks_passed=True)
        for row in summaries:
            print(json.dumps(row), flush=True)
    except BaseException as error:
        manifest.update(status="failed_or_interrupted", error=repr(error))
        raise
    finally:
        write_json(args.output/"manifest.json", manifest)
    print(f"Complete: {args.output}", flush=True)


if __name__ == "__main__":
    main()
