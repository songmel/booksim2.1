# 8×8 mesh: 2D Ring and TTO AllReduce experiments

Both repositories (`booksim-opt` and the `booksim2` reference) contain the same
schedules, packet executor, configurations, and scripts. No GPU, Accel-Sim,
SCALE-Sim, Python binding, or external schedule files are needed to run them.

From either repository root:

```sh
sh utils/run_ring2d_allreduce.sh
sh utils/run_tto_allreduce.sh
```

Each script builds its own repository and runs exactly one collective, with
1 MiB of input per participating rank by default. Build output goes to stderr;
BookSim output goes to stdout. As with the existing 1D script, the simulator's
exit status is preserved: **255 means success in this checkout**. A fatal error
may also return 255, so automated checks must additionally require the final
statistics and `Collective completion` output. A build failure stops the script.

The new scripts pass arguments through as BookSim configuration overrides:

```sh
sh utils/run_ring2d_allreduce.sh allreduce_bytes=16384 > ring2d.log
sh utils/run_tto_allreduce.sh allreduce_bytes=1048576 allreduce_chunk_bytes=4096 > tto.log
```

Configurations are `src/examples/mesh88_ring2d_allreduce` and
`src/examples/mesh88_tto_allreduce`. `sim_type = allreduce` selects the finite
collective executor; `allreduce_algorithm` selects `ring2d` or `tto`. Its default
is `ring1d`, so the existing 1D experiment and its algorithm are unchanged.

## Shared hardware and timing model

All experiments use the existing 8×8 mesh / IQ router / DOR model, one rank
endpoint per router, 8 VCs with private 8-flit buffers, 16-byte flits, and
8-flit (128-byte) packets. Existing router/credit delays match the 1D example.
Each node shares its **one existing injection/ejection port** among all flows;
ready messages take turns at packet boundaries. No extra bandwidth or ports
are introduced for either algorithm. This endpoint limit can constrain the
speedup from TTO's parallel trees compared with a multi-port accelerator model.

All input gradients are ready at cycle 0. Local aggregation has zero compute
and memory delay and is represented by contributor masks. No extra header
flits are charged. These are network-only collective models, not complete
NCCL kernels or full training iterations, and do not reproduce every hardware
setting of the source paper.

Message dependencies are released by **actual** last-flit injection / full
message reception. There are no assumed hop times or pre-recorded injection
timestamps. Intermediate payload forwarding waits for an entire message,
as in the existing 1D experiment; packet-level cut-through aggregation is
not modeled. The network itself still pipelines actual flits normally.

## 2D Ring schedule

Implemented independently in `src/ring2d_schedule.cpp`, following Ring-2D in
[Laskar et al., HPCA 2024, section II-B](https://ksungkeun84.github.io/publications/ringbiodd_tto.pdf#page=3).
It is the dimension-wise Ring-2D described there, not the later SuperMesh
artifact's `Alternative2dRingAllreduce` variant.

The input is split into two equal halves which run concurrently:

```text
A: ReduceScatter X → ReduceScatter Y → AllGather Y → AllGather X
B: ReduceScatter Y → ReduceScatter X → AllGather X → AllGather Y
```

Nodes use row-major physical coordinates `node = x + 8*y`. Each row/column
forms a logical, positive-direction ring of length 8. The 7→0 closing transfer
uses **seven physical mesh links**, rather than adding a torus wraparound link.
There are seven rounds per stage, with local send/receive dependencies and
no global round/stage barrier. Every one of the 64 ranks contributes and
receives the complete result.

The tensor has 128 elementary slices (two halves × eight first-dimension
shards × eight second-dimension shards). First-dimension messages cover eight
slices, second-dimension messages cover one. `allreduce_bytes` must therefore
be a positive multiple of **16,384 bytes**. No padding is injected.

## TTO schedule

Implemented independently in `src/tto_schedule.cpp`, following the three-tree
construction and chunk overlap in
[Laskar et al., HPCA 2024, section V-B/C](https://ksungkeun84.github.io/publications/ringbiodd_tto.pdf#page=7).
The construction uses the paper's row-major numbering translated to zero-based
IDs. AllGather trees have roots **0, 7, 63**. The bottom-left corner **56 does
not contribute a gradient**: there are 63 participating ranks, not 64. Node 56
still relays data in the first/last trees. The third tree excludes it.

The root-0 tree traverses Y then X; the root-63 tree traverses X then Y.
The root-7 tree uses the original artifact's deterministic staircase
construction (a generic BFS gives different parents on meshes larger than 3×3).
The three outward
trees share no directed edges; ReduceScatter reverses their edges. Reductions
take place at the simulated endpoints after receiving all child messages.

Tensor stripes of `allreduce_chunk_bytes` bytes rotate
across the three trees. Every wave contains three complete stripes, following
the author's HPCA 2024 implementation at commit
[`156128c51d96bedebdc28315a974e8ae9fe2e502`](https://github.com/sabujlaskar/TTO_HPCA_2024/tree/156128c51d96bedebdc28315a974e8ae9fe2e502).
Multiple stripes
pipeline bottom-up within ReduceScatter, and top-down within AllGather, with
FIFO dependencies on each tree edge: the next stripe waits until the previous
stripe has been **received**, as enforced by `hmc_fermat.py` in the artifact.
There is **one collective RS→AG barrier**,
as in the source schedule, but no global barrier between successive chunks.
The tensor is zero-padded to a multiple of three stripes. The padded tensor size
per rank is `ceil(allreduce_bytes / (3*allreduce_chunk_bytes)) *
3*allreduce_chunk_bytes`. For the default 1 MiB logical input and 16 KiB stripe,
22 waves / 66 stripes represent 1,081,344 bytes per rank (32,768 bytes padding).
This matches the reference's full-wave rounding, with its message size adapted
to this experiment's 128-byte packets. Padding is included in traffic counters.

Both input size and stripe size must be positive multiples of **128 bytes**.
The default stripe is 16 KiB; at most 8,192 stripes are supported per run.
Changing stripe size changes the pipeline granularity. `allreduce_bytes`
always means the complete input size **per contributing rank**.

## Statistics and correctness checks

Additional output reports the algorithm, active rank count, excluded node,
logical and padded input sizes, stripe count, transmitted packets/flits, and
`Collective completion = ... cycles`. This is when all collective data has
arrived. The existing `Time taken is ... cycles` includes the final credit
drain. `Total run time` measures host seconds, not simulated cycles. Packet
source queue latency includes waiting from message readiness; BookSim's hop
statistic includes ejection, so one physical link reports two hops.

The executor checks contributor uniqueness/completeness, packet uniqueness,
per-node sent/received totals, and **every directed channel's expected flit
count**, including multi-hop ring closure and zero use of mesh wraparound
channels. A cycle budget and a signed flit-ID bound reject oversized runs.

For a speed comparison, supply the same input and schedule settings to both
repositories and compare results before comparing host time. In `booksim-opt`,
the existing Makefile currently defaults to OPT1 on and OPT2–OPT5 off. Explicit
environment variables control the build without modifying that Makefile:

```sh
OPT1=1 OPT2=1 OPT3=1 OPT4=1 OPT5=1 sh utils/run_tto_allreduce.sh
```

## Validation

Run numeric graph tests (no simulator build required):

```sh
g++ -std=c++11 -Wall -Wextra -O2 -Isrc tests/allreduce_schedule_test.cpp \
    src/ring2d_schedule.cpp src/tto_schedule.cpp -o /tmp/allreduce_schedule_test
/tmp/allreduce_schedule_test
```

They execute 270 cases with randomly reordered send/receive events and numeric
payloads varying by both rank and tensor slice, including zero-padded TTO waves,
small/even/odd meshes, and invalid sizes. The runnable experiments themselves
intentionally retain the existing fixed 8×8 hardware model.

From `booksim-opt`, after building both repositories, run network cases and compare final
statistics (including completion cycles) with the reference:

```sh
python3 tests/test_collective_experiments.py src/booksim \
    --reference ../booksim2/src/booksim
```

For the differential audit against the author's original Python schedule
generator, see [tto_reference_audit.md](tto_reference_audit.md). The logical
message schedules match the pinned reference; the endpoint count, port model,
packet format, and zero-cost reduction settings remain the shared hardware
model above, so this is not a reproduction of the paper's end-to-end cycles.
