#!/usr/bin/env python3
"""Remeasure original O3 versus OPT1..6 with all compiler optimizations."""
import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import shutil
import statistics

from compiler_pilot import write_json
from opt6_pilot import save_rows
from profile import ROOT, sha
from run import compare
from table_pilot import check_preserved, execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0):
        parser.error("CPU must be available")
    previous = ROOT / "results/opt6_alltoall_1MiB_16GBps_final_3reps"
    prior = json.loads((previous / "manifest.json").read_text())
    if prior["status"] != "complete":
        raise RuntimeError("OPT6 reference measurement is incomplete")
    builds = {"baseline_o3":prior["historical_builds"]["baseline_o3"],
              "opt1to6_final":prior["builds"]["opt1to6"]}
    check_preserved(builds)
    reference = next(csv.DictReader((previous / "raw.csv").open()))
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    for name in ("logs", "configs", "driver_sources"):
        (directory / name).mkdir()
    config = directory / "configs/alltoall_1MiB.cfg"
    shutil.copy2(previous / "configs/alltoall_1MiB.cfg", config)
    args.input_mib = 1
    args.hardware_name = prior["hardware"]
    args.hardware_config = prior["effective_config"]
    for name in ("opt6_compiler_total.py", "table_pilot.py", "opt6_pilot.py", "run.py", "profile.py", "compiler_pilot.py"):
        shutil.copy2(ROOT / name, directory / "driver_sources" / name)
    plan = [["baseline_o3", "opt1to6_final"], ["opt1to6_final", "baseline_o3"],
            ["baseline_o3", "opt1to6_final"]]
    manifest = dict(status="running", created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    algorithm="alltoall", input_mib=1, repetitions=3, cpu=args.cpu,
                    hardware=args.hardware_name, effective_config=args.hardware_config,
                    config_sha256=sha(config), builds=builds, plan=plan,
                    source_reference=str(previous), source_reference_manifest_sha256=sha(previous / "manifest.json"),
                    compiler_policy="Original O3 versus OPT1..6 with O3/native/LTO-auto/no-exceptions/PGO. Combined code/build upgrade, not isolated compiler or OPT6 effect.",
                    pgo_policy="Reuse the validated OPT6 AllToAll-trained executable; no additional build or training.",
                    timing="Three fresh unsampled Run wall times per binary, alternating order on one pinned CPU. Setup/final validation excluded; controller/rate adapter/online validation/output included.",
                    driver_sha256={p.name:sha(p) for p in (directory / "driver_sources").iterdir()})
    write_json(directory / "manifest.json", manifest)
    rows = []
    try:
        for repetition, order in enumerate(plan, 1):
            for name in order:
                row = execute(builds[name], name, config, "alltoall", repetition, False,
                              args, directory, reference)
                rows.append(row)
                save_rows(directory / "raw.csv", rows)
        groups = {name:[r for r in rows if r["variant"] == name] for name in builds}
        pairs = []
        for repetition in range(1, 4):
            b = next(r for r in groups["baseline_o3"] if r["repetition"] == repetition)
            o = next(r for r in groups["opt1to6_final"] if r["repetition"] == repetition)
            checks = compare(b, o)
            if not all(checks.values()):
                raise RuntimeError("Pair result mismatch")
            pairs.append(dict(repetition=repetition, **checks, baseline_seconds=b["run_seconds"],
                              optimized_seconds=o["run_seconds"], speedup=b["run_seconds"]/o["run_seconds"]))
        save_rows(directory / "pairs.csv", pairs)
        values = {name:[r["run_seconds"] for r in group] for name, group in groups.items()}
        baseline = statistics.median(values["baseline_o3"])
        optimized = statistics.median(values["opt1to6_final"])
        summary = dict(algorithm="alltoall", input_mib=1, repetitions=3,
                       comparison="original_o3_vs_opt1to6_final", metric="unsampled_run_seconds",
                       baseline_median_seconds=baseline, optimized_median_seconds=optimized,
                       baseline_min_seconds=min(values["baseline_o3"]), baseline_max_seconds=max(values["baseline_o3"]),
                       optimized_min_seconds=min(values["opt1to6_final"]), optimized_max_seconds=max(values["opt1to6_final"]),
                       speedup=baseline/optimized, speed_increase_pct=100*(baseline/optimized-1),
                       time_reduction_pct=100*(1-optimized/baseline), all_checks_passed=True,
                       collective_cycles=rows[0]["collective_cycles"], drain_cycles=rows[0]["drain_cycles"],
                       retirement_hash=rows[0]["retirement_hash"])
        save_rows(directory / "summary.csv", [summary])
        check_preserved(builds)
        manifest.update(status="complete", measured_runs=len(rows), all_checks_passed=True,
                        preserved_binaries_and_profiles=True)
        write_json(directory / "manifest.json", manifest)
        report = f"""# AllToAll 1MiB: 코드와 컴파일러 최적화의 총 효과

원본 코어의 기본 `-O3` 실행파일과 OPT1~OPT6 및 `-O3 -march=native -flto=auto -fno-exceptions`/PGO를 적용한 실행파일을 각각 새로 3회 실행했습니다. 두 실행파일의 기존 SHA와 PGO 데이터를 보존했고, 재빌드나 추가 학습은 하지 않았습니다. 최종 버전의 PGO는 앞선 실험에서 같은 AllToAll 1MiB로 학습한 결과입니다.

| 버전 | Run 시간 중앙값 (초) | 최소~최대 (초) |
|---|---:|---:|
| 원본 코어, 기본 O3 | {baseline:.6f} | {min(values['baseline_o3']):.6f}~{max(values['baseline_o3']):.6f} |
| OPT1~OPT6, 전체 컴파일러 최적화 | {optimized:.6f} | {min(values['opt1to6_final']):.6f}~{max(values['opt1to6_final']):.6f} |

**시간 단축: {summary['time_reduction_pct']:.4f}%, 속도 향상: {summary['speedup']:.6f}배(+{summary['speed_increase_pct']:.4f}%).**

이는 코드와 빌드 옵션을 합친 총 효과입니다. 앞선 동일 컴파일 옵션 비교의 21.17% 시간 단축은 별도 실험 결과이며, 이번 수치와 단순히 퍼센트를 더하거나 빼서 개별 최적화 기여를 계산하지 않습니다.

조건은 rank당 전체 1MiB, direct AllToAll, 64 rank, 8×8 Mesh, 1GHz, 16GB/s rate channel, 16B flit, 512B packet, inter-router propagation 21ns, 4VC×318 flit, packet switch holding으로 동일합니다. CPU {args.cpu}에 고정하고 실행 순서를 번갈아 배치했습니다.

시간은 샘플링을 끈 `TrafficManager::Run()` wall time입니다. 공통 controller·rate adapter·온라인 검증·credit drain·통계 출력을 포함하고, 초기화 및 최종 검증은 제외합니다. 전체 프로세스 시간이나 순수 BookSim 구간의 시간과는 경계가 다릅니다.

6회 모두 기존 참조와 cycle·traffic·retirement hash·latency/hop 통계가 일치하고 수신/credit 검사를 통과했습니다. 완료 {summary['collective_cycles']} cycles, drain {summary['drain_cycles']} cycles, retirement hash `{summary['retirement_hash']}`입니다.

[원본 측정 CSV](raw.csv), [요약](summary.csv), [짝 비교](pairs.csv), [옵션·바이너리 SHA·실행 계획](manifest.json)
"""
        (directory / "report.md").write_text(report)
        print(json.dumps(summary, indent=2), flush=True)
    except BaseException as error:
        manifest.update(status="failed", error=str(error))
        write_json(directory / "manifest.json", manifest)
        raise


if __name__ == "__main__":
    main()
