""" 분석 API (P02) - POST /api/v0/analyses """
import json
from typing import Any

# Request — 원시 요청 객체. Content-Type·본문·폼에 직접 접근할 수 있다.
#           스키마 자동 바인딩을 포기하는 대신 분기를 제어한다.
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
# ValidationError — pydantic이 검증 실패 시 던지는 예외.
#                   자동 바인딩이 아니라 직접 검증.
from pydantic import ValidationError

from app import jobs
from app.api.errors import ApiError, format_validation_message
from app.api.multipart_docs import multipart_body
from app.schemas.analysis import (
    AnalysisAccepted,
    AnalysisJobStatus,
    AnalysisRequest,
)
from app.schemas.common import ErrorResponse

from app.engines import get_analysis_engine
from app.engines.analysis import rules
from app.engines.base import AnalysisEngine

router = APIRouter(tags=["analyses"])

def _invalid(message: str) -> ApiError:
    return ApiError(status_code=422, error="INVALID_REQUEST", message=message)

def _too_large(message: str) -> ApiError:
    # error 값이 analysis_job.failure_code 15종의 FILE_TOO_LARGE와 같은 이름이다 --
    # 백엔드가 AiCallException.failureCode를 그대로 parse해서 저장하므로,
    # 다른 이름을 쓰면 저쪽에서 MODEL_ERROR로 뭉개진다.
    return ApiError(status_code=413, error="FILE_TOO_LARGE", message=message, retryable=False)

async def _read_upload(upload: Any) -> bytes:
    """업로드를 청크로 받으며 상한을 넘는 순간 끊는다.

    `await upload.read()` 한 방이면 짧지만, 그건 **상한 검사에 도달하기 전에**
    전체를 RAM에 올린다(`fetch.py:_download`가 같은 이유로 이미 스트리밍이다).
    Starlette이 multipart를 임시파일로 흘려주므로 여기까지는 디스크에 있다.

    ponytail: b"".join이 순간적으로 2배를 쓴다(상한 100MiB니 최대 200MiB).
    진짜 해법은 bytes를 아예 안 만들고 파일 객체를 zipfile에 넘기는 것인데,
    engine이 스냅샷 해시를 zip_bytes 자체로 계산해서 그 경로가 더 넓다.
    """
    chunks: list[bytes] = []
    received = 0
    while chunk := await upload.read(1024 * 1024):
        received += len(chunk)
        if received > rules.MAX_UPLOAD_BYTES:
            raise _too_large(
                f"제출 ZIP이 업로드 상한({rules.MAX_UPLOAD_BYTES} bytes)을 넘습니다"
            )
        chunks.append(chunk)
    return b"".join(chunks)

async def _read_request(request: Request) -> tuple[dict[str,Any], bytes | None]:
    """Content-Type을 보고 본문을 읽는다. (요청 dict, ZIP 바이트) 를 돌려준다.

    multipart는 폼 필드 payload(JSON 문자열) + file(ZIP)로 온다.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()

    # 본문을 한 바이트도 읽기 전에 끊는다. Content-Length는 클라이언트가 주는 값이라
    # 이것만 믿지는 않고(_read_upload가 실측으로 다시 검사한다), 정직한 클라이언트가
    # 거대한 파일을 다 올린 뒤에야 거절당하는 낭비를 없애는 용도다.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > rules.MAX_UPLOAD_BYTES * 2:
        raise _too_large(f"요청 본문이 업로드 상한({rules.MAX_UPLOAD_BYTES} bytes)을 넘습니다")

    if content_type == "multipart/form-data":
        form = await request.form()
        raw_payload = form.get("payload")
        upload = form.get("file")
        # upload가 str이면 파일이 아니라 문자열 필드로 온 것
        if raw_payload is None or upload is None or isinstance(upload, str):
            raise _invalid("multipart 요청에는 payload와 file이 모두 필요합니다")
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError) as exc:
            raise _invalid("payload가 올바른 JSON이 아닙니다") from exc
        return payload, await _read_upload(upload)
    
    try:
        payload = await request.json()
    except (TypeError, ValueError) as exc:
        raise _invalid("요청 본문이 올바른 JSON이 아닙니다") from exc
    return payload, None

def _validate(payload: Any) -> AnalysisRequest:
    """ dict를 스키마로 검증. 자동 바인딩을 안 쓰므로 직접 부르기 """
    if not isinstance(payload, dict):
        raise _invalid("요청 본문은 객체여야 합니다")
    try:
        return AnalysisRequest.model_validate(payload)
    except ValueError as exc:
        raise _invalid(format_validation_message(exc.errors())) from exc

# 자동 바인딩을 포기해 AnalysisRequest가 components에 안 잡힌다 — 스펙에 직접 실어야
# 백엔드가 요청 필드를 볼 수 있다. 방식·근거는 multipart_docs 모듈 주석 참고.
_REQUEST_BODY = multipart_body(
    AnalysisRequest,
    file_description="제출 ZIP (multipart일 때만. GITHUB_URL은 application/json으로 보낸다)",
    payload_example='{"method":"ZIP_WITH_GITLOG","problemScope":"INDIVIDUAL_OWN_COMMIT"}',
    examples={
        "team_github": {
            "summary": "① 팀 모드 · GitHub URL — teaches 필수",
            "description":
                "오퍼레이터가 고른 교안 개념(`teaches`)마다 문제 1개. 팀당 1회만 분석하고 "
                "팀원 세션이 같은 결과를 공유한다. `teaches`를 빼면 422다.",
            "value": {
                "method": "GITHUB_URL",
                "problemScope": "TEAM_SHARED_PROBLEM",
                "source": {"repoUrl": "https://github.com/owner/repo", "branch": "main"},
                "extractionScope": "TOTAL",
                "questionBudget": 3,
                "curriculumVersionId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "teaches": [
                    {"id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
                     "label": "제네릭 타입 경계", "unitId": "5c1f0a2e-1111-4b2d-9b5d-ab8dfbbd4bed",
                     "sourcePages": [12, 13]},
                    {"id": "2c8e5abc-aaaa-4b2d-9b5d-ab8dfbbd4bed",
                     "label": "예외 처리 전략", "unitId": "5c1f0a2e-1111-4b2d-9b5d-ab8dfbbd4bed",
                     "sourcePages": [21]},
                    {"id": "3d7f4def-cccc-4b2d-9b5d-ab8dfbbd4bed",
                     "label": "의존성 주입", "unitId": "6d2e1b3f-2222-4b2d-9b5d-ab8dfbbd4bed",
                     "sourcePages": [30, 31]},
                ],
                "requirements": [
                    {"requirementId": "9f1e2d3c-4444-4b2d-9b5d-ab8dfbbd4bed",
                     "text": "회원 가입 시 이메일 중복을 검사한다"},
                ],
            },
        },
        "individual_github": {
            "summary": "② 개인 모드 · GitHub URL — teaches 없이 문제 3개",
            "description":
                "교안 없이 제출 코드 자체에서 문제를 뽑는다. `teaches`를 **보내면 422**다 "
                "(DB가 `project_verification_concept_id`를 NULL로 강제한다). "
                "문제 수는 `teaches`와 무관하게 `questionBudget`(기본 3)이다.",
            "value": {
                "method": "GITHUB_URL",
                "problemScope": "INDIVIDUAL_OWN_COMMIT",
                "source": {"repoUrl": "https://github.com/owner/repo", "branch": "main"},
                "extractionScope": "OWN_COMMIT",
                "commitEmail": "trainee@example.com",
                "questionBudget": 3,
            },
        },
        "team_zip": {
            "summary": "③ 팀 모드 · ZIP — multipart로 보낸다",
            "description":
                "`Content-Type: multipart/form-data`로 `payload`(이 JSON을 문자열로) + "
                "`file`(ZIP) 두 파트를 보낸다. `payload` 파트에 "
                "`Content-Type: application/json`을 반드시 붙인다.",
            "value": {
                "method": "ZIP_WITH_GITLOG",
                "problemScope": "TEAM_SHARED_PROBLEM",
                "extractionScope": "TOTAL",
                "questionBudget": 3,
                "teaches": [
                    {"id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
                     "label": "제네릭 타입 경계", "unitId": "5c1f0a2e-1111-4b2d-9b5d-ab8dfbbd4bed",
                     "sourcePages": [12, 13]},
                ],
            },
        },
    },
)

@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisAccepted,
    summary="코드 분석 요청 (P02)",
    responses={422: {"model": ErrorResponse, "description": "요청 스키마 위반"}},
    openapi_extra={"requestBody": _REQUEST_BODY},
)
async def create_analysis(
    request: Request,
    # BackgroundTasks: FastAPI가 자동 주입. 여기 add_task 한 작업은
    # 응답을 보낸 "뒤"에 실행된다 → 202를 먼저 돌려주고 분석은 나중.
    background_tasks: BackgroundTasks,
    engine: AnalysisEngine = Depends(get_analysis_engine),
    
    # 파라미터명 -> 헤더명 자동 변환(언더스코어 -> 하이픈, 첫글자 대문자로)
    idempotency_key: str | None = Header(
        default=None, description="submissionId:attemptNo. 중복 요청 판별"
    ),
    x_trace_id: str | None = Header(default=None, description="분산 추적 ID"),
) -> AnalysisAccepted:
    """ 분석 요청 접수하고 즉시 202 반환. 실제 분석은 백그라운드
    
    x_trace_id는 아직 사용 X. 헤더 자리 게약 고정하고 Swagger 노출 위해 지금 받아둠
    - 로깅에 붙이는 것은 나중
    """
    payload, zip_bytes = await _read_request(request)
    body = _validate(payload)

    # method와 실제 전송 형태 어긋나면 여기서 잡음
    # 스키마만으로 표현할 수 없음 - Content-Type은 본문 밖의 정보
    if body.method == "ZIP_WITH_GITLOG" and not zip_bytes:
        raise _invalid("method=ZIP_WITH_GITLOG는 multipart/form-data로 ZIP을 함께 보내야 합니다")

    # 🔴 개인 모드는 요청 계약만 확정됐고 선정 엔진이 아직 없다. topics.select()가
    # teach 중심이라(teaches_block 프롬프트 + teach_id 검증) teaches 없이는 문제를
    # 0개 만든다. 조용한 0개를 내보내면 백엔드가 "분석은 성공했는데 문제가 없네"로
    # 읽으므로, 미구현이라는 사실 그대로 끊는다.
    if body.problem_scope == "INDIVIDUAL_OWN_COMMIT":
        raise ApiError(
            status_code=501, error="NOT_IMPLEMENTED", retryable=False,
            message="problemScope=INDIVIDUAL_OWN_COMMIT 선정 엔진은 아직 구현되지 "
                    "않았습니다. 요청 형식은 확정이니 이 스펙대로 준비하시면 됩니다",
        )
        
    if idempotency_key:
        # 같은 요청 다시 온 경우 새로 만들지 않고 처음 것 돌려주기.
        # D-fix (redteam audit H12): job_id_for_key가 이제 submission_id/attempt_id
        # 신원 불일치·부재를 ValueError로 알린다 -- 409로 옮긴다.
        try:
            existing = jobs.job_id_for_key(idempotency_key, body.submission_id, body.attempt_id)
        except ValueError as exc:
            raise ApiError(status_code=409, error="IDEMPOTENCY_CONFLICT", message=str(exc)) from exc
        if existing:
            return AnalysisAccepted(job_id=existing, status="QUEUED")

    job = jobs.create_job(body, idempotency_key)
    
    # 분석 백그라운드로 넘김. 이 줄은 즉시 반환, run_analysis는
    # 응답 전송 후 실행되어 QUEUED->RUNNING->SUCCEDED로 전이
    # 헤더 2개는 ai_usage 원장에 그대로 실린다 — Spring이 과금·추적을 이 값으로 잇는다.
    background_tasks.add_task(jobs.run_analysis, job.job_id, body, engine, zip_bytes,
                              idempotency_key=idempotency_key, trace_id=x_trace_id)

    # 202는 "접수했다"는 뜻이다. 실제 job이 이미 끝났는지는 별개이고,
    # 호출자는 GET으로 상태를 확인한다.
    return AnalysisAccepted(job_id=job.job_id, status="QUEUED")

@router.get(
    "/analyses/{job_id}",
    response_model=AnalysisJobStatus,
    summary="분석 상태·결과 조회 (P02)",
    responses={404: {"model": ErrorResponse, "description": "모르는 job_id"}},
)
async def get_analysis(job_id: str) -> AnalysisJobStatus:
    """분석 job의 현재 상태와 결과를 돌려준다.

    경로의 {job_id}가 함수 파라미터 job_id로 자동 연결된다. 이름이 같아야 한다.

    job 저장소가 인메모리라 프로세스가 재시작되면 404가 난다.
    그때는 Spring이 분석을 다시 요청하면 된다(FastAPI는 상태의 소유자가 아니다).
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise ApiError(
            status_code=404,
            error="JOB_NOT_FOUND",
            message=f"분석 job을 찾을 수 없습니다: {job_id}",
            # 재시도해도 없는 건 없다. Spring은 재분석을 요청해야 한다.
            retryable=False,
        )
    return job