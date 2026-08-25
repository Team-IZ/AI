""" 분석 job의 저장소와 수명주기.

저장소는 app/job_store.py로 뺐다 -- 기본은 프로세스 인메모리(App Runner·로컬·
테스트), `JOB_STORE_BACKEND=dynamodb`면 ECS 다중 태스크에서도 안전한 DynamoDB
(app/config.py의 D-job-store 주석 참고).
"""

import logging
import uuid

from datetime import datetime, timezone
from typing import Any

from app.concurrency import HEAVY_JOB_CONCURRENCY
from app.engines.analysis import fetch as fetch_engine
from app.engines.analysis import rules
from app.job_store import get_job_store

log = logging.getLogger(__name__)
from app.engines.base import AnalysisEngine
from app.schemas.analysis import AnalysisJobStatus, AnalysisRequest, AnalysisResult
from app.usage import to_ai_usage

_store = get_job_store("analysis", AnalysisJobStatus)

def _translate_failure_code(code: str) -> str:
    """fetch.py 내부 코드 -> analysis_job.failure_code DB 15종.

    백엔드 회신(2026-08-07)으로 ZIP 검증 5종이 DB CHECK에 합류하면서
    `VERIFICATION_FAILURE_CODES` 11종이 전부 DB 값과 같은 이름이 됐다 -- 그래서
    이름을 옮기던 딕셔너리가 통째로 사라졌다. 그전에는 ZIP 4종(FILE_TOO_LARGE·
    ARCHIVE_INVALID·PROHIBITED_FILE·GIT_LOG_MISSING)이 전부 SOURCE_UNREACHABLE
    하나로 뭉개져서 교육생에게 사유를 구분해 안내할 수 없었다.

    DB에 이름이 없는 것은 `JOB_ONLY_FAILURE_CODES` 2종뿐이고("검증했던 소스가 그
    형태로 더는 없다"는 계열이라 SOURCE_UNREACHABLE이 맞다), fetch.py에 새 코드가
    늘었는데 DB CHECK에 없는 경우도 같은 값으로 떨어져 DB 밖의 값이 새지 않는다.
    test_jobs.py의 드리프트 핀 테스트가 그 상황을 CI에서 미리 잡는다.
    """
    return code if code in fetch_engine.VERIFICATION_FAILURE_CODES else "SOURCE_UNREACHABLE"

def get_job(job_id: str) -> AnalysisJobStatus | None:
    return _store.get(job_id)

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
    existing_job_id = _store.get_id_by_idempotency_key(idempotency_key)
    if existing_job_id is None:
        return None
    existing_job = _store.get(existing_job_id)
    if existing_job is None:
        # M9: 신원 불일치가 아니라 원본 job이 상한(인메모리)을 넘겨 밀려났거나
        # TTL(DynamoDB)로 정리된 것 -- "처음 보는 키"와 동일하게 취급해 새 job을
        # 만들게 한다(안 그러면 정상 재시도가 409로 막힌다).
        _store.delete_idempotency_key(idempotency_key)
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

    _store.create(job)
    if idempotency_key:
        _store.set_idempotency_key(idempotency_key, job.job_id)
    return job

def run_analysis(
    job_id: str, body: AnalysisRequest, engine: AnalysisEngine, zip_bytes: bytes | None,
    *, idempotency_key: str | None = None, trace_id: str | None = None,
) -> None:
    """`HEAVY_JOB_CONCURRENCY` 상한 안에서만 `_run_analysis_locked`를 실제로 돌린다.

    이 상한은 curricula.py의 교안분석 job과 공유한다(app/concurrency.py 주석 참고) --
    따로 들면 "코드분석 6개 + 교안분석 6개 동시"로 인시던트가 재현된다.
    상한을 넘는 job은 여기서 대기한다 -- 그동안 job.status는 create_job이 세팅한
    QUEUED 그대로다(_run_analysis_locked가 그 안에서 RUNNING으로 바꾼다).
    """
    with HEAVY_JOB_CONCURRENCY:
        _run_analysis_locked(job_id, body, engine, zip_bytes,
                              idempotency_key=idempotency_key, trace_id=trace_id)


def _run_analysis_locked(
    job_id: str, body: AnalysisRequest, engine: AnalysisEngine, zip_bytes: bytes | None,
    *, idempotency_key: str | None = None, trace_id: str | None = None,
) -> None:
    """ 백그라운드 워커. 상태 전이시키며 분석 수행

    QUEUED -> RUNNING -> SUCCEEDED (엔진 터지면 FAILED)
    202 응답 나간 뒤 실행, 호출자는 폴링(GET)으로 이 전이 관측.

    인메모리 백엔드에선 객체를 그 자리에서 수정하는 것만으로 GET에 그대로
    보인다(참조 공유). DynamoDB 백엔드에선 프로세스 경계를 넘어야 하므로
    `_store.save(job)`를 명시적으로 불러야 다른 태스크의 GET에도 보인다 --
    그래서 상태가 바뀌는 지점(RUNNING 진입, 그리고 종료 시점 finally)마다
    저장한다.
    """
    job = _store.get(job_id)
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)
    _store.save(job)
    log.info("분석 시작 job=%s method=%s scope=%s teaches=%d requirements=%d",
             job_id, body.method, body.problem_scope,
             len(body.teaches), len(body.requirements))

    try:
        raw = engine.analyze(body.model_dump(), zip_bytes)
        # 원장은 결과와 별개다. **검증 실패로 결과를 버려도 태운 토큰은 남긴다** —
        # 그래서 model_validate보다 먼저 떼어낸다.
        # contextType=ANALYSIS_JOB, contextId=jobId다 — 백엔드가 재분석(execution_no가
        # 다른 두 번째 실행)의 비용을 실행별로 구분하려고 이 조합을 택했다(2026-08-07
        # 회신, 제안서 D). ⚠️ 옛 SUBMISSION+submissionId는 폐기다 — 같은 제출을 두 번
        # 분석하면 두 실행의 토큰이 한 덩어리로 합쳐져 어느 쪽이 비쌌는지 안 보였다.
        job.ai_usage = to_ai_usage(raw.pop("ai_usage", []), "ANALYSIS_JOB", job_id,
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
    except rules.ScanLimitExceeded as exc:
        # 엔진 스캔 단계의 규모 상한이다. fetch 단계가 아니라서 아래 FetchError 분기에
        # 안 걸리고, 잡아주지 않으면 catch-all의 MODEL_ERROR가 된다 -- LLM은 한 번도
        # 안 불렸는데(ai_usage 비어 있음) 백엔드가 "모델 실패"로 읽는다.
        job.status = "FAILED"
        job.failure_code = "FILE_TOO_LARGE"
        job.failure_reason = str(exc)
    except fetch_engine.FetchError as exc:
        # fetch 실패(저장소 접근·ZIP 검증) -- FetchError가 이미 세부 사유를 들고 있다
        # (호스트 거부/브랜치 없음/ZIP 손상 등, fetch.py의 11종 어휘). 그 어휘는
        # analysis_job.failure_code의 DB 15종 안에 같은 이름으로 들어 있지만, 나중에
        # fetch.py에만 새 코드가 늘 수 있어 _translate_failure_code를 거친다.
        job.status = "FAILED"
        job.failure_code = _translate_failure_code(exc.failure_code)
        job.failure_reason = exc.message
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 job FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
        # 🔴 잠정(계획 §0.3) -- 엔진 내부 실패를 ANALYSIS_TIMEOUT/UNSUPPORTED_LANGUAGE/...로
        # 세분화할 신호가 없다(LlmError/StageError는 failure_code를 안 들고 있다, usage만
        # 있다). MODEL_ERROR를 catch-all로 쓴다 -- 근거 없이 더 구체적인 값을 추측하는
        # 것보다, "모델/엔진 실패"라는 사실만 정확히 담는 쪽을 택한다. (옛 PROVIDER_ERROR는
        # ai_usage 네임스페이스 값이라 analysis_job의 DB CHECK 11종엔 없었다 -- 버그였다,
        # 2026-08-07 수정)
        job.failure_code = "MODEL_ERROR"
        # 🔴 **실패해도 원장은 남긴다.** 콜은 이미 나갔고 백엔드가 그걸로 비용을
        # 집계한다. AnalysisFailed가 실패 지점까지의 usage를 들고 온다.
        burned = getattr(exc, "ai_usage", None)
        if burned and not job.ai_usage:
            job.ai_usage = to_ai_usage(burned, "ANALYSIS_JOB", job_id,
                                       idempotency_key=idempotency_key, trace_id=trace_id)
    finally:
        job.completed_at = datetime.now(timezone.utc)
        _store.save(job)
        elapsed = (job.completed_at - job.started_at).total_seconds()
        problems = len(job.result.problems) if job.result else 0
        log.info("분석 종료 job=%s status=%s %.1fs problems=%d llm=%d %s",
                 job_id, job.status, elapsed, problems, len(job.ai_usage),
                 job.failure_code or "")