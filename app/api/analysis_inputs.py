""" 분석 입력 확정 API(analysis-inputs 분리, 백엔드 제안) -- POST /analysis-inputs 하나.

검증+fetch만 한다. 분석은 여전히 POST /analyses가 받는다(M3에서 이 엔드포인트가 낸
analysisInputId를 받아 재fetch하도록 확장 예정 -- app/engines/analysis/fetch.py의
`refetch_pinned()`가 그 재fetch를 담당한다).
"""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.api.errors import AnalysisInputError, format_validation_message
from app.engines.analysis import fetch as fetch_engine
from app.schemas.analysis import (
    AnalysisInputFailure,
    AnalysisInputRequest,
    AnalysisInputResponse,
    GitCommit,
    HeadCommit,
)

router = APIRouter(tags=["analysis-inputs"])


def _validate(payload: object) -> AnalysisInputRequest:
    """dict를 스키마로 검증. `/analyses`의 관례(자동 바인딩 대신 직접 호출)와 같은

    모양이되, 이 엔드포인트는 실패 응답 자체가 다른 계약(`AnalysisInputError`)이라
    별도로 둔다.
    """
    if not isinstance(payload, dict):
        raise AnalysisInputError("INVALID_REPOSITORY_URL", "요청 본문은 객체여야 합니다")
    try:
        return AnalysisInputRequest.model_validate(payload)
    except ValidationError as exc:
        request_id = payload.get("requestId")
        raise AnalysisInputError(
            "INVALID_REPOSITORY_URL",
            format_validation_message(exc.errors()),
            request_id if isinstance(request_id, str) else None,
        ) from exc


def _spec_from(body: AnalysisInputRequest) -> dict:
    return {
        "method": body.method,
        "repository_url": body.repository_url,
        "requested_branch": body.requested_branch,
        "download_url": body.download_url,
        "storage_uri": body.storage_uri,
        "git_history": [c.model_dump() for c in body.git_history] if body.git_history else None,
    }


def _do_fetch(body: AnalysisInputRequest) -> fetch_engine.FetchedInput:
    """블로킹 git/파일 작업 전체 -- `asyncio.to_thread`로 이벤트 루프 밖에서 돈다.

    `with` 블록을 나가면 임시 디렉터리가 지워진다(D2/§3.3 유지) -- 필요한 값은 전부
    `FetchedInput`(dataclass)에 이미 담겨 있어 `root` 자체를 더 쓸 일이 없다.
    """
    spec = _spec_from(body)
    with fetch_engine.fetch(spec) as result:
        return result


def _source_of(body: AnalysisInputRequest) -> str:
    return body.repository_url or body.download_url or body.storage_uri or ""


@router.post(
    "/analysis-inputs",
    response_model=AnalysisInputResponse,
    summary="분석 입력 확정 -- 검증+fetch (백엔드 제안 신설 API, §제안 API ①)",
    responses={422: {"model": AnalysisInputFailure, "description": "검증/fetch 실패"}},
)
async def create_analysis_input(request: Request) -> AnalysisInputResponse:
    """저장소를 검증하고 fetch해 분석 입력을 확정한다. 동기 200 -- 202+폴링이 아니다.

    LLM 호출이 없어(fetch만) 지연이 짧고, 폴링을 도입하면 App Runner 멀티인스턴스에서
    또 깨지는 인메모리 job store가 하나 더 생긴다(D2가 피하려는 것과 같은 클래스의 문제).

    🔴 `async def`이지만 `sessions.py`의 "블로킹 호출은 `def`로" 원칙과 어긋나지 않는다 --
    실제 블로킹 작업(`_do_fetch`, git subprocess)은 `asyncio.to_thread`로 스레드에
    돌리고, 이 함수 자체는 그 결과를 기다리는 것 말고 하는 일이 없다. `async def`인
    이유는 오직 하나: 이 엔드포인트만 실패 응답 모양이 달라서(`AnalysisInputError`
    {failureCode,message,requestId} vs 공용 {error,message,retryable}) 자동 바인딩
    대신 직접 파싱해야 하고, 직접 파싱(`request.json()`)이 코루틴이라서다.
    """
    payload = await request.json()
    body = _validate(payload)

    try:
        result = await asyncio.to_thread(_do_fetch, body)
    except fetch_engine.FetchError as exc:
        raise AnalysisInputError(exc.failure_code, exc.message, body.request_id) from exc

    analysis_input_id = fetch_engine.derive_analysis_input_id(
        org_id=body.org_id,
        method=body.method,
        source=_source_of(body),
        pin=(result.head_commit or {}).get("sha") or result.input_hash,
    )

    return AnalysisInputResponse(
        analysis_input_id=analysis_input_id,
        method=result.method,
        resolved_branch=result.resolved_branch,
        head_commit=HeadCommit(**result.head_commit) if result.head_commit else None,
        git_history=[GitCommit(**c) for c in result.git_history],
        git_history_source=result.git_history_source,
        history_truncated=result.history_truncated,
        file_count=result.file_count,
        byte_count=result.byte_count,
        input_hash=result.input_hash,
        captured_at=datetime.now(timezone.utc),
    )
