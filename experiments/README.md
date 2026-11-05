# BookSim Opt experiments

These experiments compare exactly two versions on AllReduce and AllToAll:

| Version | Build ID | Core | Implementation optimizations | Compiler setup |
|---|---|---|---|---|
| BookSim 2.0 | `baseline_o3` | `booksim2/` | Original upstream implementation | `-O3` |
| BookSim Opt | `opt1to6_final` | `booksim-opt/` | OPT1–OPT6 enabled | `-O3 -march=native -flto=auto -fno-exceptions` and PGO |

Both versions use the same external collective executor and hardware model.
AllReduce uses Ring1D ReduceScatter + AllGather; AllToAll uses direct exchange.
The runner always measures both workloads and both versions.

## Requirements

Run all commands from the repository root. Requires Linux, Python 3.10+, GCC/G++,
flex, bison, and `taskset` (util-linux). Python dependencies are limited to the
standard library. The runner builds both cores directly; a separate `make` step
is unnecessary. Experiment compiler settings are independent of each core's
standalone Makefile.

## Reproduce the CSV results

```sh
python3 experiments/run_sweep.py \
  --sizes-mib 1 2 4 8 16 32 64 \
  --repetitions 3 \
  --output result/reproduced
```

This performs **84 measured runs**: seven sizes × two workloads × two versions ×
three repetitions. Measurements run serially on one pinned CPU, alternating the
version order across repetitions. The first run requires a new or empty output
directory. Execution times and speedups are measured again and may differ across
machines and runs. `result/` is ignored by Git.

A small check uses the same experiment settings and CSV format, with four
measured runs at 1 MiB/rank:

```sh
python3 experiments/run_sweep.py \
  --sizes-mib 1 --repetitions 1 --output result/quick_check
```

To resume after an interruption, repeat the original command with `--resume`:

```sh
python3 experiments/run_sweep.py \
  --sizes-mib 1 2 4 8 16 32 64 \
  --repetitions 3 \
  --output result/reproduced --resume
```

Resume requires the same sizes, repetition count, and CPU. Only validated
checkpoints are skipped; changed core sources, binaries, configuration, or PGO
feedback are rejected. Each completed measurement is saved under `records/`.
A lock prevents concurrent sweeps from using the same output directory.

`--cpu` selects an allowed logical CPU; the default is the lowest ID allowed by
the process affinity. `--jobs` controls build parallelism and defaults to 4.
Each simulation process has a timeout of `max(900, 90 * input_mib)` seconds.
Avoid other CPU-intensive work during timing measurements.

`reproduce.py` is an alias for the same runner and accepts the same options.

## Build and PGO policy

Each new output directory gets fresh source builds and profiles under `build/`.
BookSim 2.0 uses O3 without PGO. BookSim Opt enables all six implementation
switches and all compiler optimizations listed above. Both builds use C++11,
retain debug symbols with `-g`, and enable `-Wall`.

AllReduce and AllToAll each get a separate optimized executable, trained once
on that algorithm at 1 MiB/rank with the same hardware. The trained executable
is fixed across all measured sizes. Training runs are excluded from the measured
run count and timing statistics. This trains on the evaluation workloads and
sizes are swept using those profiles.

Generation and feedback-use builds keep the same object paths. Missing or
incompatible profiles are treated as build errors. Source, compiler, command,
object, executable, and PGO provenance are recorded in build manifests. Resume
reuses the saved builds after validating their sources, binaries, and profiles.

## Generated files

The completed default sweep produces eight CSV files:

| File | Contents | Rows |
|---|---|---:|
| `allreduce_runtime.csv`, `alltoall_runtime.csv` | Host execution time: median/min/max, speedup, and time reduction | 7 each |
| `allreduce_cycles.csv`, `alltoall_cycles.csv` | Simulated collective completion and credit-drain cycles for both versions | 7 each |
| `raw.csv` | Individual measured runs | 84 |
| `metrics.csv` | Timing, traffic, latency, CPU time, and throughput per workload/size/version | 28 |
| `summary.csv` | Median comparisons per workload/size | 14 |
| `pairs.csv` | Comparisons per workload/size/repetition | 42 |

CSV files update after each completed run. `measurement_complete=False` marks
rows with fewer than the requested repetitions. `booksim_opt_pgo_enabled` in the
four runtime/cycle CSVs and `pgo_enabled` in raw/metric rows record PGO use.

`manifest.json` records the settings, builds, and completion status. A completed
default sweep has `status="complete"`, `measured_runs=84`, and
`all_checks_passed=true`. `progress.json` records completed counts and the current
condition. Its ETA uses measured process seconds/MiB, with historical estimates
for groups that have no measurements yet. Estimates never supply CSV values.
`runner.log` records progress; `logs/` stores simulation output.

## Hardware and workload semantics

The default model is a 64-rank, 8×8 mesh at 1 GHz (1 ns/cycle), with 16 GB/s
links, 16-byte flits, 512-byte packets, 21 ns inter-router propagation, 1 ns
endpoint propagation, and four private VCs of 318 flits each. Admission requires
an empty VC; switch connections are held for each packet. These settings come
from `collectives/configs/mesh_16GBps_flit16B.cfg`.

Input sizes are MiB per rank. AllToAll includes the local self block in the send
buffer: at 1 MiB/rank, 1008 KiB crosses the network and 16 KiB stays local.
Direct exchange interleaves eligible peer messages at packet boundaries.
Ring1D uses 63 ReduceScatter and 63 AllGather rounds with completion dependencies.

The common executor uses native BookSim routing, arbitration, pipeline, and credit
logic, with external link adapters. It tracks packet/block provenance, contributor
identities, traffic, and credit restoration. Payload tensors are not transported
or reduced numerically in the timed model.

## Timing and validation

Primary timing is `TrafficManager::Run()` wall time, including the native
simulation, common controller, link adapters, online checks, credit drain, and
statistics output. Setup and final validation are excluded. Separate fields
record whole-process wall and CPU time.

Runs must pass native online/final checks and match simulated cycles, traffic,
retirement hashes, and latency/hop statistics across versions and repetitions.
Known 1 MiB configurations are also compared with preserved historical references.
Speedup is original time / optimized time; time reduction is
`100 * (1 - optimized time / original time)`.

## Source layout and records

```text
experiments/
  run_sweep.py         measurement entry point and CSV export
  reproduce.py         alias for run_sweep.py
  repro_build.py       original O3 and fully optimized PGO builds
  repro_common.py      fixed experiment definitions, comparisons, and helpers
  verify_layout.py     internal source inventory validation
  core_manifest.json   source hashes and upstream provenance
  collectives/
    src/               common executor, schedules, and hardware adapters
    configs/           hardware profiles
    references/        historical 1 MiB validation references
  build/               local build caches (ignored)
  results/             local measurements (ignored)
  allreduce/           historical experiment records and drivers
  legacy/              preserved earlier integrations
```

The cores contain no collective-specific algorithms or configuration extensions.
The runner automatically checks the core source inventory against
`core_manifest.json`. Intentional core changes require updating that inventory.

`allreduce/` and `legacy/` preserve earlier experiments and their recorded source
provenance. The current runner builds from the present core/common sources and
uses its own output directory.

`package.py` exports the current sources, configurations, and references without
Git metadata, binaries, caches, or historical measurement data.
