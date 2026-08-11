""" 보고서 job의 인메모리 저장소 + 스텁 (jobs.py와 형제).

인메모리 dict — 재시작 시 유실. 스케일 필요 시 Redis/DB로 이전.
스텁이라 고정 결과를 돌려준다. 실제 보고서 생성(p04-6)은 엔진 이식 때 붙인다.

**보고서는 문제 단위다**(2026-08-02). 문제 하나에 job 하나가 생기고 세션 1회에 job이
3개 만들어진다. **세션이 끝난 뒤 한꺼번에 들어온다**(2026-08-11 백엔드 설계 변경).
"""

import re
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, get_args

from app.config import get_settings
from app.engines.analysis import stages
from app.schemas.report import (
    AxisCode,
    ProblemResult,
    ReportJobStatus,
    ReportRequest,
    ReportResult,
)
from app.usage import stub_usage, to_ai_usage

# job_id -> 보고서 job 상태·결과
# M9 (redteam audit, 2026-08-05): jobs.py와 같은 문제 -- 무제한 dict라 장기가동 시
# 메모리가 계속 는다. sessions.py의 _answered와 같은 OrderedDict+상한 패턴.
_JOBS_MAX = 2000
_jobs: "OrderedDict[str, ReportJobStatus]" = OrderedDict()

# 멱등키({problemId}:{scoreRunId}) -> job_id. 재전송해도 LLM을 다시 부르지 않는다.
# 보고서는 문제마다 1건이라 중복이 곧 비용이다.
_job_id_by_idempotency_key: "OrderedDict[str, str]" = OrderedDict()

# 통과선. 미달이면 힌트 후 재질의. 총점은 만들지 않는다(ProblemResult 주석 참고).
_PASS_SCORE = 3


def get_job(job_id: str) -> ReportJobStatus | None:
    return _jobs.get(job_id)


# D-fix (redteam audit H12 companion, 2026-08-04): jobs.py(analyses.py)의 같은 패턴을
# 여기도 대조 없이 갖고 있었다. problem_id가 필수(ReportRequest)라 "둘 다 없으면 거부"
# 구멍은 원천적으로 없다.
def job_id_for_key(idempotency_key: str, problem_id: str) -> str | None:
    """재사용 시 problem_id가 최초 요청과 일치해야 기존 job_id를 돌려준다."""
    existing_job_id = _job_id_by_idempotency_key.get(idempotency_key)
    if existing_job_id is None:
        return None
    existing_job = _jobs.get(existing_job_id)
    if existing_job is None:
        # M9: 신원 불일치가 아니라 원본 job이 상한을 넘겨 밀려난 것 -- "처음 보는 키"와
        # 동일하게 취급한다.
        del _job_id_by_idempotency_key[idempotency_key]
        return None
    if existing_job.problem_id != problem_id:
        raise ValueError("idempotencyKey가 이전 요청의 problemId와 일치하지 않습니다")
    return existing_job_id


def create_job(body: ReportRequest, idempotency_key: str | None = None) -> ReportJobStatus:
    """QUEUED 보고서 job 생성. 아직 만들지 않는다."""
    job = ReportJobStatus(
        job_id=str(uuid.uuid4()),
        problem_id=body.problem_id,
        session_id=body.session_id,
        status="QUEUED",
    )
    _jobs[job.job_id] = job
    while len(_jobs) > _JOBS_MAX:
        _jobs.popitem(last=False)
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job.job_id
        while len(_job_id_by_idempotency_key) > _JOBS_MAX:
            _job_id_by_idempotency_key.popitem(last=False)
    return job


# problemId → 시나리오. 백엔드가 세 모양(완주 / L2 종료 / L1 종료)을 각각 불러
# 파싱 코드를 짤 수 있게 한다. 모르는 id는 완주로 준다.
# (점수, 힌트사용) — 도달한 단계만. 나머지는 status=NOT_REACHED로 채운다.
_STUB_SCRIPTS: dict[str, list[tuple[int, int]]] = {
    "prob-stub-1": [(4, 0), (4, 1), (3, 0), (5, 2)],  # 완주. L4는 힌트 2개 쓰고 통과
    "prob-stub-2": [(3, 0), (2, 2)],                  # L2에서 힌트 소진 후 미달 → 재시험
    "prob-stub-3": [(2, 2)],                          # L1에서 종료 → 재시험
}


def _stub_problem(problem_id: str, problem_no: int = 1) -> ProblemResult:
    """고정 매트릭스 하나. 백엔드가 파싱 코드를 짤 수 있도록 실제 모양을 준다.

    **DB `problem_stage` 한 행과 같은 모양이다** — 질문·힌트1·힌트2 슬롯에 점수가
    흩어져 들어간다. 힌트를 쓴 시나리오는 앞 슬롯이 미통과로 채워져 있어야 DB CHECK를
    통과한다("질문 미통과일 때만 첫 힌트 답변이 있다").
    """
    axes = list(get_args(AxisCode))
    reached = _STUB_SCRIPTS.get(problem_id, _STUB_SCRIPTS["prob-stub-1"])
    slots = ("question", "first_hint", "second_hint")

    stages = []
    for i, axis in enumerate(axes):
        if i >= len(reached):
            stages.append({"axis_code": axis, "status": "NOT_REACHED"})
            continue
        score, hints = reached[i]
        row: dict[str, Any] = {"axis_code": axis}
        # 힌트를 쓴 만큼 앞 슬롯은 미통과로 채운다 — DB CHECK가 "질문 미통과일 때만
        # 첫 힌트 답변이 있다"를 강제하므로 건너뛴 슬롯이 있으면 INSERT가 깨진다.
        for used in range(hints):
            row[f"{slots[used]}_score"] = _PASS_SCORE - 1
            row[f"{slots[used]}_passed"] = False
        row[f"{slots[hints]}_score"] = score
        row[f"{slots[hints]}_passed"] = score >= _PASS_SCORE
        row["status"] = "PASSED" if score >= _PASS_SCORE else "NOT_PASSED"
        stages.append(row)

    reached = 0
    for row in stages:
        if not (row.get("question_passed") or row.get("first_hint_passed")
                or row.get("second_hint_passed")):
            break
        reached += 1

    return ProblemResult.model_validate(
        {
            # 요청 problemNo를 그대로 돌려준다. 1로 고정하면 세션 하나에서 나오는
            # 보고서 3건이 전부 problemNo=1이 되어 화면의 "문제 2 / 3"과 어긋난다.
            "problem_no": problem_no,
            "problem_id": problem_id,
            "reached_stage": reached,
            "stages": stages,
        }
    )


def _stub_result(problem_id: str, problem_no: int = 1) -> ReportResult:
    problem = _stub_problem(problem_id, problem_no)
    passed = {s.axis_code: s.passed for s in problem.stages}
    return ReportResult(
        report_markdown="# [stub] 검증 보고서\n\n실제 보고서는 엔진 이식 후 생성됩니다.",
        narrative={
            "summary": "[stub] 실제 서술은 엔진이 만듭니다.",
            # 🔴 예전엔 둘 다 빈 배열이라 백엔드가 `ReportNote` 모양을 한 번도 못 봤다
            # (teachId·studyPointer가 어디 붙는지). gaps에만 teachId가 있다는 규칙도
            # 실제 값으로 보여준다.
            "strengths": [
                {"axis": "[stub] 예외 처리", "detail": "[stub] 답변에서 근거를 인용한 서술이 들어갑니다.",
                 "teach_id": None, "study_pointer": None},
            ],
            "gaps": [
                {"axis": "[stub] 동시성", "detail": "[stub] 부족했던 지점의 서술이 들어갑니다.",
                 "teach_id": "teach-stub-1", "study_pointer": "unit-stub-1의 12~13쪽을 다시 보세요"},
            ],
            "autonomy_note": "[stub] 힌트 없이 답한 부분과 힌트 후 답한 부분의 차이가 들어갑니다.",
            "unreached_axes": [s.axis_code for s in problem.stages if s.status == "NOT_REACHED"],
        },
        problem=problem,
        curriculum_refs=[
            {"teachId": "teach-stub-1", "unitId": "unit-stub-1", "sourcePages": [12, 13]}
        ],
        # 재시험 기준: L1·L2 둘 다 통과해야 재시험이 아니다(scoring.RETEST_TRIGGER_AXES).
        retest=not all(passed.get(axis) for axis in ("L1", "L2")),
        # 서술이 실패했을 때만 true. 스텁은 항상 서술이 있으므로 false를 명시한다.
        narrative_failed=False,
        versions={
            # 🔴 `"stub-0"`은 **실제 값이 아니다**(2026-08-12 교체). `ReportVersions.
            # modelCode`는 `aiUsage.modelCode`와 같은 값이고 백엔드가 그걸로
            # `ai_model.model_code`를 조회한다 — 등록 안 된 문자열이면 조회가 빈다.
            # 실경로와 같은 기본값(채점 모델)을 그대로 싣는다.
            "model_code": get_settings().model_code_session,
            "prompt_version": stages.manifest_version(),
            "rubric_version": "scoring-2026-08-02",
        },
    )


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(row: dict[str, Any]) -> dict[str, Any]:
    """와이어(camelCase) 키를 엔진이 읽는 이름으로 바꾼다.

    🔴 **`transcript`가 `list[dict]` 원시 타입이라 pydantic이 별칭을 안 풀어준다.**
    다른 필드는 스키마가 `alias_generator`로 변환하는데 여기만 그대로 통과한다.
    안 바꾸면 엔진이 `axis_code`를 찾다가 `axisCode`를 못 보고 **턴이 하나도 없는
    것처럼 계산한다** — 도달 0단·재시험 True가 되고 에러는 안 난다
    (2026-08-02 실호출에서 발견: 턴 3개를 넘겼는데 모델이 "문답 기록이 없다"고 썼다).
    """
    return {_CAMEL.sub("_", key).lower(): value for key, value in row.items()}


def _real_result(body: ReportRequest, job: ReportJobStatus,
                 idempotency_key: str | None, trace_id: str | None) -> ReportResult:
    """p04-6으로 서술을 만들고, 판정은 transcript에서 결정론으로 계산한다.

    `problemNo`는 요청이 주면 그대로 돌려주고 없으면 1이다. 예전에는 1로 고정했는데,
    **보고서가 문제 단위라 세션 하나에 3건이 나오고 세 건 모두 problemNo=1이 찍혔다**
    (2026-08-03 실측). 화면은 "문제 2 / 3"을 그리는데 보고서만 1이라 조용히 어긋난다.

    원장(`job.ai_usage`)을 여기서 채우는 이유는 **검증 실패로 결과를 버려도 태운
    토큰은 남겨야 하기 때문**이다(jobs.py와 같은 순서). 아래 model_validate가 터지면
    호출자는 FAILED로 적고, 그때도 원장은 이미 채워져 있다.
    """
    from app.engines.analysis import report as report_engine

    # 기본값은 **채점과 같은 모델**이다(`model_code_session`). 분석 기본값
    # (nemotron-ultra)은 리포트에 너무 느리다 — 세션 끝에 문제 수만큼 한꺼번에
    # 들어오는데(2026-08-11) 건당 지연이 그대로 배치 전체 지연이 된다.
    model_code = body.provider_model_code or get_settings().model_code_session
    built = report_engine.build(
        body.problem_id, body.problem_no or 1,
        [_to_snake(t if isinstance(t, dict) else dict(t)) for t in body.transcript],
        model_code=model_code,
        teaches=body.teaches,
        analysis_documents=body.analysis_documents,
    )
    # ⚠️ contextId가 report_snapshot.snapshot_id여야 하는데 **AI는 그 값을 모른다**
    # (스냅샷은 Spring이 저장할 때 생긴다). jobId를 넣고 Spring이 교체한다.
    job.ai_usage = to_ai_usage(built.usages, "REPORT_SNAPSHOT", job.job_id,
                               feature_code="REPORT_GENERATION",
                               idempotency_key=idempotency_key, trace_id=trace_id)
    return ReportResult.model_validate({
        "report_markdown": built.report_markdown,
        "narrative": built.narrative,
        "problem": built.problem,
        "curriculum_refs": built.curriculum_refs,
        "retest": built.retest,
        "narrative_failed": built.narrative_failed,
        "versions": {
            "model_code": model_code,
            "prompt_version": stages.manifest_version(),
            "rubric_version": "scoring-2026-08-02",
        },
    })


def run_report(job_id: str, body: ReportRequest | None = None, *,
               idempotency_key: str | None = None, trace_id: str | None = None) -> None:
    """백그라운드 워커. QUEUED → RUNNING → SUCCEEDED."""
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    try:
        if get_settings().engine_mode == "real" and body is not None:
            job.result = _real_result(body, job, idempotency_key, trace_id)
        else:
            job.result = _stub_result(job.problem_id or "prob-stub-1",
                                      (body.problem_no if body else None) or 1)
            # 실경로와 같은 자리에 같은 모양으로 원장을 남긴다. 빈 배열이면 백엔드가
            # REPORT_SNAPSHOT 귀속(Spring이 jobId를 실제 PK로 교체하는 경로)을 못 밟는다.
            job.ai_usage = to_ai_usage(
                [stub_usage(job.result.versions.model_code,
                            input_tokens=4800, output_tokens=1100, latency_ms=33)],
                "REPORT_SNAPSHOT", job.job_id, feature_code="REPORT_GENERATION",
                idempotency_key=idempotency_key, trace_id=trace_id)
        job.status = "SUCCEEDED"
    except Exception as exc:
        job.status = "FAILED"
        job.failure_reason = str(exc)
        # 🔴 **실패해도 원장은 남긴다**(2026-08-10). `_real_result`가 build() 성공
        # 뒤에야 job.ai_usage를 채워서, 스테이지가 터지면 이미 태운 토큰이 증발했다.
        # jobs.py·면담 브리프(§3 A-5 "성공·실패 모든 봉투에 실어라")와 같은 처리다.
        # StageError가 시도마다 1건씩 usages를 들고 온다.
        burned = getattr(exc, "usages", None)
        if burned and not job.ai_usage:
            job.ai_usage = to_ai_usage(burned, "REPORT_SNAPSHOT", job.job_id,
                                       feature_code="REPORT_GENERATION",
                                       idempotency_key=idempotency_key, trace_id=trace_id)
    finally:
        job.completed_at = datetime.now(timezone.utc)
