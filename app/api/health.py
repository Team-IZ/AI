# 운영 모니터링용, 백엔드 통신에서 인증 면제 대상

from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/api/health", summary="서비스 상태 확인")
def health() -> dict[str, str]:
    return {"status": "ok"}