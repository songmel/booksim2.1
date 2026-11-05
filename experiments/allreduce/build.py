#!/usr/bin/env python3
"""Build common experiment code against byte-for-byte Git HEAD snapshots.

Neither simulator's working tree, Makefile, nor build products are touched.
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[1]
REPOS = {"baseline": WORKSPACE / "booksim2", "optimized": WORKSPACE / "booksim-opt"}
BUILD_ROOT = ROOT / "build/current_standalone"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(command, cwd, log):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {command!r}; see {log}")


def build(backend, jobs=4, force=False):
    repo = REPOS[backend]
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", commit, "src"])
    common = sorted((ROOT / "src").glob("*"))
    common_hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in common if p.is_file()}
    switches = [1 if backend == "optimized" else 0] * 5
    compiler = os.environ.get("CXX", "g++")
    version = subprocess.check_output([compiler, "--version"], text=True).splitlines()[0]
    flags = ["-O3", "-g", "-std=c++11", "-Wall"] + [f"-DOPT{i+1}={v}" for i, v in enumerate(switches)]
    key_data = {"commit": commit, "archive_sha256": sha(archive), "experiment_sources": common_hashes,
                "flags": flags, "compiler": version}
    key = sha(json.dumps(key_data, sort_keys=True).encode())
    # Preserve binaries and source snapshots referenced by historical reports.
    directory = BUILD_ROOT / backend
    binary = directory / "collective"
    manifest_path = directory / "manifest.json"
    if not force and binary.is_file() and manifest_path.is_file():
        previous = json.loads(manifest_path.read_text())
        if previous.get("build_key") == key and previous.get("binary_sha256") == sha(binary.read_bytes()):
            print(f"{backend}: cached {binary}", flush=True)
            return previous
    source_root = directory / "source"
    # A new commit may remove translation units. Never compile stale files
    # left by a previous snapshot. This is only our generated build directory.
    if source_root.exists():
        shutil.rmtree(source_root)
    source_root.mkdir(parents=True, exist_ok=True)
    core_hashes = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        for member in snapshot.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError("Unsafe archive member")
            if not member.isfile():
                continue
            data = snapshot.extractfile(member).read()
            destination = source_root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            core_hashes[str(path)] = sha(data)
    source = source_root / "src"
    logs = directory / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    run(["bison", "-y", "-d", "config.y"], source, logs / "bison.log")
    run(["flex", "config.l"], source, logs / "flex.log")
    includes = [f"-I{source / p}" for p in (".", "arbiters", "allocators", "routers", "networks", "power")]
    includes.append(f"-I{ROOT / 'src'}")
    cpp_sources = sorted(p for p in source.glob("*.cpp") if p.name != "main.cpp")
    cpp_sources += sorted(source.glob("*/*.cpp"))
    cpp_sources += sorted((ROOT / "src").glob("*.cpp"))
    tasks = [(compiler, p) for p in cpp_sources]
    tasks += [(os.environ.get("CC", "gcc"), source / p) for p in ("lex.yy.c", "y.tab.c")]
    objects = []

    def compile_one(index_task):
        index, (executable, path) = index_task
        obj = directory / "objects" / f"{index:03d}_{path.stem}.o"
        obj.parent.mkdir(parents=True, exist_ok=True)
        selected_flags = flags if executable == compiler else ["-O3", "-g", "-Wall"]
        run([executable, *selected_flags, *includes, "-c", str(path), "-o", str(obj)],
            source, logs / f"{index:03d}_{path.stem}.log")
        return obj

    print(f"{backend}: compiling immutable commit {commit[:12]} ({len(tasks)} units)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        for obj in executor.map(compile_one, enumerate(tasks)):
            objects.append(obj)
    run([compiler, *map(str, objects), "-o", str(binary)], source, logs / "link.log")
    # Prove that even the build snapshot's original source files were not patched.
    for name, expected in core_hashes.items():
        if sha((source_root / name).read_bytes()) != expected:
            raise RuntimeError(f"Original source changed during build: {name}")
    manifest = {**key_data, "backend": backend, "repository": str(repo), "build_key": key,
                "core_files_sha256": core_hashes, "binary_sha256": sha(binary.read_bytes()),
                "binary": str(binary), "original_sources_unmodified": True}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"{backend}: built {binary}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["both", *REPOS], default="both")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    for backend in REPOS if args.backend == "both" else [args.backend]:
        build(backend, args.jobs, args.force)


if __name__ == "__main__":
    main()
