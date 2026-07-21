# 교안 관리 (Curriculum Manager) — PDF Analysis

Team-IZ Frontend의 `manager/curriculum.html` 와이어프레임 UI를 따라 만든, 실제로 동작하는
교안(PDF) 분석 도구입니다. Pipeline Lab의 P01(교안분석) 파이프라인(청크 분석 → refine →
그래프 생성)을 그대로 재사용하며, 이 저장소에 필요한 파일(웹 페이지, LLM 파이프라인,
Cloudflare Worker 프록시, DB 스키마)이 전부 포함되어 있어 **다른 저장소를 참고하지 않고도**
이 폴더/이 저장소 안의 내용만으로 배포·유지보수가 가능합니다.

## 배포 링크

**https://team-iz.github.io/AI/lab/curriculum-manager/** (2026-07-21부터 라이브,
`feat/pdf_analysis` 브랜치의 `/docs`를 GitHub Pages가 직접 서빙 — `develop`으로 머지된
상태 아님). 인프라는 Team-IZ 전용으로 분리된 Supabase 프로젝트(`team-iz-curriculum-manager`)
+ Cloudflare Worker(`team-iz-nvidia-proxy`)를 씀. 로그인/DB 저장 기능만 Google OAuth
클라이언트 등록이 남아 있고(아래 "자체 배포" 5번), 분석 자체는 지금 바로 동작함.

## 구성

- `index.html` — 목록/등록/교안 구성/교안 연결 4탭. 교안 등록 탭에서 PDF를 올리면
  실제 LLM 파이프라인이 실행되고, "교안 구성" 탭에 섹션별 [출처 페이지 범위 | 키워드]
  표로 결과가 뜹니다. 실행 중 "취소"를 누르면 실제로 파이프라인이 중단됩니다(뒤로가기가
  아님 — 백그라운드에서 계속 도는 게 아니라 진행 중인 요청이 끝나는 대로 결과를 버림).
  목록 탭에서 "− 교안 삭제"로 항목을 지울 수 있습니다(본인이 등록한 항목만).
- `dashboard.html` / `report.html` / `projects.html` — Team-IZ 와이어프레임을 그대로 포팅한
  **정적 참고 화면**(실제 데이터 연동 없음, nav가 죽은 링크로 남지 않도록 붙여둔 것).
- `labapp-shim.js` — `../p01-runner.js`(원본 파이프라인, 무수정)를 이 페이지에서 돌리기
  위한 어댑터. `p01-runner.js`가 원래 다른 DOM 구조를 전제로 짜여 있어서, 없는 7개
  `LabApp` 멤버(log/setStatus/startTimer/stopTimer/showResults/registerRunner/
  renderModelToggle)를 채워 넣습니다. `run()`이 결과값을 반환하지 않아서
  (`renderResults()` 내부에서만 소비) `jsonResultBlock`을 가로채는 방식으로 결과를 얻고,
  같은 원리로 `log`/`setStatus`에 취소 체크포인트를 심어 "취소" 버튼이 실제로 파이프라인을
  멈추게 합니다(`p01-runner.js` 자체는 여전히 무수정).
- `labdb-shim.js` — 결과 저장을 팀 공용 `public.runs`가 아니라 별도 스키마
  `pdf_analysis.runs`/`pdf_analysis.artifacts`로 보내는 어댑터. `../db.js`는 무수정.

의존 파일(`../config.js ../db.js ../lab-core.js ../llm.js ../pyodide-shared.js
../pdfjs-loader.js ../p01-runner.js ../prompt_manifest.json`)도 상대경로 그대로 동작하도록
같이 들어 있습니다. `scripts/java_curriculum_nvidia_pipeline.py`는 이 웹 도구와 같은 로직의
원본 CLI 파이프라인(참고용 — 브라우저 도구는 이 파일을 직접 실행하지 않고 JS로 이식한
`p01-runner.js`를 씀). `worker/`는 NVIDIA API를 프록시하는 Cloudflare Worker 소스(아래
"자체 배포" 참고). `experiments/web_lab/`엔 DB 스키마 2개(`members_schema.sql`,
`pdf_analysis_schema.sql`)가 들어 있습니다.

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
2. SQL Editor에서 **순서대로** 실행: `experiments/web_lab/members_schema.sql` →
   `experiments/web_lab/pdf_analysis_schema.sql` (`pdf_analysis.runs`가
   `public.members`를 참조하므로 순서 중요).
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
cd worker
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

### 4) GitHub Pages 배포 (완료)

Settings → Pages에서 소스를 `feat/pdf_analysis` 브랜치의 `/docs`로 지정해 라이브
(위 "배포 링크" 참고). `develop`이 아니라 이 브랜치를 직접 서빙하는 상태라, `develop`/
`main`으로 나중에 머지하기로 하면 Pages 소스도 그쪽으로 다시 지정해야 링크가 계속
유효합니다(머지 자체와는 독립적인 별도 설정이라 자동으로 안 따라감).

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
