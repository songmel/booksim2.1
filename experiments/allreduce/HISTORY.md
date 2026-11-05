# Collective experiments against unchanged BookSim cores

**Current scope:** Ring1D AllReduce and equal-size AllToAll, 1 MiB per rank.
`collective_pilot.sh`, `run.py` and `profile.py` default to these workloads.
Ring2D is removed from active defaults. The earlier Ring2D/TTO/1–128 MiB results below
are preserved historical measurements; they are not automatically rerun.
Topology is fixed to **mesh**. The topology follow-up was cancelled at the
user's request; its partial logs are excluded from all reported comparisons.

## OPT6 AllToAll pilot

```sh
python3 experiments/allreduce/opt6_pilot.py --cpu 0 --jobs 8 --repetitions 3 \
  --output experiments/allreduce/results/opt6_alltoall_1MiB_rerun
```

This tests **1 MiB per rank, direct AllToAll** on the latest 1 MiB hardware
profile: 8x8 mesh, 16 GB/s rate channels, 16 B flits, 512 B packets, 21 ns
inter-router propagation, four 318-flit VCs and packet switch holding. The
baseline, OPT1..OPT5 and OPT1..OPT6 variants use matching native/LTO-auto/
no-exceptions flags. Every variant gets fresh PGO from one identical sampled
AllToAll run; these three training runs are excluded from timing. This differs
from the older archived mixed-workload PGO, so comparisons use fresh executions
of all three variants rather than their historical timing numbers.

Each variant then runs three sampled and three unsampled repetitions, rotating
variant order on a single pinned CPU. Primary timing is the median unsampled
`TrafficManager::Run()` wall time, including the common controller, rate adapter,
online validation and statistics output. Sampled BookSim CPU estimates exclude
those external domains and are reported separately. Setup and final validation
remain outside both Run boundaries. Sampling estimates are not exact timers.

Builds use Git HEAD snapshots, with only the seven recorded OPT6 source/build
files overlaid on optimized cores. The two optimized candidates have identical
source hashes and explicitly differ in `-DOPT6=0/1`. Local uncommitted collective
extensions are excluded. Original working sources, archived binaries, profiles
and prior results are preserved. A fresh output directory is required.

The output includes build/source/profile manifests, commands, training and raw
CSV data, paired checks, median summaries and original logs. Every training and
timing run must match historical cycles, traffic, retirement hash and latency/
hop statistics, as well as pass the existing receive, buffer and credit audits.

The completed [OPT6 AllToAll report](results/opt6_alltoall_1MiB_16GBps_final_3reps/report.md)
records a 7.38% additional reduction in unsampled Run time over OPT1..OPT5
(18.331764 s to 16.978765 s, three-run medians). With the same compiler setup,
OPT1..OPT6 reduces time by 21.17% relative to the original core. All 21 training
and measurement runs match cycles, traffic, retirement hash and statistics.

The follow-up [combined compiler/code report](results/opt6_alltoall_1MiB_combined_compiler_3reps/report.md)
compares the original O3 binary with OPT1..OPT6 plus native/LTO/no-exceptions/PGO.
Six fresh unsampled executions give three-run medians of 23.643753 s versus
17.053037 s: **27.88% less time, 1.3865x speedup**. This is the combined upgrade;
the matching-compiler comparison above isolates a different comparison scope.
Reproduce with `opt6_compiler_total.py --output /path/to/a/new/result/directory`.

## Supplied system-table experiment

```sh
bash experiments/allreduce/table_pilot.sh
python3 experiments/allreduce/plot_table.py
```

This uses the supplied 8×8 Mesh / 1 GHz / 25 GB/s / 512 B packet /
32 B flit / 21 ns inter-router link / 4 VC / 318 flits-per-VC configuration.
The existing external rate adapter provides serialization and propagation;
empty-VC packet reservation and credit auditing implement the conservative
VCT interpretation. Injection/ejection propagation remains 1 ns, an additional
assumption absent from the supplied table. Original router pipeline and
allocator settings remain those of `hardware.cfg`.

The exact archived executables are reused: O3 BookSim2 baseline, final-flags
BookSim2 baseline, and final-flags BookSim-opt. Final flags retain
`-march=native -flto=auto -fno-exceptions` and the earlier mixed-workload PGO.
There is no recompilation or hardware-specific PGO retraining; the profiles
were trained on the earlier native-channel mesh. Both Ring1D and AllToAll
use 1 MiB/rank, three fresh runs per condition (18 sampled runs), plus six
unsampled controls. Output defaults to
`results/table1_ring1d_alltoall_final_1MiB_3reps`; choose a fresh `--output`
directory for a rerun.

`summary.csv` distinguishes the combined upgrade (O3 baseline / final opt)
from the code comparison with matching final flags (final baseline / final
opt). CPU values are statistical BookSim-domain estimates. Custom rate-adapter
and collective-controller times are separately recorded and excluded from
those estimates; unsampled total execution times include them. Existing core
sources, Makefiles, executables, PGO profiles and previous results are preserved.

To change only the bandwidth and flit size while retaining the remaining
table parameters, use the same runner:

```sh
bash experiments/allreduce/table_pilot.sh --bandwidth-gbytes 16 --flit-bytes 16
python3 experiments/allreduce/plot_table.py --input \
  experiments/allreduce/results/table1_mesh_16GBps_flit16B_ring1d_alltoall_final_1MiB_3reps
```

The packet stays 512 B, becoming 32 flits. Four VCs, 318 flits/VC and 21 ns
inter-router propagation stay unchanged. The rate adapter also stays enabled
at 16 GB/s (one 16 B flit/ns) so channel implementation is held constant.
Buffer depth is retained in flits; its byte capacity becomes 5088 B per VC.
Configuration and output names record the requested bandwidth/flit profile;
the original table results are never overwritten. The first validated O3
baseline is the cycle/retirement reference for each new hardware profile,
with independent byte/flit/link traffic accounting checked for every run.

Use `--input-mib` to change the per-rank buffer size with identical hardware,
executables and PGO. For example, the 4 MiB follow-up is:

```sh
bash experiments/allreduce/table_pilot.sh --bandwidth-gbytes 16 --flit-bytes 16 --input-mib 4
python3 experiments/allreduce/plot_table.py --input \
  experiments/allreduce/results/table1_mesh_16GBps_flit16B_ring1d_alltoall_final_4MiB_3reps
```

It still uses three runs per condition and six unsampled controls. The
AllToAll buffer includes a 64 KiB self block and 63 remote 64 KiB blocks per
rank. PGO remains the archived 1 MiB training result; the 4 MiB workload is
not added to training. The earlier 1 MiB hardware/configuration and binary
hashes are checked before starting and are retained for the size comparison.

## Current pilot: Ring1D and AllToAll

```sh
bash experiments/allreduce/collective_pilot.sh
```

This measures both workloads on the previously explored 8×8 mesh profile:
1 GHz, native 16 GB/s links, 1 ns propagation, 16 B flits, 512 B packets,
one VC with 512 flits, conservative tail-credit reuse. This is an exploratory
mesh profile, **not** the proposed calibrated data-center Clos model.
There are three sampled runs per workload/backend (12 runs) plus four
unsampled controls. Output defaults to `results/ring1d_alltoall_1MiB_mesh_3reps`;
an existing measurement directory cannot be overwritten. Pass `--hardware table`
and a fresh `--output` to explicitly select the original table profile instead.

AllToAll uses direct exchange: all 63 remote peer messages per source are
initially eligible, served packet-by-packet in round-robin order. It is not
a serialized pairwise-round algorithm or a model of a specific NCCL kernel.
The input is the **entire 1 MiB send buffer per rank**, equally split into
64 destination blocks of 16 KiB. The self block is local (zero simulated copy
cost); **1008 KiB/rank crosses the network**, or 63 MiB globally. With 512 B
packets this is 129024 packets, 4128768 flits for the native profile.
The receiver places each block in its source-indexed slot, verifies unique
source/destination provenance, and checks every packet index and endpoint/link
count. This models communication and block provenance, not tensor arithmetic.
The definition follows [MPI AllToAll](https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/node109.htm).

The same injector, receiver, timing domains and hardware serve both workloads.
AllToAll has different traffic from AllReduce; reported speedups compare the
two simulator backends **within each workload**, not algorithm efficiency.
Ring1D's cycles, traffic, receive hash and statistics must also match the prior
mesh (or explicitly selected table) reference. Raw/pair/summary/control CSVs,
logs, configuration, binary/core-object hashes and driver snapshots are saved.

Builds go to `build/current_workloads`; original core objects are reused exactly,
while all external schedule units are recompiled with their shared header.
Historical binaries remain intact. Standalone `run.py` builds use
`build/current_standalone`; the table profiler uses `build/collective_profile`.

Validate the new schedule independently:

```sh
g++ -O2 -std=c++11 -Iexperiments/allreduce/src \
  experiments/allreduce/tests/alltoall_test.cpp \
  experiments/allreduce/src/alltoall_schedule.cpp -o /tmp/booksim_alltoall_test
/tmp/booksim_alltoall_test
```

The oracle checks distinct source/destination data in shuffled delivery order,
traffic volume, and rejection of missing, duplicated or misplaced blocks.

## Compiler optimization pilot

The latest compiler follow-up adds `-fno-exceptions` and changes LTO to
`-flto=auto`, retaining native targeting and newly trained PGO:

```sh
bash experiments/allreduce/noexceptions_pilot.sh
```

It measures the previous native/LTO=4/PGO executables again as controls, and
compares them against the new flags on both code versions: 24 sampled runs
(two workloads × two backends × two flag sets × three repetitions), four
excluded PGO training runs and four unsampled controls. Hardware and input
sizes remain the same. Outputs use a fresh directory under
`results/ring1d_alltoall_lto_auto_noexceptions_pgo_1MiB_3reps`.

The native simulator core contains no explicit exception handlers and stays
byte-for-byte unchanged. The external experiment's validation now uses a
typed `Fail` helper: normal exception-enabled builds still throw, while
exception-disabled builds print `EXPERIMENT_ERROR` and exit with code 1.
The main catch is conditional, and checked `strtol` parsing replaces `stoi`.
Validation predicates and successful schedules are retained. Every C++ unit
and the final link receive `-fno-exceptions`; C parser units do not require it.
Ten final-executable checks verify malformed arguments and invalid settings
are still rejected. The previous control uses its archived pre-adaptation
wrapper; the measured delta therefore includes the required wrapper
compatibility changes as well as the flags.

PGO is retrained from scratch with the new flags, using the same two valid
workloads. `-flto=auto` chooses link-time build parallelism; the simulator
continues to run on one pinned CPU. It does not itself guarantee a runtime
speedup. `compiler_comparison.csv` reports the new vs previous CPU estimates
and code speedup under matching flags. Plot with:

```sh
python3 experiments/allreduce/plot_compiler.py \
  --input experiments/allreduce/results/ring1d_alltoall_lto_auto_noexceptions_pgo_1MiB_3reps
```

The earlier three-option compiler pilot remains reproducible separately:

```sh
bash experiments/allreduce/compiler_pilot.sh
```

This keeps the current Ring1D/direct-AllToAll source snapshots and mesh hardware
unchanged, and compares the existing O3 executables against newly rebuilt
**`-O3 -march=native -flto=4 -fprofile-use=...`** executables. Both baseline and
optimized receive the same compiler options; their OPT1–OPT5 switches remain
zero and one, respectively. Native simulator sources and original Makefiles
are never patched. Build artifacts go to a separate
`build/compiler_optimizations/<output-name>/` directory.

For each backend, all native and external units first compile and link with
`-fprofile-generate=...`, native ISA targeting and LTO. Ring1D and AllToAll each
run once at 1 MiB with the same CPU-domain sampling paths used in evaluation.
These **training runs are excluded from timing summaries**. Their profiles are
merged into one common mixed-workload profile per backend. All units then
recompile and relink using `-fprofile-use=...`, with missing-profile and
coverage-mismatch diagnostics treated as errors. The object/executable paths
remain identical between stages so GCC can resolve feedback. Recursive profile
paths, raw feedback, execution-count dumps and their hashes are retained.

The 24 measured runs are 2 workloads × 2 backends × 2 compiler variants × 3
fresh repetitions, with counterbalanced ordering on one pinned CPU. Four
unsampled controls use the final compiled executables. Every run must match
the earlier workload's cycles, traffic, retirement hash and latency statistics.
Training uses the evaluation workloads and sizes; this experiment makes no
claim about performance on held-out workloads.

`compiler_comparison.csv` distinguishes code speedup under identical options,
compiler gains within each code version, and the combined upgrade from O3
baseline to the compiled optimized version. `raw.csv`, `pairs.csv`,
`summary.csv`, `training.csv`, `controls.csv`, build manifests and logs preserve
the evidence. `plot_compiler.py` renders the result after completion. Existing
outputs cannot be overwritten; `--resume` supports an interrupted build before
timing measurements begin.

## Historical hardware sensitivity search

The following commands reproduce earlier Ring1D/Ring2D investigations; they
are not the current workload defaults.

```sh
python3 experiments/allreduce/sweep_hardware.py
```

This command screens six predeclared hardware settings at 1 MiB for both ring
algorithms, then selects a common configuration by the smaller of its two
speedups. It measures that selection in **three new repetitions**, excluding
screening samples from confirmation medians. If none of the initial conditions
reaches 1.35× on both algorithms, the two predeclared higher-latency settings
are also screened. The selection rule and complete candidate lists are saved
before the first run. All explored settings, cycles, correctness checks and
timings are retained, including those below the target. This is exploratory
hardware sensitivity, not a claim that all hardware achieves the best speedup.

The table profile retains its external 25 GB/s channels. `native_*` profiles
use the original unmodified BookSim `FlitChannel` and credit channels, with
integer-cycle latency. Their bandwidth is flit bytes × 1 GHz (e.g. 16 GB/s for
16 B flits), explicitly recorded; they do not claim to match the 25 GB/s table.
Native channel work remains in the BookSim CPU domain. Custom rate-channel
work remains in its separate domain. Thus native and rate profiles are
different network models, not interchangeable absolute runtime baselines.

The controlled packet-size comparison uses 16 B flits, one VC, 512 flits/VC,
1 ns channel delay, input speedup 1 and switch holding enabled, changing only
128 B / 4 KiB / 8 KiB packets. The logical payload and 64 contributing ranks
are unchanged. Both backends have identical settings within every pair. Core
objects are reused without edits; only the external experiment is rebuilt into
`build/hardware_search`. Original source and existing result hashes are retained.

35% faster means **1.35× throughput**, equivalent to about 25.93% less time.
The CSV also records whether runtime itself falls by at least 35% (≥1.53846×).
All core-time values remain CPU sampling estimates, as described below.

`refine_mesh.py` is a separate adaptive follow-up. It screens Ring2D at
256 B / 512 B / 1 KiB packets with the remaining parameters fixed to the
one-VC mesh profile. If its best screen exceeds 1.35×, it confirms that setting
on both ring algorithms with three fresh repetitions. Screening and confirmation
remain separate in the CSV. No topology changes or TTO workloads are run.
`plot_hardware.py` combines only the two authorized mesh datasets; cancelled
topology-search data are explicitly excluded.

## Exclusive CPU-time pilot (1 MiB)

`python3 experiments/allreduce/profile.py` runs Ring1D and AllToAll at 1 MiB,
three sampled repetitions per backend (12 runs). Four unsampled controls from
the same binaries and two Ring1D controls from the original sweep binaries accompany
them. The original sweep, binaries and simulator working trees are preserved.
Results go to `results/ring1d_alltoall_1MiB_phase_profile_3reps/` by default; choose a
new `--output` directory to repeat the pilot. This command does not start the
1–128 MiB sweep.

The profile build reuses the sweep's immutable original core objects and
recompiles the external experiment and schedules in `build/collective_profile`.
Every reused object, source snapshot and new binary is hashed in the manifest.

The pilot marks exclusive execution domains and uses Linux `ITIMER_PROF` to
sample process CPU time. No clock is read on each flit/channel callback.
`booksim_cpu_seconds_estimate` is measured process CPU time multiplied by the
fraction of samples in the BookSim domain. It is a **statistical estimate**, not
an exact stopwatch measurement or an unmodified standalone BookSim runtime.
The kernel can round the requested sampling interval; observed CPU time per
sample is saved alongside the requested interval.

The BookSim domain includes native simulation, native statistics accounting,
and native-equivalent flit creation/in-flight-map registration. Calls to the
native retirement method remain in this domain, preserving the work targeted
by OPT1. Collective dependencies, packet metadata, contributor/provenance checks and
receive validation belong to the controller domain. Custom channel work
(serialization, transit queue, reservation and credit audits) belongs to the
external-link domain; the native `FlitChannel::Send` and credit-channel method
stay in the BookSim domain. `DisplayStats`/`DisplayOverallStats` have a separate
output domain. Setup and final validation are outside the profile.

The physical model is unchanged from the table experiment: both backends use
the same external 25 GB/s link model. Excluding its CPU cost does not replace
the model with stock BookSim channels, and does not remove its simulated delay.

Raw sample counts, separate CPU estimates, total runtime, off/on controls and
matching cycles/traffic/retirement hashes/statistics against the existing sweep
are saved to CSV. The primary speedup is baseline median BookSim CPU estimate /
optimized median BookSim CPU estimate. Wilson intervals in the summary describe
sample-count uncertainty of the pooled domain fraction only; they exclude phase
tagging overhead, correlation and system drift. Control runtime changes are
observations, not a universal correction to subtract from the estimates.

Validate the accounting and summary selection independently:

```sh
g++ -O2 -std=c++11 -Iexperiments/allreduce/src \
  experiments/allreduce/tests/phase_profile_test.cpp -o /tmp/booksim_phase_test
/tmp/booksim_phase_test
python3 experiments/allreduce/tests/test_profile.py
```

This directory is the common experiment layer for the original BookSim 2.0
(`../../booksim2`) and BookSim-opt (`../../booksim-opt`). Neither simulator's
source, headers, Makefile, working-tree changes, nor original build products
are edited by these experiments.

The earlier completed sweep was **Ring1D, Ring2D, TTO × 1, 2, 4, 8, 16, 32, 64, 128 MiB
per contributing rank × baseline/optimized × 3 repetitions = 144 runs**.
These are network-only, dependency-driven AllReduce workloads. They do not
model a training iteration or make claims about gradient computation speed.

## Run and resume

From any directory:

```sh
sh /home/hwanjin/booksim-opt/experiments/allreduce/run.sh \
  --output /home/hwanjin/booksim-opt/experiments/allreduce/results/ring1d_ring2d_1MiB_3reps
```

The first invocation builds the two binaries outside the original trees.
Repeated invocations reuse matching binaries. The same command resumes the
same output directory, skipping already successful runs. A changed build,
hardware configuration, stripe size, or measurement plan requires a new output
directory. A failed run is retained in the CSV and retried on resume.

The experiment is serial, with every child pinned to one CPU (the lowest
available CPU by default). Baseline/optimized order alternates across
repetitions. Do not run another CPU-intensive experiment on that CPU while
measuring. Runtime grows with message size; the initial 1 MiB measurements on
this machine predict roughly 13 hours for the full sweep, not a completion
guarantee.

```sh
# Progress and a payload-weighted estimate of remaining time:
python3 /home/hwanjin/booksim-opt/experiments/allreduce/status.py \
  /home/hwanjin/booksim-opt/experiments/allreduce/results/table1_1to128MiB_3reps

# A separate small pilot, without rebuilding:
sh /home/hwanjin/booksim-opt/experiments/allreduce/run.sh \
  --no-build --sizes-mib 1 --repetitions 1 \
  --output /home/hwanjin/booksim-opt/experiments/allreduce/results/pilot

# Select one workload through the same interface:
sh /home/hwanjin/booksim-opt/experiments/allreduce/run.sh \
  --algorithms tto --stripe-bytes 16384 --cpu 0 \
  --output /home/hwanjin/booksim-opt/experiments/allreduce/results/tto_only
```

One native process can also be run directly, after building:

```sh
experiments/allreduce/build/baseline/collective \
  experiments/allreduce/hardware.cfg ring1d 1048576 16384 1000000000
```

Unlike the legacy executable's exit convention, the external driver returns
**0 on success, nonzero on failure**, and prints exactly one validated
`EXPERIMENT_RESULT` JSON record. The Python runner requires both. Mismatched
cycles, traffic, retirement checksums, or statistics stop the benchmark after
persisting its results and logs.

## Hardware mapping and explicit assumptions

| Table parameter | Experiment model |
| --- | --- |
| Node PEs: 8×8 | Recorded as node compute metadata; compute is outside this communication-only experiment |
| Node/router clock: 1 GHz | One native BookSim cycle is 1 ns; all gradients ready at cycle 0 |
| Network topology: Mesh | 8×8 routers, one communicating endpoint/injection port per router |
| Bandwidth: 25 GB/s | 25,000,000,000 bytes/s per unidirectional link, including the endpoint links |
| Packet: 512 B | 16 × 32 B flits, no extra charged header flit |
| Flit: 32 B | One flit takes 1.28 ns to serialize |
| Channel latency: 21 ns | 21 ns inter-router propagation, in addition to serialization |
| Flow control: Virtual Cut-Through | Conservative empty-VC packet admission, packet switch holding, tail-credit VC reuse; every packet reservation is checked |
| VCs: 4 | Four private VCs per input port |
| VC depth: 318 flits | 318 × 32 B = 10,176 B per VC |

The table does not specify network size separately from PE count, endpoint
port count, packet-header accounting, router pipeline, VC reuse policy, or
whether 21 ns includes serialization. The choices above are explicit model
assumptions. The 8×8 *communication mesh* retains the existing experiment's
rank mapping; it is distinct from an 8×8 compute PE array inside a node.
`hardware.cfg` records the other router delays and allocator choices.

**Bandwidth and delay are separate.** A 32 B flit at 1 GHz with one flit/cycle
would provide 32 GB/s, not 25 GB/s. The external `RateChannel` serializes flits
using exact 40 ps ticks (32 ticks/flit, 25 ticks/ns), then adds the propagation
delay. The native 1 GHz receiver samples arrivals on integer clock edges.
For an otherwise idle 21 ns link, a flit accepted at cycle 0 is fully sampled
at cycle 23: ceil(1.28 + 21) ns. This sampling is intentional, not an extra
native cycle loop or a change of the router frequency. Endpoint propagation
is 1 ns because the table does not specify the local NI delay. Reverse credit
propagation uses the corresponding link delay and the native credit model.

**VCT policy.** The original IQ router and its optimizations remain unchanged.
With `wait_for_tail_credit=1`, a new packet gets an empty downstream VC;
its 318 slots can hold all 16 flits. `hold_switch_for_packet=1` retains the
switch grant for the packet. External channel audits require zero outstanding
credits at each head admission and at most one 16-flit packet per VC before
reuse. Consequently an admitted packet cannot be blocked for lack of its
reserved downstream buffer space. This is a conservative VCT policy with
one packet per allocated VC; it does **not** implement packing multiple packets
into the same VC. This choice keeps the native IQ path (including OPT3) in the
benchmark. Simply writing `vct=1` in an IQ configuration would not enable VCT;
BookSim's separate event router has a different credit/buffer interpretation.

No floating-point gradients are transported. Online checks track contributor
masks and packet identities; independent schedule tests also check numerical
sums and zero padding. Reduction, compute, and memory latency are zero.

## Algorithm definitions

All three schedules use `schedule.hpp` and the same `Collective` executor.
Scheduling remains dependent on actual send and receive completion, never
fixed injection timestamps. Packet-boundary round robin shares the existing
single injection/ejection port. Forwarding waits for whole-message reception.

* `ring1d_schedule.cpp`: the original adjacent-node Hamiltonian ring, 64 ranks,
  63 ReduceScatter and 63 AllGather rounds, per-rank send/receive dependencies.
* `ring2d_schedule.cpp`: concurrent tensor halves; RS-X → RS-Y → AG-Y → AG-X
  and the dimension-swapped sequence. Logical ring closure takes seven
  physical mesh hops rather than a torus wrap link. There are 64 contributors.
* `tto_schedule.cpp`: the HPCA 2024 reference's three trees, roots 0, 7, 63,
  staircase middle tree, per-edge receive-completion FIFO, global RS→AG gate,
  16 KiB stripes and three-full-stripe wave padding. Node 56 relays traffic but
  contributes no gradient, leaving **63 contributors**. The default 128 MiB
  input needs 8,193 stripes; it is supported without the legacy 8,192 limit.

TTO's participant count and padding are stored in each CSV row. These workloads
are intended to measure baseline-vs-optimized simulator speed for each
algorithm. Their raw completion times alone do not establish a fair ranking
of algorithms with identical participants, or reproduce the TTO paper's
multi-interface accelerator hardware.

## Measurement and output

`run_seconds` uses a C++ monotonic clock around the original
`TrafficManager::Run()` call. It includes native simulation, the common
collective callbacks/channel adapters, online correctness checks, credit
drain, and native statistics output. It excludes build, schedule/network
construction, process startup, and the final validation pass. It is an
**experiment execution time**, not a claim of isolated network-kernel time.
Setup, collective-only loop, final validation, process wall time, user/system
CPU time and peak RSS are also reported separately.

Each output directory contains:

* `raw.csv`: 144 successful rows for a complete default sweep, one per process;
  times, input/padded bytes, participating ranks, cycles, traffic, checksums,
  statistics, commit IDs, OPT flags, CPU affinity, validation/pair results.
* `pairs.csv`: 72 repetition pairs, baseline and optimized cycles/times,
  comparison results, and paired runtime speedup.
* `summary.csv`: 24 algorithm/size rows, median/min/max runtime, median-ratio
  speedup, cycles/s, and overall comparison status; emitted after all three
  repetitions of each condition finish.
* `manifest.json`: exact commits, SHA-256 hashes of original sources and
  binaries, experiment sources, compiler/flags, hardware assumptions and plan.
* `logs/`: the original simulator output and per-process resource usage.

Files are saved atomically after **every process**, including failures. Resume
keeps the original measurements. Both `collective_cycles` (all data received)
and `drain_cycles` (native final credit drain completed) must match. Packet and
flit totals, per-link traffic, retirement order/timing hash, and latency/hop
statistics are checked in addition to the contributor validation.

## Source isolation and reproducibility

`build.py` reads each repository's committed **HEAD**, materializes a byte-for-
byte snapshot under `build/{baseline,optimized}/source`, and uses the same
compiler and optimization level for both. It excludes the original `main.cpp`
and links the same external experiment sources against each core. Baseline
uses the original BookSim 2.0 commit, not the already modified working-tree
collective hooks. Uncommitted simulator edits are preserved and excluded from
the measured binaries. BookSim-opt enables **OPT1–OPT5**, independently of its
working-tree Makefile defaults; the exact flags are in the manifest.

The only hooks used are the original virtual `_IssuePacket`, `_RetireFlit`,
and `_SingleSim`, plus existing `Network`/channel interfaces. Native
`TrafficManager::_Step`, `Network::ReadInputs/Evaluate/WriteOutputs`, routing,
IQ router, allocators, VC buffers, credits, and optimization implementations
are compiled without edits. Original source hashes are checked after building.
`verify_sources.py` also checks both working trees and Makefiles against their
state before creating this experiment layer, including preexisting edits.

```sh
python3 experiments/allreduce/build.py
python3 experiments/allreduce/tests/check.py
python3 experiments/allreduce/verify_sources.py

# Optional comparison with the original author's unchanged local checkout:
python3 experiments/allreduce/tests/compare_tto_reference.py \
  --upstream /path/to/TTO_HPCA_2024
```

The reference audit pins commit `156128c51d96bedebdc28315a974e8ae9fe2e502`
and the schedule source SHA-256. It compares message identities, sizes,
receive dependencies, per-link FIFO and phase gates with 512 B messages.
Validation logs from development are in `validation/`.

## Report figures

`plot.py` reads the completed CSV matrix, checks summary medians and speedups
against every raw measurement, and creates four figures: an overview, speedup,
runtime, and cycle accuracy. Each is exported as 240-dpi PNG, vector PDF, and
editable SVG under the result directory's `plots/`. The four-page
`booksim_allreduce_report.pdf` collects them in one file. CSVs and benchmark
binaries are not changed.

With Matplotlib and NumPy installed (versions in `plot-requirements.txt`):

```sh
python3 experiments/allreduce/plot.py \
  experiments/allreduce/results/table1_1to128MiB_3reps
```

Or use an isolated environment through the already available `uv`:

```sh
uv run --no-project --with matplotlib==3.10.9 --with numpy==2.2.6 \
  python experiments/allreduce/plot.py
```

Speedup points use baseline median / optimized median. Shaded ranges use
baseline min / optimized max and baseline max / optimized min; they describe
the observed runs, not confidence intervals. Runtime error bars show min/max
around each three-run median. The overview's aggregate speedups are geometric
means across the eight input sizes, with each size weighted equally.
Accuracy cells show maximum absolute cycle differences over the repetitions,
separately for data completion and final credit drain. `plot_metadata.json`
records input hashes, plotting-library versions and the derived statistics.
