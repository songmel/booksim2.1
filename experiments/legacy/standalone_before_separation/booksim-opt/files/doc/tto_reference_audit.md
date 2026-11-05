# TTO audit against the HPCA 2024 artifact

The comparison source is **the original HPCA 2024 artifact**, not the later
SuperMesh repository:

- Repository: https://github.com/sabujlaskar/TTO_HPCA_2024
- Commit: `156128c51d96bedebdc28315a974e8ae9fe2e502` (2024-03-25).
- The README identifies `mesh_overlap_2d_1` as TTO.
- Generator: [`src/allreduce/mesh_allreduce_2d_overlap_1.py`](https://github.com/sabujlaskar/TTO_HPCA_2024/blob/156128c51d96bedebdc28315a974e8ae9fe2e502/src/allreduce/mesh_allreduce_2d_overlap_1.py).
- Runtime: [`src/hmc_fermat.py`](https://github.com/sabujlaskar/TTO_HPCA_2024/blob/156128c51d96bedebdc28315a974e8ae9fe2e502/src/hmc_fermat.py).
- Sizing: [`src/simulate.py`](https://github.com/sabujlaskar/TTO_HPCA_2024/blob/156128c51d96bedebdc28315a974e8ae9fe2e502/src/simulate.py).

## Findings and corrections

The initial native implementation computed a valid AllReduce, but it did not
produce the same schedules as the reference. All eight differential cases
failed before these corrections:

| Item | Initial implementation | Reference and corrected implementation |
| --- | --- | --- |
| Third tree | Generic BFS on unused directed links | Deterministic staircase in `build_trees`; on 8×8, 15 parent assignments differed (e.g. node 9 has parent 10, not 1) |
| Same-edge chunk ordering | Next stripe could start once the previous stripe was injected | Wait for the previous complete stripe to arrive; the reference releases `link_dict[src][dst]` on `message.end` reception |
| Final wave | Short last stripe and missing lanes, no padding | Three equal full stripes in every wave, including the last wave |

The source's three roots (0, 7, 63 on 8×8), first/last trees, bottom-up
child-reception dependencies, top-down parent-reception dependencies, chunk
ordering, and global ReduceScatter→AllGather gate are now matched.

The sizing correspondence is explicit. Let `S = allreduce_chunk_bytes`:

- Native message payload is 128 bytes, so each source stripe sends `S/128`
  packets.
- Upstream `message_size = 128`, `sub_message_size = 128`, and
  `chunk_size = 3*S/4` parameters (four bytes/parameter) give
  `partial_tree_message = S/128`.
- Both use `ceil(allreduce_bytes / (3*S))` waves, each containing three
  full stripes. For 1 MiB and S=16 KiB, both generate 22 waves and 66 stripes.

## Differential validation

`tests/compare_tto_reference.py` loads and executes the author's **unchanged**
schedule class from an explicitly supplied local checkout. Its source SHA-256
is pinned to `c7496fd8f1f874a4bf9cc3e00e4503ac826e7675775cf6fb1783f3497d4801bb`.
The test extracts only the class from the source AST and supplies its trivial
base initialization; it does not run application launch code or require the
upstream NumPy, SCALE-Sim, or `pybooksim` dependencies.

The native schedule is compiled from the current C++ source. For both phases,
the comparison checks every message's source, destination, root/tree, wave,
byte count, receive prerequisites, per-link FIFO prerequisites, and phase gate.
The eight cases span 2×2, 3×3, 4×4, 5×5 and 8×8 meshes, exact and padded waves,
and the default 1 MiB input. **18,670 message records match** after correction.
This is in addition to the numeric and actual-network tests in
`collective_allreduce.md`.

The corrected default 1 MiB / 16 KiB-stripe run completes at **219,639 cycles**
in both local repositories, with identical final traffic statistics. The
earlier 211,441-cycle result belongs to the pre-audit implementation and must
not be used as a result for the corrected TTO schedule.

After checking out the pinned upstream commit, run from either local BookSim
repository:

```sh
python3 tests/compare_tto_reference.py --upstream /path/to/TTO_HPCA_2024
```

## What this does not establish

Matching communication dependencies is not a validation of end-to-end cycle
counts against the author's full simulator. These hardware/model differences
are intentional and visible:

- The artifact uses four directional network interfaces per accelerator
  (`radix=4`); this experiment shares one existing injection/ejection port
  among all directions to retain the same hardware as 1D Ring.
- The published bandwidth experiment uses 8,192-byte messages, 512-byte flits,
  16 payload flits plus a header, and different router/link/buffer settings.
  This experiment uses 128-byte packets and 16-byte flits with no charged header.
- The author's `hmc_fermat.py::aggregate` calls an NPU computation model.
  This network-only experiment has zero aggregation/compute/memory delay.
- The paper excludes the bottom-left rank's gradient and the third reference
  tree excludes that node, while the first/last trees retain it as a relay.
  The original timing simulator does not validate tensor values; our contributor
  checks explicitly enforce the paper's 63-rank interpretation on 8×8.

Use these experiments to compare BookSim implementations under the same
communication workload. Do not treat their absolute times or algorithm ranking
as a reproduction of the original paper's hardware results.
