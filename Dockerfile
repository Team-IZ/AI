# App Runner 관리형 런타임(source-code 배포)은 시스템 패키지를 못 깔아서 git 바이너리가
# 없다 -- app/engines/analysis/fetch.py·materialize.py가 subprocess로 실제 git CLI(clone/log/
# rev-parse/fetch)를 부르므로 GITHUB_URL·임베디드 .git 분석이 전부 FileNotFoundError로
# 죽는다(실측 확인). 컨테이너 이미지로 옮겨 git을 직접 설치해서 해결한다.
FROM python:3.11-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN groupadd -r app && useradd -r -g app -u 1001 -d /app app \
    && chown -R app:app /app
USER app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
