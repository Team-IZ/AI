# FastAPI 앱, 라우터 모아 붙이는 것 외에 로직 두지 않음

from fastapi import Depends, FastAPI

from app.api import (
    analyses,
    curricula,
    health,
    interview_brief,
    reports,
    sessions,
)
from app.api.deps import require_internal_key
from app.api.errors import register_error_handlers
from app.config import API_V0_PREFIX, get_settings
from app.schemas.common import ErrorResponse

# import 시점에 설정을 강제로 읽어 production 가드를 여기서 터뜨린다.
# 지연 호출(요청 때 첫 호출)로 두면 기동은 성공하고 /api/health도 200이라
# App Runner가 배포를 정상으로 판정한 뒤 업무 요청만 전부 500이 된다.
get_settings()

app = FastAPI(
    title="IZ-GET",
    description="코드 분석 및 소크라틱 문답과 채점, 교안 분석 및 안내를 위한 AI 서비스",
    version="0.1.0",
)

# 에러 응답을 계약 혈태로
register_error_handlers(app)

# 운영 모니터링용인 health는 인증 면제
app.include_router(health.router)

# 🔴 422는 **전부 공용 ErrorResponse다.** 안 적어두면 FastAPI가 자기 기본
# HTTPValidationError({detail:[{loc,msg,type}]})를 스펙에 넣는데, 실제 응답은
# errors.py의 RequestValidationError 핸들러가 {error,message,retryable}로 바꿔 낸다
# -- **스펙만 보고 구현하는 백엔드가 파서를 잘못 짠다**(2026-08-07 발견).
# 라우터 단위로 한 번 걸어 두면 새 엔드포인트가 생겨도 자동으로 따라온다.
_BUSINESS_RESPONSES = {422: {"model": ErrorResponse, "description": "요청 스키마 위반"}}

# 업무 라우터: 인증을 라우터 단위로 한 번 걸면 그 아래 모든 경로 적용
app.include_router(
    analyses.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
    responses=_BUSINESS_RESPONSES,
)

app.include_router(
    sessions.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
    responses=_BUSINESS_RESPONSES,
)

app.include_router(
    reports.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
    responses=_BUSINESS_RESPONSES,
)

app.include_router(
    curricula.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
    responses=_BUSINESS_RESPONSES,
)

app.include_router(
    interview_brief.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
    responses=_BUSINESS_RESPONSES,
)