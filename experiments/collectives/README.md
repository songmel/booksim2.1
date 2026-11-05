# Common collective layer

`src/experiment.cpp` derives from the original TrafficManager and uses its existing
virtual `_IssuePacket`, `_RetireFlit` and `_SingleSim` hooks. Its flit generator
registers native Flits/in-flight maps without requiring a new core API. Both cores
are linked with exactly this executor, excluding their standalone `main.cpp`.

The supported experiments use `ring1d_schedule.cpp` for AllReduce and
`alltoall_schedule.cpp` for AllToAll through `schedule.hpp`. The executor accepts
only these two workloads. Hardware configuration extensions, the rate channel,
and credit audits live in this directory as well.

The historical source path `../allreduce/src` is a relative compatibility link
to the canonical `src/` directory here.
