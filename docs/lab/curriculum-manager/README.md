# 교안 관리 (Curriculum Manager) — PDF Analysis

Team-IZ Frontend의 `manager/curriculum.html` 와이어프레임 UI를 따라 만든, 실제로 동작하는
교안(PDF) 분석 도구입니다. Pipeline Lab의 P01(교안분석) 파이프라인(청크 분석 → refine →
그래프 생성)을 그대로 재사용하며, 이 저장소에 필요한 파일(웹 페이지, LLM 파이프라인,
Cloudflare Worker 프록시, DB 스키마)이 전부 포함되어 있어 **다른 저장소를 참고하지 않고도**
이 폴더/이 저장소 안의 내용만으로 배포·유지보수가 가능합니다.

## 배포 링크

**https://team-iz.github.io/AI/lab/curriculum-manager/** (2026-07-21부터 라이브).

배포 방식은 2026-07-28에 바뀌었습니다. 예전엔 GitHub Pages가 `feat/pdf_analysis` 브랜치의
`/docs`를 **직접** 서빙했지만, 지금은 `.github/workflows/pages.yml`(GitHub Actions)이 네 개
브랜치를 하나의 사이트로 **조립**합니다 — 자세한 건 아래 "§4 GitHub Pages 배포" 참고.
Pages 설정을 예전처럼 "브랜치+/docs"로 되돌리면 나머지 세 도구의 배포가 전부 깨집니다.

인프라는 Cloudflare Worker(`team-iz-nvidia-proxy`) + Supabase 공유 프로젝트
(`code-reviewer-pipeline-lab`, ref `oziaeqcvrkrqkhwrybfj` — 2026-07-22에 전용 프로젝트에서
다시 이쪽으로 옮김, `../config.js` 헤더 주석 참고)를 씀. 로그인/DB 저장 기능만 Google OAuth
클라이언트 등록이 남아 있고(아래 "자체 배포" 5번), 분석 자체는 지금 바로 동작함.

## 구성

- `index.html` — 목록/등록/교안 구성/교안 연결 4탭. 교안 등록 탭에서 PDF를 올리면
  실제 LLM 파이프라인이 실행되고, "교안 구성" 탭에 섹션별 [출처 페이지 범위 | 키워드]
  표로 결과가 뜹니다. 실행 중 "취소"를 누르면 실제로 파이프라인이 중단됩니다(뒤로가기가
  아님 — 백그라운드에서 계속 도는 게 아니라 진행 중인 요청이 끝나는 대로 결과를 버림).
  목록 탭에서 "− 교안 삭제"로 항목을 지울 수 있습니다(본인이 등록한 항목만).
- `labapp-shim.js` — `../p01-runner.js`(원본 파이프라인, 무수정)를 이 페이지에서 돌리기
  위한 어댑터. `p01-runner.js`가 원래 다른 DOM 구조를 전제로 짜여 있어서, 없는 7개
  `LabApp` 멤버(log/setStatus/startTimer/stopTimer/showResults/registerRunner/
  renderModelToggle)를 채워 넣습니다. `run()`이 결과값을 반환하지 않아서
  (`renderResults()` 내부에서만 소비) `jsonResultBlock`을 가로채는 방식으로 결과를 얻고,
  같은 원리로 `log`/`setStatus`에 취소 체크포인트를 심어 "취소" 버튼이 실제로 파이프라인을
  멈추게 합니다(`p01-runner.js` 자체는 여전히 무수정).
- `labdb-shim.js` — 결과 저장을 팀 공용 `public.runs`가 아니라 별도 스키마
  `pdf_analysis.runs`/`pdf_analysis.artifacts`로 보내는 어댑터. `../db.js`는 무수정.

의존 파일(`../config.js ../db.js ../lab-core.js ../llm.js ../pdfjs-loader.js
../p01-runner.js ../prompt_manifest.json`)도 상대경로 그대로 동작하도록 같이 들어 있습니다.
`cli/java_curriculum_pipeline.py`는 이 웹 도구와 같은 로직의 원본 CLI 파이프라인(참고용 —
브라우저 도구는 이 파일을 직접 실행하지 않고 JS로 이식한 `p01-runner.js`를 씀).
`services/nvidia-proxy/`는 NVIDIA API를 프록시하는 Cloudflare Worker 소스,
`services/p01-orchestrator/`는 브라우저를 닫아도 분석이 계속 진행되게 하는 서버측 잡
오케스트레이터(Durable Object)입니다(아래 "자체 배포" 참고). `db/`엔 DB 스키마 3개
(`01_members.sql`, `02_pdf_analysis.sql`, `03_model_notes.sql`)가 적용 순서대로 들어 있습니다.

> **2026-08-03**: `dashboard.html` / `report.html` / `projects.html`(실제 데이터 연동이 전혀
> 없는 정적 와이어프레임 목업 3개)과 `../pyodide-shared.js`(이 브랜치엔 브라우저측 파이썬
> 실행 단계가 없어서 `LabPyodide`를 아무도 참조하지 않던 죽은 코드)를 삭제했습니다.
> `index.html`의 nav도 그 3개 링크를 빼고 "교안" 하나만 남겼습니다.

## 빠른 실행 (지금 바로 켜보기)

정적 파일이라 아무 HTTP 서버로나 `docs/` 루트를 서빙하면 됩니다:

```
python3 -m http.server 8000
# http://localhost:8000/docs/lab/curriculum-manager/ 접속
```

`config.js`에 Supabase 프로젝트/기본 NVIDIA 프록시 URL이 이미 채워져 있습니다(Team-IZ
전용으로 분리된 프로젝트/워커 — 아래 "자체 배포" 참고). 실제로 분석을 돌리려면 페이지
상단 "연결 설정"에 본인 NVIDIA API 키만 입력하면 됩니다(로그인은 결과를 DB에 남기고
싶을 때만 필요, 없어도 분석 자체는 그대로 동작).

## 자체 배포 (이미 완료 — 아래는 실제로 한 것 + 재현 방법)

Supabase 프로젝트/Cloudflare Worker/GitHub Pages 전부 이미 배포되어 있습니다(위 "배포
링크" 참고). 다만 셋 다 popixoxipop@gmail.com 개인 계정 위에 만들어진 것이라(Team-IZ
소유 별도 클라우드 계정이 아직 없어서), 진짜 다른 계정으로 옮기고 싶을 때를 위해 아래에
전체 재현 절차를 남겨둡니다:

### 1) Supabase 프로젝트

1. [supabase.com](https://supabase.com)에서 새 프로젝트 생성.
2. SQL Editor에서 **파일명 숫자 순서대로** 실행: `db/01_members.sql` →
   `db/02_pdf_analysis.sql` → `db/03_model_notes.sql`.
   순서가 중요한 이유: `pdf_analysis.runs`(02)가 `public.members`(01)를 참조하고,
   `public.model_notes`(03)의 `updated_by`도 `public.members`를 참조합니다.
   03은 모델 선택 UI의 "비고"를 팀 공유 메모로 저장하는 테이블입니다 — 없으면 비고가
   `lab-core.js`의 정적 `CURATED_MODELS` 텍스트로만 표시되고 편집/저장이 안 됩니다.
3. Settings → API → Data API에서 노출 스키마(`db_schema`)에 `pdf_analysis`를 추가
   (기본값 `public`에 콤마로 이어서 `public,pdf_analysis`). 반영까지 5-10초 정도 걸릴 수
   있음 — 저장 직후 안 바뀐 것처럼 보여도 잠시 후 재확인.
4. Authentication → Sign In / Providers → Google 활성화 (자체 Google Cloud Console
   OAuth 클라이언트 ID/Secret 필요 — 로그인 없이도 분석 자체는 동작하니 DB 저장 기능을
   당장 안 쓸 거면 건너뛰어도 됨).
5. Authentication → URL Configuration의 Redirect URLs(`uri_allow_list`)에 실제 배포될
   `curriculum-manager/index.html` 경로를 추가(`db.js`의 `signInWithGoogle()`이
   `origin+pathname`을 redirectTo로 쓰기 때문에, 정확한 경로가 허용 목록에 없으면
   로그인 후 엉뚱한 곳으로 리다이렉트됨).
6. Project Settings → API에서 Project URL / anon public key를 확인해 아래 4번에 사용.

### 2) Cloudflare Worker (NVIDIA 프록시)

```
cd services/nvidia-proxy
wrangler login
wrangler kv namespace create NVIDIA_JOBS      # 출력된 id를 wrangler.toml에 반영
wrangler queues create <새-큐-이름>            # 큐는 파일에서 자동 생성되지 않음, 먼저 생성 필요
wrangler deploy
```

`wrangler.toml`의 `name`/큐 이름은 **다른 Cloudflare 계정으로 옮길 때만** 원래 값
(`team-iz-nvidia-proxy`/`team-iz-nvidia-jobs-queue`)을 그대로 써도 됩니다. 같은 계정
안에서 또 다른 인스턴스를 만드는 거라면(예: 스테이징용) 반드시 다른 이름을 써야 함 —
Worker 이름도 Queue 이름도 계정 스코프라, 같은 이름을 다시 배포하면 새로 만드는 게
아니라 기존 걸 덮어씁니다(2026-07-21에 이걸로 popixoxipop-collab의 원래 공유 워커를
덮어쓸 뻔했다가 이름을 분리해서 피함).

서버 쪽에 저장해야 할 NVIDIA API 키 secret은 없습니다 — 이 워커는 상태 없이 동작하며,
호출자가 보낸 `x-nvidia-api-key` 헤더(각자 페이지에서 입력한 본인 키)를 그대로 NVIDIA로
전달만 합니다(`nvidia-proxy.js:213-214`).

### 3) `config.js` 값 교체

`docs/lab/config.js`에서 아래 2곳을 1)/2)의 결과로 교체(정확한 줄은 파일 안
"THE 2 SWAP POINTS" 주석 참고):

- `TEAM_SUPABASE_URL` / `TEAM_SUPABASE_ANON_KEY` → 새 Supabase 프로젝트 값
- `DEFAULT_PROXY_URL` → 새로 배포한 Worker URL

### 4) GitHub Pages 배포 (완료 — 2026-07-28부터 조립 방식)

**Pages 소스는 "브랜치 + 폴더"가 아니라 GitHub Actions입니다.** Settings → Pages를 예전처럼
`feat/pdf_analysis` + `/docs`로 되돌리지 마세요 — 그러면 이 도구만 남고 나머지 세 도구
(code-qna, poc, codemap)의 배포가 통째로 사라집니다.

이유: GitHub Pages는 **저장소당 소스를 하나만** 가질 수 있는데 이 저장소는 도구 4개를 브랜치
4개로 나눠 갖고 있습니다. 그래서 `.github/workflows/pages.yml`이 네 브랜치를 전부 checkout해
하나의 `site/`로 조립한 뒤 그걸 Pages 아티팩트로 올립니다:

| 소스 브랜치 | 가져오는 경로 | 사이트 URL |
|---|---|---|
| `feat/pdf_analysis` (이 브랜치) | `docs/` | `/lab/**` ← **이 도구** |
| `feat/code_Q&A` | 저장소 루트 | `/lab/code-qna/**` |
| `feat/poc_full` | 저장소 루트 | `/lab/poc/**` |
| `feature/code-importance-map` | `docs/lab/codemap/` | `/lab/codemap/**` |

사이트 루트 `index.html`(네 도구 링크 허브)은 `feat/poc_full`의 `pages-hub/index.html`에서
복사됩니다.

이 브랜치에서 실무적으로 중요한 점 3가지:

1. **`docs/`의 내부 구조가 곧 배포 URL입니다.** 조립 단계가 `src-p01/docs/` → `site/`로
   그대로 rsync하므로 `docs/lab/curriculum-manager/index.html`은 `/lab/curriculum-manager/`가
   됩니다. `docs/` 안의 파일을 옮기거나 이름을 바꾸면 라이브 URL이 깨집니다.
2. **워크플로의 smoke-check가 이 경로들을 하드코딩**해서 확인합니다 —
   `site/lab/curriculum-manager/index.html`, `site/lab/p01-runner.js`,
   `site/lab/prompt_manifest.json`. 셋 중 하나라도 없으면 빌드가 실패하고, **네 도구 전부**
   배포가 멈춥니다.
3. **`pages.yml` 자체가 네 브랜치에 복사본으로 존재**합니다(push 이벤트는 푸시된 브랜치의
   워크플로 파일을 실행). 워크플로를 고칠 땐 네 곳 전부 같이 고쳐야 합니다.

되돌리려면(단일 브랜치 Pages로): Pages 소스를 `{branch: feat/pdf_analysis, path: /docs}`로
바꾸고, `feat/code_Q&A`의 트리를 이 브랜치의 `docs/lab/code-qna/`로 복원한 뒤, 네 브랜치에서
`pages.yml`을 지우면 됩니다(= 2026-07-28 이전 상태, 중복 사본 드리프트 문제도 같이 돌아옴).

### 5) Google OAuth 클라이언트 (아직 안 됨 — 수동 단계 필요)

로그인/DB 저장 기능에만 필요, 분석 자체는 이것 없이 지금 그대로 동작합니다. `gcloud`
CLI로 자동화할 방법이 없습니다(관련 커맨드그룹 `gcloud iap oauth-brands`/
`oauth-clients`는 IAP 전용이고 2026-03-19부로 완전히 shutdown됨 — 라이브 확인).
Google Cloud Console에서 수동으로: APIs & Services → OAuth consent screen 설정 →
Credentials → Create OAuth client ID → Web application → 승인된 리디렉션 URI에
`https://<supabase-project-ref>.supabase.co/auth/v1/callback` 추가. 발급받은 Client
ID/Secret을 Supabase Dashboard → Authentication → Providers → Google에 입력하고,
Authentication → URL Configuration의 Redirect URLs에도 위 "배포 링크"의 정확한 경로
(`https://team-iz.github.io/AI/lab/curriculum-manager/index.html`)를 추가해야
로그인 후 리다이렉트가 정상 동작합니다.

## 이력

Team-IZ Frontend의 gh-pages 와이어프레임 UI를 참고해서 만들어졌고, Pipeline Lab의
P01(교안분석) 파이프라인 코드를 최초에
[popixoxipop-collab/Code_reviewer_with_feedback](https://github.com/popixoxipop-collab/Code_reviewer_with_feedback/tree/main/docs/lab)에서
포팅해 왔습니다. 위 "구성"에 나열된 대로 필요한 파일이 전부 이 저장소 안에 있으므로,
유지보수(버그 수정, 기능 추가)는 이 저장소 안에서 독립적으로 진행하면 됩니다.
