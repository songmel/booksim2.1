#!/usr/bin/env python3
"""Export the three source directories without Git metadata, caches or binaries."""
import argparse
import tarfile

from repro_common import ROOT, WORKSPACE, GENERATED, GENERATED_SUFFIXES, GENERATED_DIRECTORIES
from verify_layout import verify


def package_files():
    yield WORKSPACE / "README.md"
    yield WORKSPACE / ".gitignore"
    for name in ("booksim2", "booksim-opt"):
        for path in sorted((WORKSPACE / name).rglob("*")):
            relative = path.relative_to(WORKSPACE / name)
            if not path.is_file() or ({".git"} | GENERATED_DIRECTORIES).intersection(relative.parts):
                continue
            if path.name in GENERATED or path.suffix in GENERATED_SUFFIXES:
                continue
            yield path
    for name in ("README.md", ".gitignore", "core_manifest.json", "reproduce.py", "run_sweep.py", "repro_build.py",
                 "repro_common.py", "verify_layout.py", "package.py"):
        yield ROOT / name
    for path in sorted((ROOT / "collectives").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    verify()
    from pathlib import Path
    target = Path(args.output).resolve()
    if target.exists():
        parser.error("Choose a new output filename")
    target.parent.mkdir(parents=True, exist_ok=True)
    files = list(package_files())
    with tarfile.open(target, "w:gz", dereference=True) as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(WORKSPACE)), recursive=False)
    print(f"Exported {len(files)} source/config/reference files: {target}")


if __name__ == "__main__":
    main()
