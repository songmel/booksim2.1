BookSim Interconnection Network Simulator
=========================================

BookSim is a cycle-accurate interconnection network simulator.
Originally developed for and introduced with the [Principles and Practices of Interconnection Networks](http://cva.stanford.edu/books/ppin/) book, its functionality has since been continuously extended.
The current major release, BookSim 2.0, supports a wide range of topologies such as mesh, torus and flattened butterfly networks, provides diverse routing algorithms and includes numerous options for customizing the network's router microarchitecture.

This checkout also includes a finite, dependency-driven [1D Ring AllReduce
experiment](doc/ring_allreduce.md): one configurable-size run on an 8×8 mesh, launched
with `sh utils/run_ring_allreduce.sh`, using BookSim's normal standard output.

Set `OPT_ALL` and `OPT1` directly in `src/Makefile`, then run the script:

```sh
sh utils/run_ring_allreduce.sh
```

The defaults are `OPT_ALL=1` and `OPT1=0`. The Makefile passes both values as
preprocessor macros and rebuilds all objects when either changes, so
`make clean` is unnecessary. The Makefile does not validate their values.
Makefile assignments take precedence over shell environment variables. For a
build-only override, use `make -C src OPT_ALL=1 OPT1=1`.
Use `#if OPT1` / `#else` / `#endif` for the first optimization. `OPT1` is
currently only a build switch; the first optimization has not been implemented.
The switches are independent: `OPT_ALL` currently controls the final output
line, `This is optimized version of BookSim2.0`, via `#if OPT_ALL`.

---

If you use BookSim in your research, we would appreciate the following citation in any publications to which it has contributed:

Nan Jiang, Daniel U. Becker, George Michelogiannakis, James Balfour, Brian Towles, John Kim and William J. Dally. A Detailed and Flexible Cycle-Accurate Network-on-Chip Simulator. In *Proceedings of the 2013 IEEE International Symposium on Performance Analysis of Systems and Software*, 2013.
