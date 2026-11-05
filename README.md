# BookSim Opt

BookSim Opt is an optimized version of the BookSim cycle-accurate network simulator,
based on upstream commit `28f4329`. It reduces host execution overhead through six
implementation optimizations and compiler tuning while aiming to preserve simulated
timing, traffic, and arbitration behavior.

The repository includes the original core and a shared external executor for
AllReduce and AllToAll experiments. Both cores run the same collective workloads.

## Implementation optimizations

| Switch | Optimization | Description |
|---|---|---|
| `OPT1` | Flat flit tracking | Replaces tree maps with reusable flat hash tables and avoids duplicate lookups during flit retirement. |
| `OPT2` | Reusable ejection buffers | Reuses arrays indexed by subnet and node instead of creating temporary maps every simulation cycle. |
| `OPT3` | Contiguous router queues | Uses vectors for incoming flits and directly indexed outgoing credits instead of tree maps. |
| `OPT4` | Compact credit sets | Stores sorted, unique virtual-channel IDs in reusable vectors instead of tree sets. |
| `OPT5` | Network evaluation | Skips empty channel `Evaluate()` calls while preserving router evaluation order. |
| `OPT6` | Compact allocation | Combines compact iSLIP bitmasks (up to 64 inputs and outputs), in-place request updates, and sparse request clearing. |

All six switches are enabled by default and can be controlled individually, for
example with `make -C booksim-opt/src OPT6=0`.

## Compiler optimizations

| Option | Purpose |
|---|---|
| `-O3` | Enables GCC's high-level optimization passes. |
| `-march=native` | Generates instructions tuned to the build machine's CPU. |
| `-flto=auto` | Enables link-time optimization across translation units. |
| `-fno-exceptions` | Disables C++ exception handling. |
| PGO | Uses workload profiles through `-fprofile-generate` and `-fprofile-use`. |

The standalone Makefile enables the first four options by default and retains
debug symbols with `-g`. PGO requires a separate training run and is optional for
standalone builds. Experiment runners require PGO for optimized measurements;
training is excluded from reported execution times. See the
[core build guide](booksim-opt/README.md) for PGO and portable build options.

## Repository layout

```text
booksim2/       Original BookSim core
booksim-opt/    Optimized core with OPT1–OPT6
experiments/    Shared collective executor, configurations, and measurement tools
result/         Local measurement results and figures (Git-ignored)
```

Build products, caches, `result/`, and directories beginning with `.` are ignored.
Keep the core directory names unchanged: experiment tools use their relative paths.

## Quick start

Requires Linux, Python 3.10+, GCC/G++, GNU make 4.3+, flex, and bison.
Experiment runners use only the Python standard library.

```sh
# Build the optimized standalone simulator.
make -C booksim-opt/src -j4

# Reproduce the full CSV results: 7 sizes, 3 repetitions, 84 measured runs.
python3 experiments/run_sweep.py \
  --sizes-mib 1 2 4 8 16 32 64 \
  --repetitions 3 \
  --output result/reproduced
```

The sweep builds both variants, trains a separate optimized PGO executable for
each algorithm, and compares original O3 against OPT1–OPT6 with compiler
optimizations and PGO. Choose a new or empty output directory. It generates
`allreduce_runtime.csv`, `allreduce_cycles.csv`, `alltoall_runtime.csv`,
`alltoall_cycles.csv`, `raw.csv`, `metrics.csv`, `pairs.csv`, and `summary.csv`.
Execution times and speedups are measured again and may differ across machines
and runs.

Experiments check simulated cycles, traffic, retirement order hashes, and latency
statistics across variants, with historical references for known 1 MiB workloads.
See the [experiment guide](experiments/README.md) for size sweeps, configurations,
and measurement details. Each core retains its original `LICENSE.md`.
