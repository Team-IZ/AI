""" D-bench4 재채점: 후보 20개 API는 다시 안 부른다.

이미 수집된 raw 시행(deepseek_v4_flash_replacement_raw.json)의 struct_ok==True인
것만 골라 새 Sonnet-기준점 판단 프롬프트(_judge_grading/_judge_report/
_judge_interview_brief, D-bench4)로 다시 채점하고 summary를 재계산한다.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "benchmarks"))

from deepseek_v4_flash_replacement import (  # noqa: E402
    GRADING_CASES, _judge_grading, _judge_report, _judge_interview_brief, summarize,
)

RESULTS_DIR = _REPO_ROOT / "benchmarks" / "results"
RAW_PATH = RESULTS_DIR / "deepseek_v4_flash_replacement_raw.json"

_CASE_BY_ID = {c["case_id"]: c for c in GRADING_CASES}


def _rejudge_one(trial: dict) -> dict:
    role = trial["role"]
    if not trial.get("ok") or not trial.get("struct_ok"):
        return trial
    if role == "grading":
        case = _CASE_BY_ID[trial["case_id"]]
        trial["judge"] = _judge_grading(trial, case)
    elif role == "report":
        trial["judge"] = _judge_report(trial)
    else:
        trial["judge"] = _judge_interview_brief(trial)
    return trial


def main() -> None:
    data = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    trials = data["trials"]
    targets = [t for t in trials if t.get("ok") and t.get("struct_ok")]
    print(f"재채점 대상: {len(targets)}건 (전체 {len(trials)}건 중)", file=sys.stderr, flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_rejudge_one, t): t for t in targets}
        for fut in as_completed(futures):
            fut.result()
            done += 1
            print(f"[{done}/{len(targets)}] 재채점 완료", file=sys.stderr, flush=True)

    data["judge_note"] = "D-bench4: Sonnet 기준점 대조 방식으로 재채점(sonnet_reference.json)"
    RAW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = summarize(trials)
    (RESULTS_DIR / "deepseek_v4_flash_replacement_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("완료: raw·summary 갱신됨", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
