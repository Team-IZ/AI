""" 보고서 job의 인메모리 저장소 + 스텁 (jobs.py와 형제).

인메모리 dict — 재시작 시 유실. 스케일 필요 시 Redis/DB로 이전.
스텁이라 고정 결과를 돌려준다. 실제 보고서 생성(p04-6)은 엔진 이식 때 붙인다.
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
        session_id=body.session_id,
        status="QUEUED",
    )
    _jobs[job.job_id] = job
    return job


def _stub_problems() -> list[ProblemResult]:
    """고정 매트릭스. 백엔드가 파싱 코드를 짤 수 있도록 실제 모양을 준다.

    3문제 — 완주 / L2에서 종료 / L1에서 종료.
    힌트 상한(0회 5점 / 1회 4점 / 2회 3점)이 적용된 결과를 보여주는 것이 목적이다.
    """
    axes = list(get_args(AxisCode))

    # (원점수, 힌트사용) — 도달한 단계만. 나머지는 attemptCount=0으로 채운다.
    scripts: list[tuple[str, list[tuple[int, int]]]] = [
        ("prob-stub-1", [(4, 0), (4, 1), (3, 0), (5, 2)]),  # 완주. L4는 상한 3에 걸린다
        ("prob-stub-2", [(3, 0), (2, 2)]),                  # L2에서 힌트 소진 후 미달
        ("prob-stub-3", [(2, 2)]),                          # L1에서 종료 → 재시험
    ]
    caps = {0: 5, 1: 4, 2: 3}
    autonomy = {0: "SELF", 1: "SELF_MAINTAINED", 2: "PARTIAL"}

    problems = []
    for problem_no, (problem_id, reached) in enumerate(scripts, start=1):
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

        problems.append(
            ProblemResult.model_validate(
                {
                    "problem_no": problem_no,
                    "problem_id": problem_id,
                    "total_score": sum(s.get("confirmed_score") or 0 for s in stages),
                    "max_score": len(axes) * _MAX_PER_STAGE,
                    "stages": stages,
                }
            )
        )
    return problems


def _stub_result() -> ReportResult:
    problems = _stub_problems()
    return ReportResult(
        report_markdown="# [stub] 검증 보고서\n\n실제 보고서는 엔진 이식 후 생성됩니다.",
        problems=problems,
        curriculum_refs=[
            {"teachId": "teach-stub-1", "unitId": "unit-stub-1", "sourcePages": [12, 13]}
        ],
        # 재시험 기준: L1에서 막힌 문제만. scoring-config.js의 triggerAxis.
        retest_targets=[p.problem_id for p in problems if not p.stages[0].passed],
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
        job.result = _stub_result()
        job.status = "SUCCEEDED"
    except Exception as exc:
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)
