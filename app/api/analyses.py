"""분석 API (P02) — 명세 §3.

엔드포인트 2개:
- `POST /api/v1/analyses`          비동기 job 접수 → 202 + {job_id, status:"QUEUED"}
- `GET  /api/v1/analyses/{job_id}` 상태·결과 폴링 (§2: 콜백 유실 대비 폴백 경로)

요청 본문이 두 가지 Content-Type을 갖는 이유 (§2/§3.1):
- `GITHUB_URL` → `application/json`
- `ZIP_WITH_GITLOG` → `multipart/form-data` (§3.3: Spring 무저장 중계 스트리밍).
  JSON 본문과 파일을 동시에 받아야 해서, 폼 필드 `payload`에 §3.1 JSON을 문자열로
  담고 `file`에 ZIP을 싣는다. FastAPI는 한 오퍼레이션에서 Body와 Form을 섞을 수
  없으므로 라우터가 Content-Type을 보고 직접 분기하고, Swagger에서 두 형태 모두
  시험할 수 있도록 `openapi_extra`로 requestBody를 명시한다.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings, get_settings
from app.core import analysis_job

router = APIRouter(tags=["analyses"])

_store = analysis_job.JobStore()


class AnalysisSource(BaseModel):
    """§3.1 `source` — B5 결정에 따라 PAT 필드는 없다(공개 레포만)."""

    repo_url: str | None = Field(default=None, description="method=GITHUB_URL일 때 필수. 공개 레포만")
    branch: str | None = Field(default=None, description="생략 시 기본 브랜치")


class AnalysisRequest(BaseModel):
    """§3.1 요청 본문."""

    attempt_id: str | None = Field(default=None, description="Spring 측 MeasurementAttempt 키 (에코용)")
    submission_id: str | None = None
    callback_url: str | None = Field(
        default=None,
        description="B3 완료 통지 수신 창구. **현재는 수용·보관만 하고 전송은 미구현**",
    )
    method: Literal["GITHUB_URL", "ZIP_WITH_GITLOG"]
    source: AnalysisSource = Field(default_factory=AnalysisSource)
    extraction_scope: Literal["TOTAL", "OWN_COMMIT"] = "TOTAL"
    commit_email: str | None = Field(default=None, description="OWN_COMMIT일 때 필수")
    question_budget: int = Field(default=4, ge=1, description="PLAN-01 질문 수 N")
    focus_areas: list[str] = Field(default_factory=list)


class AnalysisAccepted(BaseModel):
    job_id: str
    status: Literal["QUEUED"]


def _error(status_code: int, code: str, message: str, retryable: bool = False) -> HTTPException:
    """§2 공통 에러 형식."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "retryable": retryable}},
    )


def _validate(payload: dict[str, Any]) -> AnalysisRequest:
    try:
        req = AnalysisRequest.model_validate(payload)
    except ValidationError as exc:
        raise _error(422, "INVALID_REQUEST", "요청 스키마가 올바르지 않습니다") from exc

    if req.method == "GITHUB_URL" and not (req.source.repo_url or "").strip():
        raise _error(422, "INVALID_REQUEST", "method=GITHUB_URL에는 source.repo_url이 필요합니다")
    if req.extraction_scope == "OWN_COMMIT" and not (req.commit_email or "").strip():
        raise _error(422, "INVALID_REQUEST", "extraction_scope=OWN_COMMIT에는 commit_email이 필요합니다")
    return req


# Swagger에서 두 Content-Type을 모두 시험할 수 있게 requestBody를 직접 기술한다.
_JSON_PROPERTIES: dict[str, Any] = {
    "attempt_id": {"type": "string", "nullable": True},
    "submission_id": {"type": "string", "nullable": True},
    "callback_url": {"type": "string", "nullable": True},
    "method": {"type": "string", "enum": ["GITHUB_URL", "ZIP_WITH_GITLOG"]},
    "source": {
        "type": "object",
        "properties": {
            "repo_url": {"type": "string"},
            "branch": {"type": "string"},
        },
    },
    "extraction_scope": {"type": "string", "enum": ["TOTAL", "OWN_COMMIT"], "default": "TOTAL"},
    "commit_email": {"type": "string", "nullable": True},
    "question_budget": {"type": "integer", "default": 4},
    "focus_areas": {"type": "array", "items": {"type": "string"}},
}

_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["method"],
                "properties": _JSON_PROPERTIES,
            },
            "example": {
                "attempt_id": "att-1",
                "method": "GITHUB_URL",
                "source": {"repo_url": "https://github.com/owner/repo", "branch": "main"},
                "extraction_scope": "TOTAL",
                "question_budget": 4,
            },
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["payload", "file"],
                "properties": {
                    "payload": {
                        "type": "string",
                        "description": "§3.1 요청 JSON을 문자열로. method는 ZIP_WITH_GITLOG",
                        "example": '{"method":"ZIP_WITH_GITLOG","extraction_scope":"TOTAL","question_budget":4}',
                    },
                    "file": {"type": "string", "format": "binary", "description": "제출 ZIP"},
                },
            }
        },
    },
}


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAccepted,
    summary="코드 분석 요청 (P02)",
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def create_analysis(
    request: Request, background_tasks: BackgroundTasks
) -> AnalysisAccepted:
    """분석 요청 접수. 실제 scan/score는 background task로 넘기고 즉시 202를 반환한다.

    입력: `Request`(raw — Content-Type에 따라 JSON 또는 multipart(`payload` JSON
    문자열 + `file` ZIP)를 담고 있어 모델로 바로 바인딩할 수 없다), `BackgroundTasks`.
    출력: `AnalysisAccepted`(job_id: str, status: Literal["QUEUED"]).
    에러: 422 `INVALID_REQUEST`(스키마 위반·필수 필드 누락·multipart 형식 오류).
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    zip_bytes: bytes | None = None

    if content_type == "multipart/form-data":
        form = await request.form()
        raw_payload = form.get("payload")
        upload = form.get("file")
        if raw_payload is None or upload is None or isinstance(upload, str):
            raise _error(422, "INVALID_REQUEST", "multipart 요청에는 payload와 file이 모두 필요합니다")
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError) as exc:
            raise _error(422, "INVALID_REQUEST", "payload가 올바른 JSON이 아닙니다") from exc
        zip_bytes = await upload.read()
    else:
        try:
            payload = await request.json()
        except (TypeError, ValueError) as exc:
            raise _error(422, "INVALID_REQUEST", "요청 본문이 올바른 JSON이 아닙니다") from exc

    if not isinstance(payload, dict):
        raise _error(422, "INVALID_REQUEST", "요청 본문은 객체여야 합니다")

    req = _validate(payload)

    if req.method == "ZIP_WITH_GITLOG" and not zip_bytes:
        raise _error(
            422,
            "INVALID_REQUEST",
            "method=ZIP_WITH_GITLOG는 multipart/form-data로 ZIP을 함께 보내야 합니다",
        )

    settings: Settings = get_settings()
    root = settings.workspace_dir
    root.mkdir(parents=True, exist_ok=True)
    # B8: TTL 지난 이전 작업공간 청소 (별도 스케줄러 없이 요청 시점에 수행)
    analysis_job.sweep_workspaces(root, settings.workspace_ttl_sec)

    job = _store.create(
        attempt_id=req.attempt_id,
        submission_id=req.submission_id,
        callback_url=req.callback_url,  # B3: 보관만, 전송은 미구현
    )
    job.workspace = root / job.job_id
    job.workspace.mkdir(parents=True, exist_ok=True)

    background_tasks.add_task(
        analysis_job.run_analysis,
        job,
        method=req.method,
        repo_url=req.source.repo_url,
        branch=req.source.branch,
        zip_bytes=zip_bytes,
        extraction_scope=req.extraction_scope,
        commit_email=req.commit_email,
        question_budget=req.question_budget,
    )
    return AnalysisAccepted(job_id=job.job_id, status="QUEUED")


@router.get(
    "/analyses/{job_id}",
    summary="분석 상태·결과 조회 (§3.2)",
)
async def get_analysis(job_id: str) -> dict[str, Any]:
    """job 상태 폴링. 콜백(B3)이 아직 미구현이라 현재는 이 폴링만이 결과 취득 경로다.

    입력: `job_id: str`(경로 파라미터).
    출력: `AnalysisJob.to_response()` 결과 — §3.2 형태의 dict(job_id/attempt_id/
    submission_id/status/failure_reason/result/ai_usage, 실패 시 error도 포함).
    READY/PARTIAL이면 `result`에 다음도 포함된다(S1/M3·N1 — 코드 원문 무저장,
    스냅샷 메타만 제공):
    - `snapshot_id: str` — job_id와 별개로 발급한 UUID. Spring `code_snapshot` 키 대응.
    - `snapshot_meta: dict` — `{content_hash(sha256 hex 64자), file_count, byte_count}`.
      실제 분석에 쓰인(스코프 필터 적용 후 물리화된) 파일 집합 기준.
    에러: 404 `JOB_NOT_FOUND`(모르는 job_id — 서버 재시작으로 유실됐을 수도 있음, §1).
    """
    job = _store.get(job_id)
    if job is None:
        raise _error(404, "JOB_NOT_FOUND", f"분석 job을 찾을 수 없습니다: {job_id}")
    return job.to_response()
