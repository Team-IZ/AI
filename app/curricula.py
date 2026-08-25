""" 교안 분석 job의 인메모리 저장소 + 스텁 (jobs.py·reports.py와 형제).

인메모리 dict — 재시작 시 유실. 스케일 필요 시 Redis/DB로 이전.
스텁이라 고정 결과를 돌려준다. 실제 추출(PDF 파싱 + LLM 정규화)은 엔진 이식 때 붙인다.

교안 분석은 LLM을 무겁게 쓴다(교안 1개에 1~2분 이상). 수업 중이 아니라
LMS 업로드 시점에 도는 것이 전제다 — 그래서 멱등키로 중복 실행을 막는다.
"""

import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from app.concurrency import HEAVY_JOB_CONCURRENCY
from app.config import get_settings
from app.schemas.curriculum import (
    CurriculumJobStatus,
    CurriculumRequest,
    CurriculumResult,
)
from app.usage import to_ai_usage

# job_id -> 교안 분석 job 상태·결과
# M9 (redteam audit, 2026-08-05): jobs.py와 같은 문제 -- 무제한 dict라 장기가동 시
# 메모리가 계속 는다. sessions.py의 _answered와 같은 OrderedDict+상한 패턴.
_JOBS_MAX = 2000
_jobs: "OrderedDict[str, CurriculumJobStatus]" = OrderedDict()

# 멱등키 -> job_id. 같은 키 재요청 시 새 job 안 만들고 처음 id 반환
_job_id_by_idempotency_key: "OrderedDict[str, str]" = OrderedDict()


def get_job(job_id: str) -> CurriculumJobStatus | None:
    return _jobs.get(job_id)


# D-fix (redteam audit H12 companion, 2026-08-04): jobs.py(analyses.py)의 같은 패턴을
# 여기도 대조 없이 갖고 있었다. 여긴 analyses.py보다 유리한 조건이다 -- version_id가
# optional이 아니라 필수(CurriculumRequest)라 "둘 다 없으면 거부" 구멍이 원천적으로 없다.
def job_id_for_key(idempotency_key: str, version_id: str) -> str | None:
    """재사용 시 version_id가 최초 요청과 일치해야 기존 job_id를 돌려준다."""
    existing_job_id = _job_id_by_idempotency_key.get(idempotency_key)
    if existing_job_id is None:
        return None
    existing_job = _jobs.get(existing_job_id)
    if existing_job is None:
        # M9: 신원 불일치가 아니라 원본 job이 상한을 넘겨 밀려난 것 -- "처음 보는 키"와
        # 동일하게 취급한다.
        del _job_id_by_idempotency_key[idempotency_key]
        return None
    if existing_job.version_id != version_id:
        raise ValueError("idempotencyKey가 이전 요청의 versionId와 일치하지 않습니다")
    return existing_job_id


def create_job(body: CurriculumRequest, idempotency_key: str | None) -> CurriculumJobStatus:
    """QUEUED job 생성. 아직 분석하지 않는다."""
    job = CurriculumJobStatus(
        job_id=str(uuid.uuid4()),
        version_id=body.version_id,
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


def _stub_result(body: CurriculumRequest, pdf_bytes: bytes | None) -> CurriculumResult:
    """고정 결과. 백엔드가 파싱 코드를 짤 수 있도록 3계층 모양을 실제로 준다.

    모듈 2개 × 개념 2개. 개념 하나는 설명을 못 찾은 경우(null)로 둬서
    NULL 허용 컬럼을 백엔드가 실제로 마주보게 한다.

    2026-08-12: `keywords`·`confidence`(섹션)와 `kind`·`evidence`·`siblingNames`
    (개념)를 채운다. 전부 NOT NULL이거나 문제 선정의 재료인데 스텁이 기본값에
    기대고 있어서, 백엔드가 값이 실제로 실린 모양을 한 번도 못 봤다.
    """
    return CurriculumResult.model_validate(
        {
            "version_id": body.version_id,
            "analysis_version": 1,
            "heuristic_version": 1,
            "prompt_version": 1,
            "extraction_status": "EXTRACTED",
            "quality_status": "OK",
            "fallback_used": False,
            "sections": [
                {
                    "module_no": 1,
                    "title": "[stub] 예외 처리",
                    "page_start": 1,
                    "page_end": 12,
                    # 별도 LLM 호출이 아니라 teaches[].canonicalName을 모은 값이다.
                    "keywords": ["try-except", "finally"],
                    "confidence": 1.0,
                    "teaches": [
                        {
                            "canonical_name": "try-except",
                            "normalized_name": "try except",
                            "confidence": 1.0,
                            "canonical_description": "[stub] 예외를 잡아 처리하는 구문",
                            "description_page_start": 3,
                            "description_page_end": 5,
                            "kind": "CODE_EXAMPLE",
                            "evidence": "[stub] try: ... except ValueError as e: ...",
                            # 같은 unit의 다른 개념. 교안이 대안을 가르쳤다는 신호다.
                            "sibling_names": ["finally"],
                        },
                        {
                            # 설명을 못 찾은 개념. 세 필드가 전부 null이다.
                            "canonical_name": "finally",
                            "normalized_name": "finally",
                            "kind": "CAUTION",
                            "sibling_names": ["try except"],
                        },
                    ],
                },
                {
                    "module_no": 2,
                    "title": "[stub] 동시성",
                    "page_start": 13,
                    "page_end": 30,
                    "keywords": ["race condition"],
                    # 이 구간에 걸친 청크가 실패해 개념이 빠졌을 수 있다는 등급.
                    # 확률이 아니라 두 등급뿐이다(1.0 / 0.5).
                    "confidence": 0.5,
                    "teaches": [
                        {
                            "canonical_name": "race condition",
                            "normalized_name": "race condition",
                            "confidence": 0.5,
                            "canonical_description": "[stub] 실행 순서에 따라 결과가 달라지는 상태",
                            "description_page_start": 14,
                            "description_page_end": 14,
                            "kind": "CONCEPT",
                            "evidence": None,
                            "sibling_names": [],
                        }
                    ],
                },
            ],
        }
    )


def _real_result(body: CurriculumRequest, pdf_bytes: bytes, job: CurriculumJobStatus,
                 idempotency_key: str | None, trace_id: str | None) -> CurriculumResult:
    """PDF를 실제로 분석한다.

    **청크가 하나라도 깨지면 `fallback_used`를 세운다.** 결과는 나오지만 그 페이지
    범위의 개념이 빠져 있다는 뜻이라, 강사가 teach 3건을 고르기 전에 알아야 한다.

    원장(`job.ai_usage`)은 검증보다 먼저 채운다 — 결과를 버려도 태운 토큰은 남긴다
    (jobs.py와 같은 순서). 교안은 청크마다 호출이라 행이 여러 개다.
    """
    from app.engines import curriculum as engine

    settings = get_settings()
    built = engine.analyse(
        pdf_bytes,
        model_code=body.provider_model_code or settings.model_code_curriculum,
        course_label=body.course_label,
    )
    # ⚠️ contextId가 curriculum_analysis.analysis_id여야 하는데 **AI는 그 값을 모른다**
    # (요청에 오는 것은 versionId뿐이다). jobId를 넣고 Spring이 교체한다.
    job.ai_usage = to_ai_usage(built.usages, "CURRICULUM_ANALYSIS", job.job_id,
                               feature_code="CURRICULUM_ANALYSIS",
                               idempotency_key=idempotency_key, trace_id=trace_id)
    return CurriculumResult.model_validate({
        "version_id": body.version_id,
        "analysis_version": engine.ANALYSIS_VERSION,
        "prompt_version": None,
        "extraction_status": "EXTRACTED" if built.sections else "EMPTY",
        "quality_status": "OK" if not built.failed_chunks else "PARTIAL",
        "fallback_used": bool(built.failed_chunks),
        "sections": built.sections,
    })


def run_curriculum(job_id: str, body: CurriculumRequest, pdf_bytes: bytes | None, *,
                   idempotency_key: str | None = None, trace_id: str | None = None) -> None:
    """`HEAVY_JOB_CONCURRENCY` 상한 안에서만 `_run_curriculum_locked`를 실제로 돌린다.

    jobs.py의 코드분석 job과 이 상한을 공유한다(app/concurrency.py 주석 참고) --
    교안분석도 job 하나당 ThreadPoolExecutor(max_workers=8)를 쓰는 같은 구조라
    (analyse()가 hints.MAX_PARALLEL을 재사용), 별도 세마포어를 들면 "코드분석
    6개 + 교안분석 6개 동시"로 2026-08-25 CPU 100% 인시던트가 재현된다.
    """
    with HEAVY_JOB_CONCURRENCY:
        _run_curriculum_locked(job_id, body, pdf_bytes,
                               idempotency_key=idempotency_key, trace_id=trace_id)


def _run_curriculum_locked(job_id: str, body: CurriculumRequest, pdf_bytes: bytes | None, *,
                           idempotency_key: str | None = None, trace_id: str | None = None) -> None:
    """백그라운드 워커. QUEUED → RUNNING → SUCCEEDED."""
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    try:
        if get_settings().engine_mode == "real" and pdf_bytes:
            job.result = _real_result(body, pdf_bytes, job, idempotency_key, trace_id)
        else:
            job.result = _stub_result(body, pdf_bytes)
        job.status = "SUCCEEDED"
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)