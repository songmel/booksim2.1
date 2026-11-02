#!/bin/sh
# Build and run one Ring AllReduce, preserving BookSim standard output/status.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

# Build messages go to stderr so stdout contains only BookSim output.
make -C "$repo_dir/src" -j4 >&2
exec "$repo_dir/src/booksim" "$repo_dir/src/examples/mesh88_ring_allreduce"
