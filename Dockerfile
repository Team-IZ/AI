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

# D7 (2026-07-31): Worker의 DEFAULT_MODEL_CODE 교체를 이미 떠 있던 컨테이너
# 인스턴스에 반영시키려면 이미지 해시 자체가 바뀌어야 강제 재생성된다(env var는
# 컨테이너 프로세스 시작 시점에 고정, 실측 확인 -- warm 인스턴스가 새 값 없이 옛
# 값(z-ai/glm-5.2)으로 계속 돌아 재현). 주석만으로는 레이어 해시가 안 바뀌어서(1차
# 시도 실패, 확인함) LABEL로 실제 이미지 config를 바꾼다.
LABEL rebuild_reason="D7-force-container-recreate-2026-07-31"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
