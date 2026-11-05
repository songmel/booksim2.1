# BookSim with OPT1 through OPT6

This is the upstream BookSim core (`28f4329`) with CPU-side implementation
optimizations. Collective schedules, controllers, hardware extensions and benchmark
code live in `../experiments/collectives`, outside the native simulator.

OPT1..OPT5 retain the earlier optimized implementation; OPT6 adds Compact iSLIP,
in-place request replacement and sparse Clear improvements. Source branches are
marked with `// OPTn` and guarded by `#if OPTn`.

The Makefile requires GNU make 4.3+ and GCC/G++. Plain `make` enables all six
implementation switches and `-O3 -g -march=native -flto=auto -fno-exceptions`.
Native instructions target the build machine. Optimization/LTO flags are also
passed at link time; generated C parsers do not receive C++ exception flags.

```sh
make -C src -j4
# Portable O3 build, retaining all six implementation optimizations:
make -C src -j4 NATIVE=0 LTO=0 NO_EXCEPTIONS=0
# Disable implementation optimizations, retaining default compiler optimizations:
make -C src -j4 OPT1=0 OPT2=0 OPT3=0 OPT4=0 OPT5=0 OPT6=0
```

PGO is optional because it requires workload training. Use the same source path,
compiler settings and OPT switches for generation and feedback use. Start each
new training session with a fresh `PGO_DIR`; the default is `src/.pgo`.

```sh
cd src
make -j4 PGO=generate
# Example native batch workload; replace it with representative native traffic.
./booksim examples/mesh88_lat sim_type=batch batch_size=4 batch_count=1 packet_size=4 traffic=uniform seed=17
# Stock BookSim returns 255 for a successful simulation; check its traffic stats.
make -j4 PGO=use
```

`PGO=use` fails on missing/incompatible feedback. Plain `make` uses `PGO=off`;
keep passing `PGO=use` when retaining a trained build. `make clean` preserves PGO
data, which is excluded from source inventories and release archives.

`.opt_config` tracks OPT1..OPT6, compiler/linker flags and PGO feedback contents.
Changing them rebuilds objects automatically; unchanged settings reuse objects.
`OPTFLAGS` defaults to `-O3 -g`; standard `CFLAGS`, `CXXFLAGS`, `LDFLAGS` and
`LDLIBS` are also supported.

AllReduce/AllToAll training and reproduction use the external
[experiment builder](../experiments/README.md), which sets its own compiler flags
and switches and builds outside this tree. See [OPT6](doc/opt6.md) for code details.
