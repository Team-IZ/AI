# 운영 모니터링용, 백엔드 통신에서 인증 면제 대상

from fastapi import APIRouter

router = APIRouter(tags=["health"])

# D-health-async(2026-08-26): sync def였던 걸 async def로 바꾼다.
#   WHY: sync def는 Starlette이 AnyIO 스레드풀(기본 40)에서 돌린다. run_analysis도
#        sync def라 같은 풀을 쓰는데, 세마포어 대기 중인 job이 스레드를 붙들고 있으면
#        대기 job이 풀 크기를 넘는 순간 헬스체크도 같이 막힌다 -- 2026-08-25 인시던트의
#        "헬스체크 15초간 0바이트 무응답"을 설명하는 유력한 메커니즘(ECS Fargate 마이그
#        레이션 계획, §5.5a). App Runner는 헬스체크 실패해도 인스턴스를 유지했지만, ECS는
#        unhealthy 태스크를 죽이고 재시작한다 -- 재시작이 진행 중 job을 죽이고, 그게 다시
#        부하를 만들어 재시작 루프로 이어질 수 있어 ECS에서는 훨씬 위험하다.
#   COST: 없음 -- 응답 로직 자체가 없어 await할 것도 없다.
#   EXIT: 해당 없음(원복할 이유가 없는 순수 개선).
@router.get("/api/health", summary="서비스 상태 확인")
async def health() -> dict[str, str]:
    return {"status": "ok"}