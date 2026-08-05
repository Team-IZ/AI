""" 분석 job의 인메모리 저장소와 수명주기. 

프로세스 재시작 시 유실, 워커 1개에서만 동작
영속/스케일 필요시 Redis 나 DB로 이전, 지금은 단일 프로세스 개발용
"""

import uuid

from collections import OrderedDict
from datetime import datetime, timezone

from app.engines.base import AnalysisEngine
from app.schemas.analysis import AnalysisJobStatus, AnalysisRequest, AnalysisResult
from app.usage import to_ai_usage

# job_id
# M9 (redteam audit, 2026-08-05): 무제한 dict였다 -- 업로드 상한(H13)과 별개로 job
# 자체가 영원히 안 지워져 장기가동 시 메모리가 계속 는다. sessions.py의 _answered와
# 같은 OrderedDict+상한 패턴. COST: 상한을 넘긴 시점에 아직 QUEUED/RUNNING인 job이
# 있으면(동시 in-flight job이 상한 개수만큼 쌓여야 함) 밀려날 수 있다 -- run_analysis는
# 지역 참조로 계속 도니 실행 자체는 안 끊기지만, 그 job의 GET 조회는 404가 된다.
_JOBS_MAX = 2000
_jobs: "OrderedDict[str, AnalysisJobStatus]" = OrderedDict()

# 멱등성 키 -> job_id. 같은 키 재요청 시 새 job 안 만들고 처음 id 반환
_job_id_by_idempotency_key: "OrderedDict[str, str]" = OrderedDict()

def get_job(job_id: str) -> AnalysisJobStatus | None:
    return _jobs.get(job_id)

# D-fix (redteam audit H12, 2026-08-04): job_id_for_key()가 idempotency_key만 보고 신원
# 대조 없이 기존 job_id를 그대로 돌려줬다 -- 저엔트로피 멱등키(submissionId:attemptNo)를
# 추측/재사용하면 남의 job_id(그리고 그 결과인 제출 코드 전문)를 받아갈 수 있었다.
#   WHY: 원래 감사가 제안한 "(caller, key) 복합키"는 이 서비스에 caller 개념이 없어서
#   (deps.py: 단일 공유 시크릿, Spring이 유일 호출자) 적용 불가 -- 대신 재사용 요청의
#   submission_id/attempt_id가 최초 요청 때와 같은지 대조한다.
#   COST: 둘 다 optional(AnalysisRequest)이라 "둘 다 없으면 무조건 거부"를 명시적으로
#   넣어야 한다 -- 안 그러면 그냥 둘 다 생략한 재사용 요청이 None==None으로 통과해버려
#   방어가 무력화된다.
def job_id_for_key(idempotency_key: str, submission_id: str | None, attempt_id: str | None) -> str | None:
    """재사용 시 신원이 최초 요청과 일치해야 기존 job_id를 돌려준다. 불일치/신원부재는 예외."""
    existing_job_id = _job_id_by_idempotency_key.get(idempotency_key)
    if existing_job_id is None:
        return None
    existing_job = _jobs.get(existing_job_id)
    if existing_job is None:
        # M9: 신원 불일치가 아니라 원본 job이 상한을 넘겨 밀려난 것 -- "처음 보는 키"와
        # 동일하게 취급해 새 job을 만들게 한다(안 그러면 정상 재시도가 409로 막힌다).
        del _job_id_by_idempotency_key[idempotency_key]
        return None
    if not submission_id and not attempt_id:
        raise ValueError("idempotencyKey 재사용에는 submissionId/attemptId 중 최소 하나가 필요합니다")
    if existing_job.submission_id != submission_id or existing_job.attempt_id != attempt_id:
        raise ValueError("idempotencyKey가 이전 요청의 submissionId/attemptId와 일치하지 않습니다")
    return existing_job_id

def create_job(body: AnalysisRequest, idempotency_key: str | None) -> AnalysisJobStatus:
    """ QUEUED 상태 job을 만들어 저장. 아직 분석 X -> result 없음 """
    job = AnalysisJobStatus(
        job_id=str(uuid.uuid4()),
        attempt_id=body.attempt_id,
        submission_id=body.submission_id,
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

def run_analysis(
    job_id: str, body: AnalysisRequest, engine: AnalysisEngine, zip_bytes: bytes | None,
    *, idempotency_key: str | None = None, trace_id: str | None = None,
) -> None:
    """ 백그라운드 워커. 상태 전이시키며 분석 수행

    QUEUED -> RUNNING -> SUCCEEDED (엔진 터지면 FAILED)
    202 응답 나간 뒤 실행, 호출자는 폴링(GET)으로 이 전이 관측.

    _jobs에 담긴 객체를 그 자리에서 수정 -> GET이 같은 객체 돌려주므로 변경 그대로 보임
    """
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    try:
        raw = engine.analyze(body.model_dump(), zip_bytes)
        # 원장은 결과와 별개다. **검증 실패로 결과를 버려도 태운 토큰은 남긴다** —
        # 그래서 model_validate보다 먼저 떼어낸다.
        job.ai_usage = to_ai_usage(raw.pop("ai_usage", []), "ANALYSIS", job_id,
                                   idempotency_key=idempotency_key, trace_id=trace_id)
        result = AnalysisResult.model_validate(raw)  # 계약 위반은 여기서 예외

        # 요구사항 판정은 빠짐없이 와야 한다. 모델이 몇 개를 조용히 빠뜨리면
        # 판정 안 된 요구사항이 통과로 기록된다 — 여기서 막는다.
        if len(result.requirement_results) != len(body.requirements):
            raise ValueError(
                f"requirementResults 개수가 요청 requirements와 다릅니다: "
                f"{len(result.requirement_results)} != {len(body.requirements)}"
            )

        job.result = result
        # 🔴 **부분 성공을 SUCCEEDED로 덮지 않는다** (2026-08-03, `analysis_job.status`에
        # `PARTIAL`이 있다). 요구사항 판정은 문답과 독립이라 실패해도 문제·질문·힌트는
        # 정상으로 나가는데(엔진이 verdict='F' + "판정 실패" note로 채운다), 그걸
        # SUCCEEDED로 보내면 **화면에 "요구사항 전부 미충족"이 사실처럼 뜬다.**
        # 실호출에서 실제로 나오는 경로다.
        failed_judgements = sum(
            1 for r in result.requirement_results
            if (r.note or "").startswith("판정 실패")
        )
        job.status = "PARTIAL" if failed_judgements else "SUCCEEDED"
        if failed_judgements:
            job.failure_reason = (
                f"요구사항 판정 {failed_judgements}건이 실패했습니다. "
                f"문제·질문·힌트는 정상입니다"
            )
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 job FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
        # 🔴 **실패해도 원장은 남긴다.** 콜은 이미 나갔고 백엔드가 그걸로 비용을
        # 집계한다. AnalysisFailed가 실패 지점까지의 usage를 들고 온다.
        burned = getattr(exc, "ai_usage", None)
        if burned and not job.ai_usage:
            job.ai_usage = to_ai_usage(burned, "ANALYSIS", job_id,
                                       idempotency_key=idempotency_key, trace_id=trace_id)
    finally:
        job.completed_at = datetime.now(timezone.utc)