# FastAPI 앱, 라우터 모아 붙이는 것 외에 로직 두지 않음

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analyses, curricula, health, reports, sessions
from app.api.deps import require_internal_key
from app.api.errors import register_error_handlers
from app.config import API_V0_PREFIX

app = FastAPI(
    title="IZ-GET",
    description="코드 분석 및 소크라틱 문답과 채점, 교안 분석 및 안내를 위한 AI 서비스",
    version="0.1.0",
)

# D4 (2026-07-31): CORS -- 팀 종합 테스트 배포본(GitHub Pages 정적 페이지)이 이 서비스를
# 브라우저에서 직접 호출하려면 필요하다(그 전까지는 CORS 설정 자체가 아예 없었음 --
# Spring만 호출자였으므로 서버 간 호출엔 CORS가 관여하지 않았다).
#   WHY: allow_origins를 "*"로 열지 않고 GitHub Pages 오리진 + 로컬 개발 서버로 좁힌다 --
#     이 서비스는 X-Internal-Key로 인증하는 내부 서비스라, 임의 오리진에 노출하면
#     그 헤더를 아는 누구나(브라우저 CORS 우회 없이) 호출을 시도할 수 있게 된다.
#   COST: 팀 테스트 페이지를 다른 도메인으로 옮기면 이 목록도 같이 갱신해야 한다.
#   EXIT: Spring을 통한 프록시 호출로 바뀌면(원래 아키텍처) 이 미들웨어 자체를 제거해도 된다 --
#     서버 간 호출은 브라우저 CORS 정책의 대상이 아니다.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://team-iz\.github\.io|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Internal-Key", "Idempotency-Key", "X-Trace-Id"],
    allow_credentials=False,  # 쿠키 안 씀 -- 헤더 기반 인증뿐이라 자격증명 공유 불필요
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