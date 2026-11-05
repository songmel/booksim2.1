# Collective experiments

The size-sweep entry point is `run_sweep.py`; `reproduce.py` supports additional
PGO comparisons. AllReduce and AllToAll use one external
executor and the same native BookSim interfaces. Neither core contains collective
algorithms, added collective configuration fields or injection/generation APIs.

```text
experiments/
  reproduce.py         build, validate and measure current sources
  run_sweep.py         checkpointed size sweep; mandatory optimized PGO
  repro_build.py       isolated builds and fresh PGO
  repro_common.py      result/reference checks and CSV helpers
  verify_layout.py     recorded core inventory validation
  core_manifest.json   source SHA and upstream provenance
  collectives/
    src/               executor, schedules, hardware adapters, CPU sampler
    configs/           explicit 16GB/s and 25GB/s hardware profiles
    references/        timing-free historical 1MiB results
  build/               generated snapshots, objects, PGO, binaries (ignored)
  results/             fresh measurements (ignored)
  allreduce/           preserved historical experiments/results
  legacy/              removed standalone integrations and build products
```

## Build variants

| Name | Core | OPT1..5 | OPT6 | Compiler setup |
|---|---|---:|---:|---|
| `baseline_o3` | booksim2 | 0 | 0 | O3 |
| `optimized_o3` | booksim-opt | 1 | 1 | O3 |
| `optimized_native` | booksim-opt | 1 | 1 | O3/native/LTO-auto/no-exceptions; no PGO |
| `baseline_final` | booksim2 | 0 | 0 | O3/native/LTO-auto/no-exceptions/PGO |
| `opt1to5_final` | booksim-opt | 1 | 0 | same full compiler setup |
| `opt1to6_final` | booksim-opt | 1 | 1 | same full compiler setup |

Every C++ unit receives all six switches explicitly. Full variants train once
per requested algorithm at 1MiB using the selected hardware. Training is excluded
from timing; generation and feedback-use builds keep the same object paths, with
missing-profile and coverage-mismatch diagnostics treated as errors. This is
training on the evaluation workload, not a held-out generalization experiment.

All active optimized measurements require PGO. The O3-only and native-only
optimized variants are retained in the low-level builder for historical and
diagnostic uses; the experiment entry points reject them as comparison targets.

These variants set compiler flags independently of the native core Makefiles.
The optimized core's own `make` now defaults to all six OPT switches and
O3/native/LTO-auto/no-exceptions, with explicit PGO generation/use modes; see
its [build instructions](../booksim-opt/README.md).

The build key hashes current core/common sources, compiler flags/version, native
CPU target and PGO input. Reusing a valid matching cache is optional; builds work
with no cache, Git installation or repository metadata. Failed builds retain logs
and a rerun uses a fresh retry directory. Relative checkout/build paths are supported.

## Run

For the 1..64 MiB sweep at powers of two, three repeats per condition, use:

```sh
python3 experiments/run_sweep.py --output /home/hwanjin/booksim-opt/result
# Continue completed checkpoints after an interruption, with identical settings:
python3 experiments/run_sweep.py --output /home/hwanjin/booksim-opt/result --resume
```

This makes 84 serial runs on one pinned CPU: BookSim2 with O3 versus BookSim-Opt
with OPT1..6 and O3/native/LTO-auto/no-exceptions/**PGO**. AllToAll and AllReduce
each use a separate optimized executable trained once on that algorithm at
1 MiB, on the same hardware. The trained executable remains fixed across all
sizes; training is excluded from the three timing repetitions. A new output
directory gets fresh source builds and profiles under its `build/` subdirectory.
The external executor uses C++11 for both variants. AllReduce uses Ring1D;
AllToAll uses direct exchange. The hardware and per-rank size semantics are below.

`alltoall_runtime.csv`, `alltoall_cycles.csv`, `allreduce_runtime.csv` and
`allreduce_cycles.csv` contain one row per size. Runtime values are host seconds,
reported as median/min/max; cycles are simulated collective completion and final
credit-drain cycles. The simulated clock is 1 ns/cycle. CPU implementation and
compiler optimizations should preserve cycles, traffic and retirement order.
`raw.csv` retains each measured run, `metrics.csv` adds traffic, latency, CPU time
and simulation throughput, and `summary.csv`/`pairs.csv` compare speedups.
`booksim_opt_pgo_enabled` in the primary CSVs and `pgo_enabled` in raw/metric rows
explicitly record the optimized setup. The runner rejects missing or changed
PGO feedback and untrained executables.

Reports update after each completed run. `measurement_complete=False` means a
row still has fewer than the requested repeats. `progress.json` records completed
counts, the current condition and a linear ETA from measured seconds/MiB. The ETA
is an estimate; it never supplies CSV measurements. `manifest.json` records full
build/source/configuration provenance and final success/failure. Each completed
run also has a durable JSON checkpoint under `records/`. A missing or mismatching
result stops the sweep; it is not silently skipped. Resume skips only validated
checkpoints and rejects changed binaries, configuration or core sources.

Each process gets `max(900, 90 * input_mib)` seconds to accommodate large sizes.
Successful checkpoints survive timeouts. A lock prevents concurrent sweeps writing
the same output. Avoid other CPU-intensive work during host-time comparisons.

From the checkout root, with Python 3.10+, GCC/G++, flex and bison installed:

```sh
python3 experiments/verify_layout.py
python3 experiments/reproduce.py --variants baseline_o3 opt1to6_final \
  --algorithms ring1d alltoall --repetitions 1 \
  --output experiments/results/check_1MiB

python3 experiments/reproduce.py \
  --variants baseline_o3 baseline_final opt1to5_final opt1to6_final \
  --algorithms alltoall --repetitions 3 \
  --output experiments/results/alltoall_1MiB_all_comparisons
```

Defaults are Ring1D and AllToAll, 1MiB/rank, three repetitions, original O3 versus
OPT1..6 with the full compiler setup. `--sizes-mib 1 2 4` changes input sizes.
`--config experiments/collectives/configs/mesh_25GBps_flit32B.cfg` selects the
original 25GB/s table model. `--cpu`, `--jobs` and `--build-root` control host
execution/build resources. `--sample` enables optional statistical CPU-domain
estimates. Ring2D and TTO remain available through explicit `--algorithms` selection.

Each output must be new. Binaries and sources stay unchanged during measurement;
variant order rotates across repetitions on one pinned CPU. A result contains
`raw.csv`, available paired comparisons/median `summary.csv`, `manifest.json`,
configuration, driver snapshots, logs and `report.md`. Single-variant checks have
no paired summary. Build manifests preserve every source/object/binary SHA,
command, generated parser and PGO training record.

## Workload and timing definitions

AllToAll's input is the entire send buffer per rank, including the local self
block. At 1MiB/64 ranks, 1008KiB/rank crosses the network and 16KiB stays local.
Direct exchange interleaves eligible peer messages at packet boundaries. Ring1D
uses 63 ReduceScatter and 63 AllGather rounds with actual completion dependencies.

The default hardware is 8x8 Mesh, 1GHz, 16GB/s rate channels, 16B flits, 512B
packets, 21ns inter-router and 1ns endpoint propagation, four 318-flit private
VCs, empty-VC admission and packet switch holding. Native IQ routing, arbitration,
pipeline and credit logic remain in the core; adapters model the explicit links.
Payload tensors are not transported or reduced numerically in the timed model.
Packet/block provenance, contributor identities, traffic and credit restoration
are validated.

Primary timing is `TrafficManager::Run()` wall time: native simulation plus the
common controller, link adapter, online checks, credit drain and statistics output.
It excludes setup and final validation. It is not whole-process time or an exact
isolated BookSim timer. Optional CPU sampling estimates separately describe native,
controller, link and output domains. Training samples never enter measured medians.

Each run passes native online/final checks. Known 1MiB profiles additionally match
preserved cycles, traffic, retirement hash and latency/hop statistics. Other sizes
still get native validation and cross-variant/run comparisons. Report speedup as
old/new time, and time reduction as 100*(1-new/old); these percentages differ.

## Source separation and historical records

`core_manifest.json` records the upstream `28f4329` source and cleaned optimized
source. `verify_layout.py` checks it and rejects collective code in core C++.
Intentional future core changes require reviewing/updating the optimized inventory.

The historical `allreduce/` runners use older snapshots/binaries and retain their
original provenance. Their old HEAD/overlay and source-preservation assumptions
are not the active reproduction workflow. A relative compatibility link preserves
their common-source path. Old performance reports remain tied to their
recorded source snapshots; cleanup validation is not a new performance claim.

`package.py` exports current sources/configs/references without `.git`,
binaries, caches, legacy standalone integrations or historical measurement data.
