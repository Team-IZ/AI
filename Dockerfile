# D1 (2026-07-31): Cloudflare Container 이미지 -- 이 FastAPI 서비스를 GitHub Pages의
# 다른 3개 파이프라인과 같은 자리(팀 테스트용 상시 URL)에 놓기 위한 첫 실제 배포.
#   WHY: Cloudflare Workers(V8/JS 런타임)는 subprocess(git clone, materialize.py)를
#     못 돌린다 -- Containers는 진짜 Linux 컨테이너라 가능.
#   COST(2026-07-31 갱신): 애초에 이 이미지가 필요로 하는 게 requirements.txt뿐이다 --
#     Tier 2(재랭킹)가 crewai 대신 shared.llm.chat()을 직접 쓰도록 바뀌면서
#     (app/engines/codemap/crew.py D1), requirements-codemap.txt(~1.9GB, crewai/
#     chromadb/pdfplumber 등)는 이제 이 서비스 어디에서도 필요하지 않다.
#   EXIT: requirements-codemap.txt가 다시 필요해지는 상황(crewai 실제 재도입)이
#     생기면, 그 파일을 다시 이 이미지에 설치하고 wrangler.toml의 instance_type을
#     올리면 된다 -- git 이력에 그 시절 crew.py 구현이 남아 있다.
FROM python:3.12-slim

# git: app/engines/codemap/materialize.py의 GITHUB_URL 경로가 subprocess로 clone한다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 레이어를 코드보다 먼저 복사 -- requirements가 안 바뀌면 이 레이어가 캐시된다.
# requirements-codemap.txt(crewai)는 의도적으로 제외 -- 위 COST 참고.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY openapi.json ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
