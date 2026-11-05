# 8×8 Mesh / 1D Ring AllReduce

For the separate 2D Ring and TTO experiments, see
[collective_allreduce.md](collective_allreduce.md).

Run the single experiment from the repository root:

```sh
sh utils/run_ring_allreduce.sh
```

The script builds BookSim and executes one collective. Build messages go to
stderr; BookSim's existing stdout is passed through directly, including the
configuration, `Time taken is`, overall traffic statistics and `Total run time`.
BookSim's original exit status is also preserved (255 on success in this checkout).

To save that output, use normal shell redirection:

```sh
sh utils/run_ring_allreduce.sh > run.log
```

## Input size

Change `allreduce_bytes` in `src/examples/mesh88_ring_allreduce`:

```text
allreduce_bytes = 1048576;  // 1 MiB per rank
```

Input bytes must be a positive multiple of 8,192 so each of the 64 chunks divides
into full 128-byte packets. The same script still executes one experiment.

## Experiment

| Item | Setting |
| --- | --- |
| Network | 8×8 mesh, 64 ranks, one rank per router |
| Ring | Unidirectional; every successor, including 63→0, is physically adjacent |
| Default input per rank | 1 MiB = 1,048,576 bytes |
| Chunk at default input | 16 KiB = 16,384 bytes |
| Steps | 63 ReduceScatter + 63 AllGather |
| Flit / packet | 16 bytes / 8 flits = 128 bytes |
| Packets per chunk at default input | Exactly 128 full packets, zero padding |
| Flow control | Credit-based, 8 VCs, 8-flit private buffer per VC |
| Routing / router | DOR / IQ, using the timings in `src/examples/mesh88_ring_allreduce` |

Each rank sends its current chunk while receiving from its predecessor. It
advances after its send is fully injected and its entire incoming chunk has
arrived. All ranks begin at cycle 0. Reduction is represented by contributor
masks with zero compute/memory delay; no extra header flits are charged.
The existing mesh, router and credit models move the actual simulated flits.

The traffic manager silently checks contributor masks, destinations, chunk IDs,
packet uniqueness, sent/received counts and physical-link totals. Statistics
are collected and printed by the existing BookSim `TrafficManager`.

`Time taken is` includes the final credit drain. Packet latency starts when its
chunk becomes ready; source queue latency includes waiting behind earlier
packets in the same chunk. BookSim's hop counter counts router traversals,
including ejection, so a one-link transfer reports two hops in these statistics.
Clock frequency is unspecified; times are in cycles.
