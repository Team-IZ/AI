"""FastAPI 앱 팩토리 (PLAN §1.5).

- APP_MODE에 따라 storage adapter를 주입한다:
  - integrated: NullStore (저장 없음 — DB 단일 소유자는 Spring)
  - standalone: SupabaseStore (Phase 5에서 구현; 그 전까지는 NullStore로 폴백)
- standalone 모드에서는 trainee/ 목업 페이지를 정적 서빙으로 마운트한다.
  (Phase 1은 마운트 코드만 — 페이지의 데이터 호출부를 FastAPI API로 교체하는 작업은
   Phase 2~4에서 페이지별로 순차 진행한다: submission→Phase 2, session→Phase 3, result→Phase 4.)
"""
import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import analyses, health
from app.api.deps import require_internal_key
from app.config import API_V1_PREFIX, Settings, get_settings
from app.core import pipeline_runner
from app.storage.base import ResultStore
from app.storage.null_store import NullStore

logger = logging.getLogger(__name__)

_AI_ROOT = Path(__file__).resolve().parent.parent
TRAINEE_DIR = _AI_ROOT / "trainee"
SHARED_DIR = _AI_ROOT / "shared"


def _build_store(settings: Settings) -> ResultStore:
    """APP_MODE에 따른 ResultStore adapter 선택 (PLAN §1.5).

    현재는 두 모드 모두 NullStore다 — standalone의 SupabaseStore는 Phase 5 범위라
    아직 없다. 그 폴백 사실만 경고로 남긴다.
    """
    if settings.app_mode == "standalone":
        logger.warning(
            "standalone mode: supabase_store is not implemented yet (Phase 5); "
            "falling back to NullStore"
        )
    return NullStore()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Big Project AI Service",
        description="코드 분석(P02)·소크라틱 문답(P03)·후채점 AI 서비스",
        version="0.1.0",
    )

    # CORS는 standalone 모드에서만 붙인다.
    # 명세 §1: integrated 모드에서 FastAPI의 호출자는 Spring뿐이고 React는 FastAPI를
    # 직접 호출하지 않는다 — 즉 브라우저 preflight가 발생할 경로가 없으므로,
    # 열어두면 실익 없이 노출 면적만 넓힌다.
    # standalone에서도 목업 프론트를 FastAPI가 같은 오리진으로 서빙하므로 대부분
    # 불필요하지만, 페이지를 별도 dev 서버/file://로 여는 개발 편의를 위해 남긴다.
    # 인증은 쿠키가 아니라 X-Internal-Key 헤더(B1)이므로 allow_credentials는 끈다.
    if settings.app_mode == "standalone":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # storage adapter 주입 — 라우터에서는 request.app.state.store로 접근
    app.state.store = _build_store(settings)

    # 파이프라인 import 경로 셋업 (앱 생성 시 1회)
    pipeline_runner.setup_pipeline_paths()

    # /api/health는 운영 모니터링용이라 B1 인증 면제 — require_internal_key를
    # 붙이지 않는다. Phase 2~4의 업무 라우터는 config.API_V1_PREFIX 아래에
    # dependencies=[Depends(require_internal_key)]와 함께 붙인다.
    app.include_router(health.router)

    # 업무 라우터: B1 공유 API 키 인증을 라우터 단위로 일괄 적용한다.
    app.include_router(
        analyses.router,
        prefix=API_V1_PREFIX,
        dependencies=[Depends(require_internal_key)],
    )

    # standalone: 목업 프론트 정적 서빙 (PLAN §1.5 모드 A).
    # 페이지 내부의 Pyodide·프록시 호출은 Phase 2~4에서 페이지별로 FastAPI API 호출로 교체·제거.
    if settings.app_mode == "standalone":
        if SHARED_DIR.is_dir():
            app.mount("/shared", StaticFiles(directory=str(SHARED_DIR)), name="shared")
        if TRAINEE_DIR.is_dir():
            app.mount(
                "/",
                StaticFiles(directory=str(TRAINEE_DIR), html=True),
                name="trainee",
            )

    return app


app = create_app()
