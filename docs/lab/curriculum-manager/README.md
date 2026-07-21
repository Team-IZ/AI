# 교안 관리 (Curriculum Manager) — PDF Analysis

Team-IZ Frontend의 `manager/curriculum.html` 와이어프레임 UI를 따라 만든, 실제로 동작하는
교안(PDF) 분석 도구입니다. `popixoxipop-collab/Code_reviewer_with_feedback`의 Pipeline Lab
P01(교안분석) 파이프라인을 코드 수정 없이 그대로 재사용하며, 이 브랜치는 그 저장소의
`docs/lab/curriculum-manager/`와 그 의존 파일들을 그대로 옮겨온 스냅샷입니다.

## 구성

- `index.html` — 목록/등록/교안 구성/교안 연결 4탭. 교안 등록 탭에서 PDF를 올리면
  실제 LLM 파이프라인(청크 분석 → refine → 그래프 생성)이 실행되고, "교안 구성" 탭에
  섹션별 [출처 페이지 범위 | 키워드] 표로 결과가 뜹니다.
- `dashboard.html` / `report.html` / `projects.html` — Team-IZ 와이어프레임을 그대로 포팅한
  **정적 참고 화면**(실제 데이터 연동 없음, nav가 죽은 링크로 남지 않도록 붙여둔 것).
- `labapp-shim.js` — `p01-runner.js`(원본 파이프라인, 무수정)를 이 페이지에서 돌리기 위한
  어댑터. `p01-runner.js`가 원래 다른 DOM 구조(index.html 상단의 다크테마 도구)를 전제로
  짜여 있어서, 없는 7개 `LabApp` 멤버(log/setStatus/startTimer/stopTimer/showResults/
  registerRunner/renderModelToggle)를 채워 넣습니다. 특히 `run()`이 결과값을 반환하지
  않아서(`renderResults()` 내부에서만 소비) `jsonResultBlock`을 가로채는 방식으로 결과를
  얻습니다.
- `labdb-shim.js` — 결과 저장을 팀 공용 `public.runs`가 아니라 별도 스키마
  `pdf_analysis.runs`/`pdf_analysis.artifacts`로 보내는 어댑터. `db.js`는 무수정.

의존 파일(`../config.js ../db.js ../lab-core.js ../llm.js ../pyodide-shared.js
../pdfjs-loader.js ../p01-runner.js ../prompt_manifest.json`)도 상대경로 그대로 동작하도록
같이 옮겨왔습니다. `scripts/java_curriculum_nvidia_pipeline.py`는 이 웹 도구가 포팅해온
원본 CLI 파이프라인(참고용 — 브라우저 도구는 이 파일을 직접 실행하지 않고 같은 로직을
JS로 이식한 `p01-runner.js`를 씀).

## 실행

정적 파일이라 아무 HTTP 서버로나 `docs/` 루트를 서빙하면 됩니다:

```
python3 -m http.server 8000
# http://localhost:8000/docs/lab/curriculum-manager/ 접속
```

`config.js`에 팀 공용 Supabase 프로젝트(RLS로 보호됨, anon key는 공개되어도 안전)와 기본
NVIDIA 프록시 URL이 이미 채워져 있습니다. 실제로 분석을 돌리려면 페이지 상단 "연결 설정"에
본인 NVIDIA API 키만 입력하면 됩니다(로그인은 결과를 DB에 남기고 싶을 때만 필요, 없어도
분석 자체는 그대로 동작).

## 이 저장소에서 실행하려면

`experiments/web_lab/pdf_analysis_schema.sql`을 이 브랜치가 실제로 연결할 Supabase
프로젝트에 적용하고, PostgREST 노출 스키마(`db_schema`) 설정에 `pdf_analysis`를 추가해야
합니다(Dashboard: Settings → API → Data API, 또는 Management API). `config.js`가 현재
가리키는 프로젝트를 그대로 쓸지, Team-IZ 자체 Supabase 프로젝트로 옮길지는 팀에서 결정
필요 — 옮긴다면 `config.js`의 `TEAM_SUPABASE_URL`/`TEAM_SUPABASE_ANON_KEY`와
`DEFAULT_PROXY_URL`(NVIDIA 프록시 Cloudflare Worker)도 같이 바꿔야 합니다.

원본/최신 이력: https://github.com/popixoxipop-collab/Code_reviewer_with_feedback/tree/main/docs/lab
