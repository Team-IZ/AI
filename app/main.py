# FastAPI 앱, 라우터 모아 붙이는 것 외에 로직 두지 않음

from fastapi import Depends, FastAPI

from app.api import analyses, health, reports, sessions
from app.api.deps import require_internal_key
from app.api.errors import register_error_handlers
from app.config import API_V0_PREFIX

app = FastAPI(
    title="IZ-GET",
    description="코드 분석 및 소크라틱 문답과 채점, 교안 분석 및 안내를 위한 AI 서비스",
    version="0.1.0",
)

# 에러 응답을 계약 혈태로
register_error_handlers(app)

# 운영 모니터링용인 health는 인증 면제
app.include_router(health.router)

# 업무 라우터: 인증을 라우터 단위로 한 번 걸면 그 아래 모든 경로 적용
app.include_router(
    analyses.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
)

app.include_router(
    sessions.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
)

app.include_router(
    reports.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
)