#!/bin/sh
set -eu
experiment_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$experiment_dir/run.py" "$@"
