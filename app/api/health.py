"""GET /api/health — Phase 1의 유일한 엔드포인트.

운영 모드와 파이프라인 로드 상태를 반환한다.
"""
from fastapi import APIRouter

from app.config import get_settings
from app.core import pipeline_runner

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "mode": settings.app_mode,
        "pipeline_loaded": pipeline_runner.is_pipeline_loaded(),
    }
