""" 교안 분석 job의 인메모리 저장소 + 스텁 (jobs.py·reports.py와 형제).

인메모리 dict — 재시작 시 유실. 스케일 필요 시 Redis/DB로 이전.
스텁이라 고정 결과를 돌려준다. 실제 추출(PDF 파싱 + LLM 정규화)은 엔진 이식 때 붙인다.

교안 분석은 LLM을 무겁게 쓴다(교안 1개에 1~2분 이상). 수업 중이 아니라
LMS 업로드 시점에 도는 것이 전제다 — 그래서 멱등키로 중복 실행을 막는다.
"""

import uuid
from datetime import datetime, timezone

from app.schemas.curriculum import (
    CurriculumJobStatus,
    CurriculumRequest,
    CurriculumResult,
)

# job_id -> 교안 분석 job 상태·결과
_jobs: dict[str, CurriculumJobStatus] = {}

# 멱등키 -> job_id. 같은 키 재요청 시 새 job 안 만들고 처음 id 반환
_job_id_by_idempotency_key: dict[str, str] = {}


def get_job(job_id: str) -> CurriculumJobStatus | None:
    return _jobs.get(job_id)


def job_id_for_key(idempotency_key: str) -> str | None:
    return _job_id_by_idempotency_key.get(idempotency_key)


def create_job(body: CurriculumRequest, idempotency_key: str | None) -> CurriculumJobStatus:
    """QUEUED job 생성. 아직 분석하지 않는다."""
    job = CurriculumJobStatus(
        job_id=str(uuid.uuid4()),
        version_id=body.version_id,
        status="QUEUED",
    )
    _jobs[job.job_id] = job
    if idempotency_key:
        _job_id_by_idempotency_key[idempotency_key] = job.job_id
    return job


def _stub_result(body: CurriculumRequest, pdf_bytes: bytes | None) -> CurriculumResult:
    """고정 결과. 백엔드가 파싱 코드를 짤 수 있도록 3계층 모양을 실제로 준다.

    모듈 2개 × 개념 2개. 개념 하나는 설명을 못 찾은 경우(null)로 둬서
    NULL 허용 컬럼을 백엔드가 실제로 마주보게 한다.
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
                    "teaches": [
                        {
                            "canonical_name": "try-except",
                            "normalized_name": "try except",
                            "canonical_description": "[stub] 예외를 잡아 처리하는 구문",
                            "description_page_start": 3,
                            "description_page_end": 5,
                        },
                        {
                            # 설명을 못 찾은 개념. 세 필드가 전부 null이다.
                            "canonical_name": "finally",
                            "normalized_name": "finally",
                        },
                    ],
                },
                {
                    "module_no": 2,
                    "title": "[stub] 동시성",
                    "page_start": 13,
                    "page_end": 30,
                    "teaches": [
                        {
                            "canonical_name": "race condition",
                            "normalized_name": "race condition",
                            "canonical_description": "[stub] 실행 순서에 따라 결과가 달라지는 상태",
                            "description_page_start": 14,
                            "description_page_end": 14,
                        }
                    ],
                },
            ],
        }
    )


def run_curriculum(job_id: str, body: CurriculumRequest, pdf_bytes: bytes | None) -> None:
    """백그라운드 워커. QUEUED → RUNNING → SUCCEEDED (스텁은 항상 성공)."""
    job = _jobs[job_id]
    job.status = "RUNNING"
    job.started_at = datetime.now(timezone.utc)

    try:
        job.result = _stub_result(body, pdf_bytes)
        job.status = "SUCCEEDED"
    except Exception as exc:
        # 엔진 터지거나 계약 어기면 FAILED로. 예외 삼키지 말고 사유 기록
        job.status = "FAILED"
        job.failure_reason = str(exc)
    finally:
        job.completed_at = datetime.now(timezone.utc)