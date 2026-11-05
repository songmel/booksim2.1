#!/usr/bin/env python3
"""Explain size scaling using existing completed measurements; no simulation."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parent
CELLS=("baseline_o3","baseline_final","optimized_final")
ALGORITHMS=("ring1d","alltoall")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_csv(path, rows):
    with path.open("w",newline="") as output:
        writer=csv.DictWriter(output,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    base="table1_mesh_16GBps_flit16B_ring1d_alltoall_final_"
    parser.add_argument("--small",type=Path,default=ROOT/"results"/(base+"1MiB_3reps"))
    parser.add_argument("--large",type=Path,default=ROOT/"results"/(base+"4MiB_3reps"))
    parser.add_argument("--output",type=Path,default=ROOT/"results/input_scaling_analysis_1to4MiB_16GBps_flit16B")
    args=parser.parse_args()
    directories=(args.small.resolve(),args.large.resolve())
    manifests=[json.loads((p/"manifest.json").read_text()) for p in directories]
    assert all(m["status"]=="complete" and m["all_checks_passed"] for m in manifests)
    assert tuple(m["input_mib"] for m in manifests)==(1,4)
    assert manifests[0]["builds"]==manifests[1]["builds"]
    assert manifests[0]["config_sha256"]==manifests[1]["config_sha256"]
    assert manifests[0]["effective_config"]==manifests[1]["effective_config"]
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    if (args.output/"metadata.json").exists():
        previous=json.loads((args.output/"metadata.json").read_text())
        for name,h in previous["source_files"].items():assert sha(Path(name))==h
    normalized=[]
    source_hashes={}
    controls={}
    for directory,manifest in zip(directories,manifests):
        metadata=json.loads((directory/"plots/metadata.json").read_text())
        for name,h in metadata["sources"].items():assert sha(directory/name)==h
        for name in ("raw.csv","summary.csv","controls.csv","manifest.json"):
            source_hashes[str(directory/name)]=sha(directory/name)
        rows=list(csv.DictReader((directory/"raw.csv").open()))
        controls[manifest["input_mib"]]=list(csv.DictReader((directory/"controls.csv").open()))
        size=manifest["input_mib"]
        for algorithm in ALGORITHMS:
            for cell in CELLS:
                selected=[r for r in rows if r["algorithm"]==algorithm and r["variant"]==cell]
                assert len(selected)==3 and all(r["validation_passed"]=="True" for r in selected)
                value=lambda key:statistics.median(float(r[key]) for r in selected)
                cpu=value("booksim_cpu_seconds_estimate")
                flits=int(selected[0]["flits"]);cycles=int(selected[0]["collective_cycles"])
                normalized.append(dict(algorithm=algorithm,variant=cell,input_mib=size,
                    cpu_seconds=cpu,cpu_seconds_per_mib=cpu/size,cpu_ns_per_flit=cpu*1e9/flits,
                    cpu_min_seconds=min(float(r["booksim_cpu_seconds_estimate"]) for r in selected),
                    cpu_max_seconds=max(float(r["booksim_cpu_seconds_estimate"]) for r in selected),
                    flits=flits,packets=int(selected[0]["packets"]),collective_cycles=cycles,
                    flits_per_cycle=flits/cycles,flit_latency_cycles=value("flit_latency"),
                    average_hops=value("average_hops"),sampled_total_run_seconds=value("run_seconds")))
    lookup={(r["input_mib"],r["algorithm"],r["variant"]):r for r in normalized}
    small,large=(m["input_mib"] for m in manifests)
    comparison=[];gains=[]
    for algorithm in ALGORITHMS:
        for cell in CELLS:
            a,b=lookup[small,algorithm,cell],lookup[large,algorithm,cell]
            comparison.append(dict(algorithm=algorithm,variant=cell,small_input_mib=small,large_input_mib=large,
                cpu_time_scaling=b["cpu_seconds"]/a["cpu_seconds"],flit_scaling=b["flits"]/a["flits"],
                packet_scaling=b["packets"]/a["packets"],cycle_scaling=b["collective_cycles"]/a["collective_cycles"],
                small_cpu_ns_per_flit=a["cpu_ns_per_flit"],large_cpu_ns_per_flit=b["cpu_ns_per_flit"],
                unit_cost_change_pct=100*(b["cpu_ns_per_flit"]/a["cpu_ns_per_flit"]-1)))
        for base in CELLS[:2]:
            a,b=lookup[small,algorithm,base],lookup[large,algorithm,base]
            oa,ob=lookup[small,algorithm,"optimized_final"],lookup[large,algorithm,"optimized_final"]
            saved_a=a["cpu_seconds"]-oa["cpu_seconds"];saved_b=b["cpu_seconds"]-ob["cpu_seconds"]
            gains.append(dict(algorithm=algorithm,baseline_variant=base,small_speedup=a["cpu_seconds"]/oa["cpu_seconds"],
                large_speedup=b["cpu_seconds"]/ob["cpu_seconds"],small_seconds_saved=saved_a,
                large_seconds_saved=saved_b,absolute_savings_scaling=saved_b/saved_a))
    save_csv(args.output/"normalized.csv",normalized)
    save_csv(args.output/"scaling.csv",comparison)
    save_csv(args.output/"savings.csv",gains)
    rows=[]
    for algorithm in ALGORITHMS:
        for cell in ("baseline_o3","optimized_final"):
            a,b=lookup[small,algorithm,cell],lookup[large,algorithm,cell]
            rows.append(f"| {algorithm} | {cell} | {a['cpu_seconds_per_mib']:.3f} | {b['cpu_seconds_per_mib']:.3f} | {a['cpu_ns_per_flit']/1000:.3f} | {b['cpu_ns_per_flit']/1000:.3f} |")
    report=f'''# 1→4 MiB에서 가속비가 유지되는 이유

같은 하드웨어·실행 파일·PGO에서 메시지당 패킷 수만 늘어났다. 기존 측정에서는 패킷·flit 수가 정확히 4배, 사이클은 Ring1D 3.915배 / AllToAll 3.971배 증가했다. baseline과 opt의 flit당 평균 CPU 비용 변화는 모든 조건에서 1% 미만이다. 따라서 반복 작업을 줄이는 비율이 비슷하게 유지됐다. 통신량은 실제 flit 수에 반영됐고, 저장된 두 측정의 입력 크기와 바이너리·설정 해시도 확인했다.

## 정규화한 처리 비용

CPU 시간은 BookSim 영역의 샘플링 추정 중앙값이다. 아래 flit당 값은 전체 BookSim CPU 시간을 총 flit 수로 나눈 상각 비용이며, 단일 flit 함수의 실행 시간을 직접 측정한 값이 아니다.

| 워크로드 | 실행 조건 | 1 MiB의 초/MiB | 4 MiB의 초/MiB | 1 MiB의 μs/flit | 4 MiB의 μs/flit |
|---|---|---:|---:|---:|---:|
'''+"\n".join(rows)+'''

근사적으로 `T_baseline(M) ≈ a×M`, `T_opt(M) ≈ b×M`이므로 `speedup ≈ a/b`다. 이번 두 크기에서 관측한 결과는 이 선형 모델과 일치한다. 두 지점만으로 모든 입력 크기에서 같은 관계가 유지된다고 단정하지 않는다.

## 스케줄과 동시 처리

보존된 최종 실행 파일의 `--schedule-only`로 1 MiB / 4 MiB 각각을 확인했다. 이 확인은 네트워크 시뮬레이션 실행이 아니다.

- Ring1D: 두 크기 모두 64 ranks, 64 chunks, 126 steps, 통신 연산 8,064개.
- AllToAll: 두 크기 모두 64 ranks, 64 chunks, 63 peer messages/rank, 통신 연산 4,032개.
- 메시지당 크기는 16→64 KiB, 메시지당 패킷은 32→128개다. 패킷 크기 512 B, flit 크기 16 B는 그대로다.
- 원본 `_Inject()`는 소스의 `_partial_packets`가 비었을 때 `_IssuePacket()`을 호출한다. 외부 `Generate()`는 그때 패킷 하나의 flit만 생성하고 등록한다.
- 원본 in-flight map은 flit이 retire되면 항목을 지운다. OPT1의 `FlatFlitMap`도 live entry 수에 따라 확장하며 전체 전송 flit ID를 모두 저장하지 않는다. 총 전송량 증가와 동시 live map 크기 증가는 구분해야 한다.

Ring1D의 flit 지연은 두 크기에서 30사이클로 같고, AllToAll은 약 623→617사이클이다. 평균 hops는 각각 2 / 6.333으로 동일하다. 전체 처리율도 Ring1D 62.18→63.53 flits/cycle, AllToAll 22.43→22.59 flits/cycle로 비슷하다. 이는 같은 형태의 부하를 더 오래 처리했다는 해석을 뒷받침한다. peak in-flight entry 수와 캐시 miss는 측정하지 않았으므로 동일하다고 단정하지 않는다.

## 실제 최적화 코드와의 관계

아래는 측정에 사용한 immutable source snapshot에서 확인했다.

| 옵션 | 줄이는 작업 | 입력 증가에 따른 변화 |
|---|---|---|
| OPT1 | live flit 추적의 트리 map을 재사용 가능한 flat map으로 대체하고 중복 조회 감소 | flit마다 반복되며 active map 크기가 중요 |
| OPT2 | 매 사이클의 ejection map 생성·조회 대신 고정 노드 배열 사용 | 사이클 수에 비례해 반복 |
| OPT3 | 라우터 입력/크레딧 map을 재사용 가능한 vector/고정 슬롯으로 대체 | 라우터·flit·credit 처리마다 반복 |
| OPT4 | 작은 credit VC set의 트리 노드 할당을 재사용 vector로 대체 | credit마다 반복하며 VC 수는 그대로 |
| OPT5 | 채널의 빈 Evaluate 호출을 건너뛰고 라우터만 평가 | 같은 네트워크에서 사이클마다 비슷한 호출 수 절약 |

이 변경들은 이번 부하에서 반복 단위의 처리 비용을 낮춘다. 총 메시지 크기만 늘려도 제거되는 작업 비율 자체가 자동으로 커지는 구조는 아니다. 컴파일 최적화와 기존 PGO도 두 크기에서 같은 실행 파일에 고정돼 있다. 동일 컴파일 옵션끼리의 코드 가속비도 Ring1D 약 1.391→1.393, AllToAll 약 1.176→1.174로 유지됐다.

## 절대 이득과 측정 방식 확인

기본 O3 baseline 대비 절약한 BookSim CPU 시간은 Ring1D 약 4.594→18.086초, AllToAll 약 5.307→21.244초다. 절대 절약 시간도 약 4배 증가했다. 비율이 같아도 절약 효과는 통신량에 비례해 커졌다.

외부 실행기·링크 모델은 주 가속비에서 이미 제외했다. 샘플링 추정식이 같은 비율을 강제로 만드는 것도 아니다. 세 번의 계측 실행에서 전체 Run() 중앙값으로 계산해도 Ring1D 약 1.504→1.495, AllToAll 약 1.267→1.266으로 비슷하다. 계측 없는 단발 대조 실행도 Ring1D 약 1.501→1.516, AllToAll 약 1.268→1.264로 같은 경향이다. 전체 Run() 시간은 외부 제어·링크 모델을 포함하므로 주 CPU 가속비와 혼합하지 않는다.

함수별 CPU 시간·캐시 miss·peak queue occupancy는 기존 측정에 없다. 따라서 AllToAll에서 어느 함수가 몇 %의 한계를 만드는지, OPT1–5 각각의 기여가 얼마인지는 이 자료만으로 확정할 수 없다. 이번 분석으로 확인한 원인은 크기 증가에도 단위 작업 비용과 작업 구성이 거의 유지됐다는 점이다. 세 번의 반복과 샘플링 추정치이므로 소수점 수준의 가속비 변화를 의미 있는 성능 차이로 단정하지 않는다.

`normalized.csv`, `scaling.csv`, `savings.csv`에 모든 실행 조건의 계산값을 기록했다. 새 네트워크 성능 실험은 실행하지 않았으며 기존 결과·코어·Makefile은 변경하지 않았다.
'''
    build=manifests[1]["builds"]["optimized_final"]
    frozen=Path(build["binary"]).parent
    source=frozen/"source/src"
    references=(
        ("live flit map 확장과 재사용",source/"flit_map.hpp",57),
        ("retirement 시 map 삭제",source/"trafficmanager.cpp",651),
        ("소스 패킷 큐가 비었을 때 생성",source/"trafficmanager.cpp",954),
        ("OPT5 사이클당 평가 호출 감소",source/"networks/network.cpp",203),
        ("실험 실행기의 패킷 단위 flit 생성",frozen/"experiment_sources/experiment.cpp",73),
        ("Ring1D의 고정 단계 수",frozen/"experiment_sources/ring1d_schedule.cpp",17),
        ("AllToAll의 고정 peer 메시지 수",frozen/"experiment_sources/alltoall_schedule.cpp",16))
    report+="\n측정에 사용한 코드 근거:\n\n"+"\n".join(f"- [{label}]({path}:{line})" for label,path,line in references)+"\n"
    for _,path,_ in references:source_hashes[str(path)]=sha(path)
    (args.output/"analysis.md").write_text(report)
    (args.output/"metadata.json").write_text(json.dumps(dict(source_files=source_hashes,
        script_sha256=sha(Path(__file__)),new_network_simulation_runs=0),indent=2)+"\n")
    print(args.output)


if __name__=="__main__":
    main()
