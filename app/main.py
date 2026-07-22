# FastAPI 앱, 라우터 모아 붙이는 것 외에 로직 두지 않음

from fastapi import FastAPI

from app.api import health

app = FastAPI(
    title="IZ-GET",
    description="코드 분석 및 소크라틱 문답과 채점, 교안 분석 및 안내를 위한 AI 서비스",
    version="0.1.0",
)

app.include_router(health.router)