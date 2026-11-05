#!/usr/bin/env python3
"""Compare frozen O3 binaries with native + LTO + trained PGO, without core edits."""
import argparse
import concurrent.futures
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import time

from profile import ROOT, sha
from run import compare, save_csv
import sweep_hardware as sweep
from verify_sources import verify

ALGORITHMS = ("ring1d", "alltoall")
VARIANTS = ("o3", "native_lto_pgo")
REFERENCE = ROOT / "results/ring1d_alltoall_1MiB_mesh_3reps"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2)+"\n")
    temporary.replace(path)


def checked(command, cwd, log):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {log}")


def frozen_build(backend, old, directory, args, cfg, reference, training):
    directory.mkdir(parents=True, exist_ok=args.resume)
    source_root = directory / "source"
    legacy = Path(old["legacy_binary"]).parent
    for name, expected in old["core_files_sha256"].items():
        if sha(legacy/"source"/name) != expected:
            raise RuntimeError(f"Native source snapshot changed: {name}")
    if not source_root.exists():
        shutil.copytree(legacy/"source", source_root)
    for name, expected in old["core_files_sha256"].items():
        if sha(source_root/name) != expected:
            raise RuntimeError(f"Build source snapshot changed: {name}")
    external = directory/"experiment_sources"
    external_origin = Path(getattr(args, "external_sources", None) or (Path(old["binary"]).parent/"experiment_sources"))
    external_hashes = {p.name:sha(p) for p in sorted(external_origin.glob("*")) if p.is_file()}
    if not external.exists():
        shutil.copytree(external_origin, external)
    for name, expected in external_hashes.items():
        if sha(external/name) != expected:
            raise RuntimeError(f"External snapshot changed: {name}")
    source = source_root/"src"
    objects = directory/"objects"
    logs = directory/"logs"
    profiles = directory/"profiles"
    for p in (objects, logs, profiles):
        p.mkdir(exist_ok=args.resume)
    includes = [f"-I{source/p}" for p in (".","arbiters","allocators","routers","networks","power")]
    includes.append(f"-I{external}")
    sources = sorted(p for p in source.glob("*.cpp") if p.name != "main.cpp")
    sources += sorted(source.glob("*/*.cpp")) + sorted(external.glob("*.cpp"))
    sources += [source/"lex.yy.c", source/"y.tab.c"]
    native_flags = ["-O3", "-g", "-Wall", "-march=native", f"-flto={getattr(args,'lto',None) or args.jobs}"]
    cxx_flags = ["-fno-exceptions"] if getattr(args,"no_exceptions",False) else []
    opt_flags = [f"-DOPT{i}={int(backend=='optimized')}" for i in range(1,6)]
    object_paths = [objects/f"{i:03d}_{p.stem}.o" for i,p in enumerate(sources)]
    binary = directory/"collective"
    record = {"backend":backend, "commit":old["commit"], "binary":str(binary),
              "compiler":subprocess.check_output(["g++","--version"],text=True).splitlines()[0],
              "flags":native_flags+["-std=c++11",*cxx_flags,*opt_flags,f"-fprofile-use={profiles}"],
              "core_files_sha256":old["core_files_sha256"],
              "experiment_sources":external_hashes,
              "reference_experiment_sources":old["experiment_sources"],
              "exceptions_enabled":not getattr(args,"no_exceptions",False),
              "lto_parallelism":str(getattr(args,"lto",None) or args.jobs),
              "generated_parser_sha256":{p.name:sha(p) for p in (source/"lex.yy.c",source/"y.tab.c",source/"y.tab.h")},
              "commands":[], "stages":{}, "original_sources_unmodified":True}
    manifest_path = directory/"manifest.json"
    if args.resume and manifest_path.exists():
        record = json.loads(manifest_path.read_text())
        if record["commit"] != old["commit"] or record["experiment_sources"] != external_hashes:
            raise RuntimeError("Resume build provenance mismatch")
        if record.get("profile_use_strict_checks_passed"):
            if sha(binary) != record["binary_sha256"]:
                raise RuntimeError("Resumed binary hash mismatch")
            return record
    write_json(manifest_path, record)

    def compile_stage(stage):
        feedback = [f"-fprofile-generate={profiles}"] if stage=="generate" else [
            f"-fprofile-use={profiles}", "-Werror=missing-profile", "-Werror=coverage-mismatch"]
        commands = []
        for index,(src,obj) in enumerate(zip(sources,object_paths)):
            cxx = src.suffix == ".cpp"
            flags = native_flags+(["-std=c++11",*cxx_flags,*opt_flags] if cxx else [])+feedback
            commands.append(["g++" if cxx else "gcc", *flags, *includes, "-c", str(src), "-o", str(obj)])
        record["commands"].extend({"stage":stage,"argv":c} for c in commands)
        write_json(manifest_path, record)
        print(f"{backend}: {stage}, compiling {len(commands)} frozen units", flush=True)
        def compile_one(item):
            index, command = item
            checked(command, source, logs/f"{stage}_{index:03d}.log")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for count,_ in enumerate(pool.map(compile_one, enumerate(commands)),1):
                if count%20==0 or count==len(commands):
                    print(f"  {backend} {stage}: {count}/{len(commands)} units", flush=True)
        link = ["g++", *native_flags, *cxx_flags, *feedback, *map(str,object_paths), "-o", str(binary)]
        record["commands"].append({"stage":stage,"argv":link})
        write_json(manifest_path, record)
        print(f"{backend}: {stage}, LTO link", flush=True)
        checked(link, source, logs/f"{stage}_link.log")
        record["stages"][stage] = {"binary_sha256":sha(binary),
                                   "objects_sha256":{str(p):sha(p) for p in object_paths}}
        write_json(manifest_path, record)

    # Same object and output paths in both stages are essential for GCC's
    # profile naming and checksums. The generation executable is also archived.
    if "generate" not in record["stages"]:
        compile_stage("generate")
    elif sha(binary) != record["stages"]["generate"]["binary_sha256"]:
        raise RuntimeError("Generation binary changed before training resume")
    generation_binary = directory/"collective.profile_generate"
    shutil.copy2(binary, generation_binary)
    record["generation_binary"] = str(generation_binary)
    for algorithm in ALGORITHMS:
        if any(r["backend"]==backend and r["algorithm"]==algorithm for r in training):
            print(f"Reusing completed PGO training: {backend} {algorithm}", flush=True)
            continue
        print(f"PGO training (excluded from measurement): {backend} {algorithm}", flush=True)
        row = execute(record, cfg, algorithm, "pgo_training", 0, args, args.output,
                      sampled=True, reference=reference[algorithm])
        row["is_timing_measurement"] = False
        training.append(row)
        save_csv(args.output/"training.csv", sorted({k for r in training for k in r}), training)
    gcda = sorted(profiles.rglob("*.gcda"))
    if not gcda:
        raise RuntimeError("No PGO feedback generated")
    hot_units = ("trafficmanager", "iq_router", "buffer_state", "experiment")
    for unit in hot_units:
        if not any(p.name.endswith(f"_{unit}.gcda") and p.stat().st_size>32 for p in gcda):
            raise RuntimeError(f"No feedback for hot unit: {unit}")
    record["profiles_sha256"] = {str(p.relative_to(profiles)):sha(p) for p in gcda}
    record["profile_file_count"] = len(gcda)
    # Keep a readable summary proving that execution counts were collected.
    with (logs/"profile_dump.txt").open("w") as output:
        for p in gcda:
            process = subprocess.run(["gcov-dump","-l",str(p)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=True)
            output.write(process.stdout)
    compile_stage("use")
    for name,expected in old["core_files_sha256"].items():
        if sha(source_root/name)!=expected:
            raise RuntimeError(f"Native source changed during compilation: {name}")
    for name,expected in external_hashes.items():
        if sha(external/name)!=expected:
            raise RuntimeError(f"External source changed during compilation: {name}")
    if any(sha(profiles/name)!=expected for name,expected in record["profiles_sha256"].items()):
        raise RuntimeError("Profile feedback changed during use build")
    record["binary_sha256"] = sha(binary)
    record["profile_use_strict_checks_passed"] = True
    write_json(manifest_path, record)
    print(f"{backend}: native+LTO+PGO ready; {len(gcda)} feedback files", flush=True)
    return record


def execute(build, cfg, algorithm, variant, repetition, args, directory, sampled, reference):
    backend = build["backend"]
    name = f"{variant}_{algorithm}_r{repetition}_{backend}"
    command = ["taskset","-c",str(args.cpu),build["binary"],str(cfg),algorithm,"1048576","16384","1000000000"]
    if sampled:
        command.append("--profile-us=1000")
    started = time.monotonic()
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,text=True,timeout=900)
    log = directory/"logs"/f"{name}.log"
    log.write_text(process.stdout)
    markers = [s.removeprefix("EXPERIMENT_RESULT ") for s in process.stdout.splitlines() if s.startswith("EXPERIMENT_RESULT ")]
    if process.returncode or len(markers)!=1:
        raise RuntimeError(f"Run failed: {log}")
    row = json.loads(markers[0])
    row.update(backend=backend, variant=variant, stage=variant, repetition=repetition,
               input_mib=1, cpu=args.cpu, hardware="mesh_native_1vc_packet512B",
               mode="sampled" if sampled else "unsampled_control", log_file=str(log),
               config_sha256=sha(cfg), process_wall_seconds=time.monotonic()-started,
               reference_match=all(compare(row,reference).values()), status="ok")
    if not row["validation_passed"] or not row["reference_match"]:
        raise RuntimeError(f"Simulation changed relative to O3 reference: {log}")
    if sampled and row.get("profile_samples",0)<100:
        raise RuntimeError(f"Insufficient CPU samples: {log}")
    return row


def regenerate(directory, rows):
    pairs=[]
    groups={}
    for row in rows:
        groups.setdefault((row["variant"],row["algorithm"],row["repetition"]),{})[row["backend"]]=row
    for (variant,algorithm,repetition),group in groups.items():
        if set(group)!={"baseline","optimized"}:
            continue
        b,o=group["baseline"],group["optimized"]
        checks=compare(b,o)
        pair={"stage":variant,"variant":variant,"hardware":b["hardware"],"algorithm":algorithm,
              "repetition":repetition,**checks,"collective_cycles":b["collective_cycles"],
              "drain_cycles":b["drain_cycles"],"baseline_core_seconds_estimate":b["booksim_cpu_seconds_estimate"],
              "optimized_core_seconds_estimate":o["booksim_cpu_seconds_estimate"],
              "core_speedup_estimate":b["booksim_cpu_seconds_estimate"]/o["booksim_cpu_seconds_estimate"],
              "baseline_total_seconds":b["run_seconds"],"optimized_total_seconds":o["run_seconds"]}
        for key in ("channel_model","flit_bytes","packet_flits","packet_bytes","num_vcs","vc_depth","link_latency_ns","bandwidth_gbps","input_speedup","hold_switch","network_topology"):
            if b[key]!=o[key]:raise RuntimeError("Hardware mismatch")
            pair[key]=b[key]
        pairs.append(pair)
        if not all(checks.values()):raise RuntimeError("Backend simulation mismatch")
    return sweep.save(directory,rows,pairs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"results/ring1d_alltoall_native_lto_pgo_1MiB_3reps")
    parser.add_argument("--cpu",type=int,default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs",type=int,default=4)
    parser.add_argument("--repetitions",type=int,default=3)
    parser.add_argument("--resume",action="store_true",help="Resume an interrupted build before timing measurements")
    args=parser.parse_args()
    if args.jobs<1 or args.repetitions<1 or args.cpu not in os.sched_getaffinity(0):parser.error("Invalid jobs/repetitions/CPU")
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    lock=(args.output/".lock").open("w")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest_path=args.output/"manifest.json"
    if manifest_path.exists() and not args.resume:raise RuntimeError("Choose a new output directory")
    if args.resume and (args.output/"raw.csv").exists():raise RuntimeError("This resume mode is only for pre-measurement builds")
    previous_manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    for p in ("logs","configs","driver_sources"):(args.output/p).mkdir(exist_ok=args.resume)
    verify()
    old=json.loads((REFERENCE/"manifest.json").read_text())
    if old["algorithms"]!=list(ALGORITHMS):raise RuntimeError("Unexpected reference workloads")
    old_rows=list(csv.DictReader((REFERENCE/"raw.csv").open()))
    reference={a:next(r for r in old_rows if r["algorithm"]==a) for a in ALGORITHMS}
    old_builds=old["builds"]
    for b in old_builds.values():
        if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("O3 binary hash changed")
    if old_builds["baseline"]["experiment_sources"]!=old_builds["optimized"]["experiment_sources"]:
        raise RuntimeError("External source snapshots differ")
    source_cfg=next((REFERENCE/"configs").glob("*.cfg"))
    cfg=args.output/"configs"/source_cfg.name
    cfg.write_bytes(source_cfg.read_bytes())
    build_root=ROOT/"build/compiler_optimizations"/args.output.name
    if build_root.exists() and not args.resume:raise RuntimeError("Build directory already exists; select a fresh output name")
    manifest={"status":"building","algorithms":ALGORITHMS,"input_mib":1,"topology":"mesh",
              "hardware":old["hardware"],"effective_config":old["effective_config"],
              "cpu":args.cpu,"repetitions":args.repetitions,"config_sha256":sha(cfg),
              "reference_directory":str(REFERENCE),"reference_manifest_sha256":sha(REFERENCE/"manifest.json"),
              "o3_builds":old_builds,"compiler_builds":{},
              "training":"One fresh Ring1D and one fresh direct AllToAll at 1 MiB per backend, same sampling paths; common mixed-workload profile per backend. Training runs excluded from timing summaries. Same workload sizes as evaluation; no held-out workload generalization claimed.",
              "measurement":"Fresh O3 controls and native+LTO+PGO; 3 sampled runs per workload/backend/variant, counterbalanced serial CPU affinity. BookSim CPU sampling estimates, not exact exclusive timers. Four additional unsampled controls for final binaries.",
              "source_policy":"Frozen exact native and external sources from prior pilot; no core/Makefile or workload changes.",
              "hardware_limit":"Exploratory 8x8 mesh profile, not a calibrated data-center model.",
              "host_lscpu":subprocess.check_output(["lscpu"],text=True),
              "gcc_native_target":subprocess.check_output(["g++","-march=native","-Q","--help=target"],text=True),
              "compiler_version":subprocess.check_output(["g++","--version"],text=True).splitlines()[0]}
    names=("compiler_pilot.py","run.py","sweep_hardware.py","profile.py","verify_sources.py")
    manifest["driver_sha256"]={n:sha(ROOT/n) for n in names}
    snapshot_name="resume_driver_sources" if previous_manifest else "driver_sources"
    (args.output/snapshot_name).mkdir(exist_ok=True)
    for name in names:(args.output/snapshot_name/name).write_bytes((ROOT/name).read_bytes())
    if previous_manifest:
        for key in ("cpu","repetitions","config_sha256","reference_manifest_sha256"):
            if previous_manifest[key]!=manifest[key]:raise RuntimeError(f"Resume mismatch: {key}")
        manifest["initial_driver_sha256"]=previous_manifest["driver_sha256"]
        manifest["resume_reason"]=previous_manifest.get("error")
        manifest["active_driver_snapshot"]=snapshot_name
    write_json(manifest_path,manifest)
    training=list(csv.DictReader((args.output/"training.csv").open())) if args.resume and (args.output/"training.csv").exists() else []
    rows,controls=[],[]
    try:
        for backend in ("baseline","optimized"):
            manifest["compiler_builds"][backend]=frozen_build(backend,old_builds[backend],build_root/backend,args,cfg,reference,training)
            write_json(manifest_path,manifest)
        manifest["status"]="measuring"
        write_json(manifest_path,manifest)
        cells=[("o3","baseline"),("native_lto_pgo","baseline"),("o3","optimized"),("native_lto_pgo","optimized")]
        for algorithm in ALGORITHMS:
            for repetition in range(1,args.repetitions+1):
                order=cells if repetition%3==1 else list(reversed(cells)) if repetition%3==2 else cells[2:]+cells[:2]
                for variant,backend in order:
                    builds=old_builds if variant=="o3" else manifest["compiler_builds"]
                    print(f"MEASURE {algorithm} r{repetition} {variant} {backend}",flush=True)
                    row=execute(builds[backend],cfg,algorithm,variant,repetition,args,args.output,True,reference[algorithm])
                    rows.append(row)
                    regenerate(args.output,rows)
                    print(f"  BookSim CPU estimate {row['booksim_cpu_seconds_estimate']:.4f}s; {row['collective_cycles']} cycles; reference match",flush=True)
        for algorithm in ALGORITHMS:
            for backend in ("baseline","optimized"):
                print(f"UNSAMPLED {algorithm} {backend}",flush=True)
                row=execute(manifest["compiler_builds"][backend],cfg,algorithm,"compiled_control",0,args,args.output,False,reference[algorithm])
                controls.append(row)
                save_csv(args.output/"controls.csv",sorted({k for r in controls for k in r}),controls)
        summaries=regenerate(args.output,rows)
        if len(summaries)!=4 or not all(s["all_checks_passed"] and s["repetitions"]==args.repetitions for s in summaries):
            raise RuntimeError("Incomplete measurement summaries")
        comparison=[]
        for algorithm in ALGORITHMS:
            get=lambda variant,backend:statistics.median(r["booksim_cpu_seconds_estimate"] for r in rows if r["algorithm"]==algorithm and r["variant"]==variant and r["backend"]==backend)
            b0,o0,b1,o1=get("o3","baseline"),get("o3","optimized"),get("native_lto_pgo","baseline"),get("native_lto_pgo","optimized")
            comparison.append({"algorithm":algorithm,"input_mib":1,"repetitions":args.repetitions,
                "o3_baseline_cpu_seconds":b0,"o3_optimized_cpu_seconds":o0,
                "compiled_baseline_cpu_seconds":b1,"compiled_optimized_cpu_seconds":o1,
                "o3_backend_speedup":b0/o0,"compiled_backend_speedup":b1/o1,
                "compiler_gain_baseline":b0/b1,"compiler_gain_optimized":o0/o1,
                "combined_o3_baseline_to_compiled_opt_speedup":b0/o1,
                "compiled_backend_time_reduction_pct":100*(1-o1/b1),
                "collective_cycles":reference[algorithm]["collective_cycles"],"all_checks_passed":True})
        save_csv(args.output/"compiler_comparison.csv",list(comparison[0]),comparison)
        for backend,b in manifest["compiler_builds"].items():
            if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("Final binary hash changed")
            for name,expected in b["profiles_sha256"].items():
                if sha(Path(b["binary"]).parent/"profiles"/name)!=expected:raise RuntimeError("Measurement modified training profiles")
        for b in old_builds.values():
            if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("Original O3 binary changed")
        verify()
        manifest.update(status="complete",sampled_runs=len(rows),training_runs=len(training),unsampled_controls=len(controls),all_checks_passed=True)
        for row in comparison:print(json.dumps(row),flush=True)
    except BaseException as error:
        manifest.update(status="failed_or_interrupted",error=repr(error))
        raise
    finally:
        write_json(manifest_path,manifest)
    print(f"Complete: {args.output}",flush=True)


if __name__=="__main__":
    main()
