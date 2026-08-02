""" 보고서 job의 인메모리 저장소 + 스텁 (jobs.py와 형제).

인메모리 dict — 재시작 시 유실. 스케일 필요 시 Redis/DB로 이전.
스텁이라 고정 결과를 돌려준다. 실제 보고서 생성(p04-6)은 엔진 이식 때 붙인다.

**보고서는 문제 단위다**(2026-08-02). 문제 하나가 끝날 때마다 job 하나가 생기고
세션 1회에 job이 3개 만들어진다.
"""

import uuid
from datetime import datetime, timezone
from typing import get_args

from app.schemas.report import (
    AxisCode,
    ProblemResult,
    ReportJobStatus,
    ReportRequest,
    ReportResult,
)

# job_id -> 보고서 job 상태·결과
_jobs: dict[str, ReportJobStatus] = {}

# 단계당 만점. scoring-config.js의 값 단계(0~5)와 같다.
_MAX_PER_STAGE = 5
# 통과선. 미달이면 힌트 후 재질의.
_PASS_SCORE = 3


def get_job(job_id: str) -> ReportJobStatus | None:
    return _jobs.get(job_id)


def create_job(body: ReportRequest) -> ReportJobStatus:
    """QUEUED 보고서 job 생성. 아직 만들지 않는다."""
    job = ReportJobStatus(
        job_id=str(uuid.uuid4()),
        problem_id=body.problem_id,
        session_id=body.session_id,
        status="QUEUED",
    )
    _jobs[job.job_id] = job
    return job


# problemId → 시나리오. 백엔드가 세 모양(완주 / L2 종료 / L1 종료)을 각각 불러
# 파싱 코드를 짤 수 있게 한다. 모르는 id는 완주로 준다.
# (원점수, 힌트사용) — 도달한 단계만. 나머지는 attemptCount=0으로 채운다.
_STUB_SCRIPTS: dict[str, list[tuple[int, int]]] = {
    "prob-stub-1": [(4, 0), (4, 1), (3, 0), (5, 2)],  # 완주. L4는 상한 3에 걸린다
    "prob-stub-2": [(3, 0), (2, 2)],                  # L2에서 힌트 소진 후 미달 → 재시험
    "prob-stub-3": [(2, 2)],                          # L1에서 종료 → 재시험
}


def _stub_problem(problem_id: str) -> ProblemResult:
    """고정 매트릭스 하나. 백엔드가 파싱 코드를 짤 수 있도록 실제 모양을 준다.

    힌트 상한(0회 5점 / 1회 4점 / 2회 3점)이 적용된 결과를 보여주는 것이 목적이라
    완주 시나리오의 L4에 상한 3에 걸리는 케이스를 넣었다.
    """
    axes = list(get_args(AxisCode))
    reached = _STUB_SCRIPTS.get(problem_id, _STUB_SCRIPTS["prob-stub-1"])
    caps = {0: 5, 1: 4, 2: 3}
    autonomy = {0: "SELF", 1: "SELF_MAINTAINED", 2: "PARTIAL"}

    stages = []
    for i, axis in enumerate(axes):
        if i >= len(reached):
            stages.append({"axis_code": axis, "attempt_count": 0, "passed": False})
            continue
        best, hints = reached[i]
        confirmed = min(best, caps[hints])
        stages.append(
            {
                "axis_code": axis,
                "attempt_count": hints + 1,
                "passed": confirmed >= _PASS_SCORE,
                "best_score": best,
                "confirmed_score": confirmed,
                "hints_used": hints,
                "autonomy": autonomy[hints],
            }
        )

    return ProblemResult.model_validate(
        {
            "problem_no": 1,
            "problem_id": problem_id,
            "total_score": sum(s.get("confirmed_score") or 0 for s in stages),
            "max_score": len(axes) * _MAX_PER_STAGE,
            "stages": stages,
        }
    )


def _stub_result(problem_id: str) -> ReportResult:
    problem = _stub_problem(problem_id)
    passed = {s.axis_code: s.passed for s in problem.stages}
    return ReportResult(
        report_markdown="# [stub] 검증 보고서\n\n실제 보고서는 엔진 이식 후 생성됩니다.",
        problem=problem,
        curriculum_refs=[
            {"teachId": "teach-stub-1", "unitId": "unit-stub-1", "sourcePages": [12, 13]}
        ],
        # 재시험 기준: L1·L2 둘 다 통과해야 재시험이 아니다(scoring.RETEST_TRIGGER_AXES).
        retest=not all(passed.get(axis) for axis in ("L1", "L2")),
        versions={
            "model_code": "stub-0",
            "prompt_version": "stub-0",
            "rubric_version": "stub-0",
        },
    )


def run_report(job_id: str) -> None:
    """백그라운드 워커. QUEUED → RUNNING → SUCCEEDED (스텁은 항상 성공)."""
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    try:
        job.result = _stub_result(job.problem_id or "prob-stub-1")
        job.status = "SUCCEEDED"
    except Exception as exc:
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)
