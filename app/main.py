# FastAPI 앱, 라우터 모아 붙이는 것 외에 로직 두지 않음

from fastapi import Depends, FastAPI

from app.api import analyses, curricula, health, interview_brief, reports, sessions
from app.api.deps import require_internal_key
from app.api.errors import register_error_handlers
from app.config import API_INTERNAL_V1_PREFIX, API_V0_PREFIX, get_settings

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

app.include_router(
    curricula.router,
    prefix=API_V0_PREFIX,
    dependencies=[Depends(require_internal_key)],
)

# /internal/v1: 면담 브리프 전용. 다른 4개와 버전 축이 분리된 별도 계약이라
# 접두사가 다르지만, 호출자는 여전히 백엔드 하나뿐이라 인증은 그대로 건다
# (명세 §4 헤더 목록에 X-Internal-Key가 명시되진 않았지만, 뺄 근거도 없다).
app.include_router(
    interview_brief.router,
    prefix=API_INTERNAL_V1_PREFIX,
    dependencies=[Depends(require_internal_key)],
)