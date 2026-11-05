#!/bin/sh
# Build and run one 2D Ring AllReduce, preserving BookSim stdout/status.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
make -C "$repo_dir/src" -j4 >&2
exec "$repo_dir/src/booksim" "$repo_dir/src/examples/mesh88_ring2d_allreduce" "$@"
