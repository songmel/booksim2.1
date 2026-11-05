"""Content-addressed builds from current source files, independent of Git/history."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from repro_common import COLLECTIVES, ROOT, WORKSPACE, core_hashes, execute, sha, write_json

VARIANTS = {
    "baseline_o3": ("booksim2", 0, 0, False),
    "optimized_o3": ("booksim-opt", 1, 1, False),
    # Match the optimized Makefile's default compiler optimizations, without PGO.
    "optimized_native": ("booksim-opt", 1, 1, True),
    "baseline_final": ("booksim2", 0, 0, True),
    "opt1to5_final": ("booksim-opt", 1, 0, True),
    "opt1to6_final": ("booksim-opt", 1, 1, True),
}


def command(argv, cwd, log):
    process = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=600)
    log.write_text(process.stdout)
    if process.returncode:
        raise RuntimeError(f"Build failed: {log}\n{process.stdout[-2000:]}")


def target_cpu():
    path = Path("/proc/cpuinfo")
    if path.exists():
        return [line for line in path.read_text().split("\n\n")[0].splitlines()
                if line.startswith(("vendor_id", "model name", "flags"))]
    return [os.uname().machine]


def build(variant, configuration, algorithms, jobs=4, cpu=0, build_root=None):
    repository_name, old_opts, opt6, full = VARIANTS[variant]
    pgo = full and variant != "optimized_native"
    repository = WORKSPACE / repository_name
    cxx, cc = os.environ.get("CXX", "g++"), os.environ.get("CC", "gcc")
    compiler = subprocess.check_output([cxx, "--version"], text=True).splitlines()[0]
    c_compiler = subprocess.check_output([cc, "--version"], text=True).splitlines()[0]
    native = ["-O3", "-g", "-Wall"] + (["-march=native", "-flto=auto"] if full else [])
    opts = [f"-DOPT{i}={old_opts}" for i in range(1, 6)] + [f"-DOPT6={opt6}"]
    cpp_flags = [*native, "-std=c++11", *(["-fno-exceptions"] if full else []), *opts]
    common_hashes = {p.name:sha(p) for p in sorted((COLLECTIVES / "src").iterdir()) if p.is_file()}
    inputs = dict(schema=1, variant=variant, compiler=compiler, c_compiler=c_compiler,
                  cpp_flags=cpp_flags, c_flags=native, source_sha256=core_hashes(repository),
                  experiment_sources_sha256=common_hashes,
                  pgo_training=dict(config_sha256=sha(configuration), algorithms=algorithms, input_mib=1) if pgo else None,
                  target_cpu=target_cpu() if full else None)
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    parent = Path(build_root or ROOT / "build").resolve() / variant
    parent.mkdir(parents=True, exist_ok=True)
    for path in sorted(parent.glob(key[:16] + "*/manifest.json")):
        cached = json.loads(path.read_text())
        binary = path.parent / "collective"
        valid = (cached.get("status") == "complete" and cached["build_key"] == key
                 and binary.is_file() and sha(binary) == cached["binary_sha256"]
                 and core_hashes(path.parent / "source") == inputs["source_sha256"]
                 and all((path.parent / "experiment_sources" / name).is_file()
                         and sha(path.parent / "experiment_sources" / name) == digest
                         for name, digest in common_hashes.items())
                 and all((path.parent / "profiles" / name).is_file()
                         and sha(path.parent / "profiles" / name) == digest
                         for name, digest in cached.get("profiles_sha256", {}).items()))
        if valid:
            # Store a location resolved now, so copied caches do not require old paths.
            cached["binary"] = str(binary.resolve())
            print(f"BUILD {variant}: cached {binary}", flush=True)
            return cached
    directory = parent / key[:16]
    retry = 0
    while directory.exists():
        retry += 1
        directory = parent / f"{key[:16]}-retry{retry}"
    directory.mkdir()
    for name in ("source", "experiment_sources", "objects", "logs", "profiles"):
        (directory / name).mkdir()
    for name, digest in inputs["source_sha256"].items():
        target = directory / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository / name, target)
        if sha(target) != digest:
            raise RuntimeError("Core source changed during snapshot: " + name)
    for name, digest in common_hashes.items():
        shutil.copy2(COLLECTIVES / "src" / name, directory / "experiment_sources" / name)
        if sha(directory / "experiment_sources" / name) != digest:
            raise RuntimeError("Experiment source changed during snapshot: " + name)
    source = directory / "source/src"
    external = directory / "experiment_sources"
    profiles = directory / "profiles"
    config = directory / "training.cfg"
    shutil.copy2(configuration, config)
    binary = directory / "collective"
    record = dict(**inputs, build_key=key, status="building", binary=str(binary.resolve()),
                  commands=[], training=[], stages={}, pgo_enabled=pgo)
    manifest = directory / "manifest.json"
    write_json(manifest, record)
    try:
        command(["bison", "-y", "-d", "config.y"], source, directory / "logs/bison.log")
        command(["flex", "config.l"], source, directory / "logs/flex.log")
        record["generated_parser_sha256"] = {name:sha(source / name) for name in ("lex.yy.c", "y.tab.c", "y.tab.h")}
        units = sorted(p for p in source.glob("*.cpp") if p.name != "main.cpp")
        units += sorted(source.glob("*/*.cpp")) + sorted(external.glob("*.cpp"))
        units += [source / "lex.yy.c", source / "y.tab.c"]
        objects = [directory / "objects" / f"{i:03d}_{p.stem}.o" for i, p in enumerate(units)]
        includes = [f"-I{source / p}" for p in (".", "allocators", "arbiters", "routers", "networks", "power")]
        includes += [f"-I{external}"]

        def compile_stage(stage):
            feedback = ([f"-fprofile-generate={profiles}"] if stage == "generate" else
                        [f"-fprofile-use={profiles}", "-Werror=missing-profile", "-Werror=coverage-mismatch"] if stage == "use" else [])
            commands = [[cxx if src.suffix == ".cpp" else cc,
                         *(cpp_flags if src.suffix == ".cpp" else native), *feedback, *includes,
                         "-c", str(src), "-o", str(obj)] for src, obj in zip(units, objects)]
            record["commands"].extend(dict(stage=stage, argv=argv) for argv in commands)
            write_json(manifest, record)
            print(f"BUILD {variant}: {stage}, {len(units)} units", flush=True)
            def one(item):
                index, argv = item
                command(argv, source, directory / "logs" / f"{stage}_{index:03d}.log")
            with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
                list(pool.map(one, enumerate(commands)))
            link = [cxx, *native, *(["-fno-exceptions"] if full else []), *feedback, *map(str, objects), "-o", str(binary)]
            record["commands"].append(dict(stage=stage, argv=link))
            command(link, source, directory / "logs" / f"{stage}_link.log")
            record["stages"][stage] = dict(binary_sha256=sha(binary), objects_sha256={p.name:sha(p) for p in objects})
            write_json(manifest, record)

        if pgo:
            compile_stage("generate")
            shutil.copy2(binary, directory / "collective.profile_generate")
            for algorithm in algorithms:
                print(f"TRAIN {variant}: {algorithm} 1 MiB (excluded from measurements)", flush=True)
                row = execute(binary, config, algorithm, 1, cpu, directory / "logs" / f"training_{algorithm}.log")
                row["is_timing_measurement"] = False
                record["training"].append(row)
                write_json(manifest, record)
            feedback = sorted(profiles.rglob("*.gcda"))
            if not feedback:
                raise RuntimeError("PGO did not produce feedback")
            record["profiles_sha256"] = {str(p.relative_to(profiles)):sha(p) for p in feedback}
            compile_stage("use")
            if any(sha(profiles / name) != digest for name, digest in record["profiles_sha256"].items()):
                raise RuntimeError("PGO feedback changed during use compilation")
        else:
            compile_stage("native" if full else "o3")
        if core_hashes(directory / "source") != inputs["source_sha256"]:
            raise RuntimeError("Core snapshot was modified during build")
        record.update(status="complete", binary_sha256=sha(binary))
        write_json(manifest, record)
        return record
    except BaseException as error:
        record.update(status="failed", error=str(error))
        write_json(manifest, record)
        raise
