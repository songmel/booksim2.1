#!/usr/bin/env python3
"""Add -fno-exceptions and -flto=auto to the existing native/LTO/PGO pilot."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import statistics
import subprocess

from compiler_pilot import ROOT, REFERENCE, frozen_build, execute, regenerate, write_json
from profile import sha
from run import save_csv
from verify_sources import verify

ALGORITHMS=("ring1d","alltoall")
PREVIOUS=ROOT/"results/ring1d_alltoall_native_lto_pgo_1MiB_3reps"
OLD="native_lto4_pgo_exceptions"
NEW="native_lto_auto_pgo_noexceptions"


def compatibility_checks(build,cfg,directory):
    results=[]
    # Run only the final feedback-use executable so rejected-input tests do
    # not contaminate the two valid PGO training runs or their profiles.
    for name,algorithm,size,option in (
        ("invalid_integer","ring1d","12oops",None),
        ("integer_overflow","ring1d","9999999999999999999999",None),
        ("unknown_algorithm","unknown","1048576",None),
        ("unaligned_alltoall","alltoall","1048575",None),
        ("invalid_sampling_period","ring1d","1048576","--profile-us=999")):
        command=[build["binary"],str(cfg),algorithm,size,"16384","1000000000"]
        if option:command.append(option)
        p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15)
        log=directory/"logs"/f"error_{build['backend']}_{name}.log"
        log.write_text(p.stdout)
        if p.returncode!=1 or "EXPERIMENT_ERROR " not in p.stdout or "EXPERIMENT_RESULT " in p.stdout:
            raise RuntimeError(f"No-exception validation did not reject input: {log}")
        results.append({"backend":build["backend"],"case":name,"exit_code":p.returncode,"passed":True,"log":str(log)})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"results/ring1d_alltoall_lto_auto_noexceptions_pgo_1MiB_3reps")
    parser.add_argument("--cpu",type=int,default=min(os.sched_getaffinity(0)))
    parser.add_argument("--jobs",type=int,default=4)
    parser.add_argument("--repetitions",type=int,default=3)
    args=parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0) or args.jobs<1 or args.repetitions<1:parser.error("Invalid CPU/jobs/repetitions")
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    lock=(args.output/".lock").open("w")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest_path=args.output/"manifest.json"
    if manifest_path.exists():raise RuntimeError("Choose a new output directory")
    for name in ("logs","configs","driver_sources"):(args.output/name).mkdir()
    args.resume=False
    args.lto="auto"
    args.no_exceptions=True
    args.external_sources=ROOT/"src"
    verify()
    old=json.loads((PREVIOUS/"manifest.json").read_text())
    source=json.loads((REFERENCE/"manifest.json").read_text())
    assert old["status"]=="complete" and old["all_checks_passed"]
    controls=old["compiler_builds"]
    for b in controls.values():
        if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("Prior compiler executable changed")
    ref_rows=list(csv.DictReader((REFERENCE/"raw.csv").open()))
    references={a:next(r for r in ref_rows if r["algorithm"]==a) for a in ALGORITHMS}
    original_cfg=next((PREVIOUS/"configs").glob("*.cfg"))
    cfg=args.output/"configs"/original_cfg.name
    cfg.write_bytes(original_cfg.read_bytes())
    build_root=ROOT/"build/compiler_optimizations"/args.output.name
    if build_root.exists():raise RuntimeError("Build name already exists")
    manifest={"status":"building","algorithms":ALGORITHMS,"input_mib":1,"cpu":args.cpu,"repetitions":args.repetitions,
              "topology":"mesh","hardware":old["hardware"],"effective_config":old["effective_config"],
              "config_sha256":sha(cfg),"previous_directory":str(PREVIOUS),"previous_manifest_sha256":sha(PREVIOUS/"manifest.json"),
              "control_builds":controls,"compiler_builds":{},"variants":{"control":OLD,"new":NEW},
              "new_flags":["-O3","-march=native","-flto=auto","-fno-exceptions","-fprofile-use=<per-backend profiles>"],
              "native_source_policy":"Exact original core snapshots and Makefiles retained; full recompile for both backends.",
              "external_compatibility_changes":"Experiment validation calls use typed Fail: throw with exceptions enabled, stderr + exit(1) otherwise. Conditional main catch; checked strtol parsing replaces stoi. Successful communication schedule and all validation predicates retained. Prior controls use the archived pre-compatibility wrapper, so the measured delta includes this necessary wrapper adaptation.",
              "training":"Fresh one Ring1D and one AllToAll at 1MiB per backend with evaluation sampling paths; excluded from timing. Mixed-workload PGO at the evaluation sizes, no held-out claim.",
              "measurement":"24 fresh serial sampled runs, counterbalanced compiler/backend order on one CPU, plus 4 final-executable unsampled controls. BookSim CPU estimates exclude collective control/output.",
              "lto_auto_meaning":"Compiler chooses LTO build parallelism; this does not make the simulator multithreaded.",
              "host_lscpu":subprocess.check_output(["lscpu"],text=True),
              "gcc_native_target":subprocess.check_output(["g++","-march=native","-Q","--help=target"],text=True)}
    names=("noexceptions_pilot.py","compiler_pilot.py","run.py","sweep_hardware.py","profile.py","verify_sources.py")
    manifest["driver_sha256"]={n:sha(ROOT/n) for n in names}
    for name in names:(args.output/"driver_sources"/name).write_bytes((ROOT/name).read_bytes())
    write_json(manifest_path,manifest)
    rows,training,unsampled,errors=[],[],[],[]
    try:
        for backend in ("baseline","optimized"):
            b=frozen_build(backend,source["builds"][backend],build_root/backend,args,cfg,references,training)
            manifest["compiler_builds"][backend]=b
            errors.extend(compatibility_checks(b,cfg,args.output))
            write_json(args.output/"compatibility_checks.json",errors)
            write_json(manifest_path,manifest)
        manifest["status"]="measuring"
        write_json(manifest_path,manifest)
        cells=[(OLD,"baseline"),(NEW,"baseline"),(OLD,"optimized"),(NEW,"optimized")]
        for algorithm in ALGORITHMS:
            for repetition in range(1,args.repetitions+1):
                order=cells if repetition%3==1 else list(reversed(cells)) if repetition%3==2 else cells[2:]+cells[:2]
                for variant,backend in order:
                    builds=controls if variant==OLD else manifest["compiler_builds"]
                    print(f"MEASURE {algorithm} r{repetition} {variant} {backend}",flush=True)
                    row=execute(builds[backend],cfg,algorithm,variant,repetition,args,args.output,True,references[algorithm])
                    rows.append(row)
                    regenerate(args.output,rows)
                    print(f"  BookSim CPU estimate {row['booksim_cpu_seconds_estimate']:.4f}s; {row['collective_cycles']} cycles; reference match",flush=True)
        for algorithm in ALGORITHMS:
            for backend in ("baseline","optimized"):
                print(f"UNSAMPLED {algorithm} {backend}",flush=True)
                row=execute(manifest["compiler_builds"][backend],cfg,algorithm,"noexceptions_control",0,args,args.output,False,references[algorithm])
                unsampled.append(row)
                save_csv(args.output/"controls.csv",sorted({k for r in unsampled for k in r}),unsampled)
        summaries=regenerate(args.output,rows)
        if len(summaries)!=4 or not all(s["all_checks_passed"] and s["repetitions"]==args.repetitions for s in summaries):
            raise RuntimeError("Incomplete summaries")
        comparison=[]
        for algorithm in ALGORITHMS:
            get=lambda variant,backend:statistics.median(r["booksim_cpu_seconds_estimate"] for r in rows if r["variant"]==variant and r["backend"]==backend and r["algorithm"]==algorithm)
            b0,o0,b1,o1=get(OLD,"baseline"),get(OLD,"optimized"),get(NEW,"baseline"),get(NEW,"optimized")
            comparison.append({"algorithm":algorithm,"input_mib":1,"repetitions":args.repetitions,
                "previous_baseline_cpu_seconds":b0,"previous_optimized_cpu_seconds":o0,
                "new_baseline_cpu_seconds":b1,"new_optimized_cpu_seconds":o1,
                "previous_backend_speedup":b0/o0,"new_backend_speedup":b1/o1,
                "incremental_gain_baseline":b0/b1,"incremental_gain_optimized":o0/o1,
                "optimized_time_reduction_pct":100*(1-o1/o0),
                "collective_cycles":references[algorithm]["collective_cycles"],"all_checks_passed":True})
        save_csv(args.output/"compiler_comparison.csv",list(comparison[0]),comparison)
        for b in manifest["compiler_builds"].values():
            if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("Final executable changed")
            for name,h in b["profiles_sha256"].items():
                if sha(Path(b["binary"]).parent/"profiles"/name)!=h:raise RuntimeError("Training data changed during measurement")
        for b in controls.values():
            if sha(Path(b["binary"]))!=b["binary_sha256"]:raise RuntimeError("Prior executable changed")
        verify()
        manifest.update(status="complete",sampled_runs=len(rows),training_runs=len(training),unsampled_controls=len(unsampled),rejected_input_checks=len(errors),all_checks_passed=True)
        for row in comparison:print(json.dumps(row),flush=True)
    except BaseException as error:
        manifest.update(status="failed_or_interrupted",error=repr(error))
        raise
    finally:
        write_json(manifest_path,manifest)
    print(f"Complete: {args.output}",flush=True)


if __name__=="__main__":main()
