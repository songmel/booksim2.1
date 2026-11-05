# Historical collective experiments

This directory preserves the earlier runners, immutable builds, PGO profiles,
measurement CSV/logs and performance reports. The original documentation is in
[HISTORY.md](HISTORY.md).

The active, portable workflow is [../reproduce.py](../reproduce.py), documented in
[../README.md](../README.md). It builds the current separated sources and does not
depend on the archived files here. Older runners may require their original Git
HEAD/overlay/source-preservation assumptions and are retained as historical evidence.

`src/` is a relative compatibility link to `../collectives/src/`.
File contents used in earlier measurement remain intact. Existing `build/` and
`results/` paths have not moved, so archived manifests keep their local references.

Test directories have been removed. Test commands in `HISTORY.md` describe the
historical layout and are retained only as records.
