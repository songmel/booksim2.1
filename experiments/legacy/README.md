# Preserved standalone collective integrations

`standalone_before_separation/manifest.json` records SHA-256 and original locations
for the files moved/copied out of both BookSim trees during source separation.

Each repository's `files/` contains standalone Ring1D/Ring2D/TTO implementations,
configs, scripts, docs and copies of the original modified core connections.
`build_products/` preserves its old executable, objects, generated parsers and
option stamps. These files are excluded from the active build and minimal export.

This is an inventory-backed backup, not a standalone complete simulator checkout.
The current cores and the preserved connection/dedicated files can reconstruct
the old layout; complete historical measurement snapshots also remain under
`../allreduce/build/` and `../allreduce/results/`.

Test directories have since been removed. Entries under `files/tests/` in the
historical manifest describe files that were present at source separation time.
