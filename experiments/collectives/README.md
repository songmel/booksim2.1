# Common collective layer

`src/experiment.cpp` derives from the original TrafficManager and uses its existing
virtual `_IssuePacket`, `_RetireFlit` and `_SingleSim` hooks. Its flit generator
registers native Flits/in-flight maps without requiring a new core API. Both cores
are linked with exactly this executor, excluding their standalone `main.cpp`.

`ring1d_schedule.cpp`, `alltoall_schedule.cpp`, `ring2d_schedule.cpp` and
`tto_schedule.cpp` provide schedules through `schedule.hpp`. Hardware configuration
extensions, the rate channel and credit audits live in this directory as well.
`phase_profile.hpp` optionally samples exclusive CPU domains.

The historical source path `../allreduce/src` is a relative compatibility link
to the canonical `src/` directory here.
