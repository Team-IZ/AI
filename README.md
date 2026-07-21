# AI 서비스 (FastAPI)

교육생이 제출한 코드를 정적 분석해 **Decision Point(finding)** 를 뽑고(P02), 그 지점을 두고
**소크라틱 문답 세션**을 진행한 뒤(P03), 세션이 끝나면 전사(transcript) 전체를 대상으로
**5축 루브릭 후채점**(코드이해·설계논리·대안비교·반례대응·자기수정, 축당 1~5점·총 5~25점)을
수행하는 AI 서비스다. 채점은 세션 중이 아니라 **세션 종료 후 1회** 이루어진다.

```
React (Frontend) ←REST→ Spring Boot (Backend) ←REST→ FastAPI (이 저장소) ──→ NVIDIA LLM
```

React는 FastAPI를 직접 호출하지 않는다. FastAPI의 호출자는 Spring뿐이다.

> ⚠️ **현재 Phase 1(골격) 단계다.** 구현된 엔드포인트는 `GET /api/health` 하나뿐이고,
> 목업 프론트는 아직 FastAPI와 연결돼 있지 않다. 자세한 내용은 아래 [현재 진행 상태](#현재-진행-상태)를 반드시 읽어라.

---

## 폴더 구조

| 경로 | 설명 |
|---|---|
| `app/` | FastAPI 애플리케이션. `api/`(라우터)·`core/`(파이프라인 호출 서비스 레이어)·`storage/`(저장소 어댑터) 3계층 |
| `pipeline/` | 팀원 분석 레포에서 내재화(vendoring)한 분석 엔진 40개 파일. **수정 금지** — 목업이 E2E 검증한 기준 동작을 그대로 실행한다 |
| `trainee/` | 목업 프론트 페이지 3종(`submission.html`·`session.html`·`result.html`). standalone 모드에서 FastAPI가 정적 서빙 |
| `shared/` | 목업 프론트가 쓰는 공용 JS/CSS. P02 분석은 FastAPI로 옮겨졌고(Phase 2b), P03 세션(`p03-engine.js`)은 아직 브라우저 Pyodide 구버전 코드다 |
| `reference/` | 이식 작업 중 대조용으로 둔 원본 러너 사본. 실행되지 않는 참고 자료 |
| `tests/` | pytest 테스트 |
| `docs/` | AI 파트 내부 문서 (목업 시절 이식 기록 등) |

`webtool_driver.py`와 `shared/p02-engine.js`는 **Phase 2b에서 삭제됐다** — `submission.html`이
FastAPI 분석 API를 호출하게 되면서 브라우저 Pyodide 경로가 통째로 없어졌다(두 구현 병존 금지).
로직은 `app/core/pipeline_runner.py`·`app/core/collect.py`·`app/core/findings.py`로 이관돼 있다.
`prompt_manifest.json`은 P03 세션(`shared/p03-engine.js`)이 아직 런타임에 fetch하므로 남아 있다.

---

## 최초 설정

Python 3.12 기준으로 검증했다.

```powershell
# AI/ 디렉터리에서
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> `run_standalone.ps1`은 `.venv`가 없으면 위 3줄을 알아서 수행한다. standalone만 돌릴 거면
> 이 단계를 건너뛰고 바로 스크립트를 실행해도 된다.

### `.env` 준비

```powershell
Copy-Item .env.example .env
```

`.env`는 `.gitignore`에 등록돼 있다. 채워야 할 값:

| 변수 | 설명 |
|---|---|
| `APP_MODE` | `standalone` 또는 `integrated`. 기본값 `integrated` |
| `NVIDIA_API_KEY` | NVIDIA LLM API 키. Phase 3부터 필요 |
| `INTERNAL_API_KEY` | Spring↔FastAPI **실제 통합용** 공유 API 키(B1 결정). Spring이 `X-Internal-Key` 헤더로 보낸다. 실제 값은 아직 미정이라 빈 값이 기본이고, **비워두면 키 검증이 비활성화**된다. integrated 배포에서는 반드시 설정할 것. 비밀 값이므로 커밋 금지 |
| `SUPABASE_URL` / `SUPABASE_KEY` | standalone 전용. Supabase가 Spring 대역으로 확정 데이터를 저장한다. **Phase 5 범위라 아직 사용되지 않는다** |

그 외 운영 파라미터(`LLM_TIMEOUT_SEC=600`, `ANSWER_TIMEOUT_SEC=120`, `CALLBACK_RETRY_MAX=3`,
`WORKSPACE_TTL_SEC`, `CORS_ORIGINS`)는 `.env.example`에 기본값과 근거 주석이 함께 들어 있다.

> 🔑 **NVIDIA API 키는 팀원 각자 자기 키를 쓴다. 절대 커밋하지 마라.**
> `../docs/env.xlsx`에 평문 키가 있지만 그 값을 코드나 커밋으로 옮기지 마라.
> `.env`만 사용하고, 키가 코드·로그·커밋에 남지 않게 한다.

---

## 실행 방법

두 모드의 **엔드포인트와 요청/응답 스키마는 완전히 동일하다.** 다른 것은 "누가 호출하는가"와
"확정 데이터를 어디에 저장하는가" 뿐이다. 통합 시점에 코드 변경 없이 `APP_MODE`만 바꿔
전환되는 것이 이 설계의 합격 기준이다.

### 모드 A — standalone (AI 파트 단독 테스트)

```
목업 프론트(trainee/) ←→ FastAPI ←→ Supabase (Spring 대역)
                            └→ NVIDIA LLM
```

Spring Boot 없이 AI 레포만으로 제출→분석→문답→후채점 흐름을 테스트하는 모드다.
최종 통합 이후에도 이 명령 하나로 계속 살아 있어야 한다.

```powershell
.\run_standalone.ps1
```

`APP_MODE=standalone`을 설정하고 `127.0.0.1:8000`에서 uvicorn을 띄운다.

**정상 확인 방법:**

- 콘솔에 `Uvicorn running on http://127.0.0.1:8000` 출력
- 콘솔에 `standalone mode: supabase_store is not implemented yet (Phase 5); falling back to NullStore`
  경고가 뜬다 — **정상이다.** Phase 5 전까지 저장 어댑터는 NullStore다
- <http://127.0.0.1:8000/api/health> → `{"status":"ok","mode":"standalone","pipeline_loaded":true}`
  - `pipeline_loaded: true`가 핵심이다. `pipeline/` 내재화 코드가 서버 CPython에서 import된다는 뜻
- <http://127.0.0.1:8000/submission.html> → 제출 페이지가 뜬다

> ⚠️ 루트 `/`는 **404**다. `trainee/`에 `index.html`이 없어서 그렇다.
> 반드시 `/submission.html`로 직접 접속해라.
>
> ✅ **Phase 2b부터 `submission.html`의 제출 흐름이 실제로 동작한다.** GitHub URL 또는 ZIP을
> 제출하면 페이지가 `POST /api/v1/analyses` → `GET /api/v1/analyses/{job_id}` 폴링으로
> 서버 분석 결과를 받아 finding 목록을 그린다.
>
> 제출 화면에서 **추출 범위(전체 코드 TOTAL / 본인 커밋 기여분 OWN_COMMIT)** 를 고를 수 있다.
> `OWN_COMMIT`을 고르면 커밋 이메일 입력란이 나타난다(§3.1에서 필수). 결과 화면에는 실제 적용된
> 범위, `TOTAL` 폴백 여부와 사유, 귀속 커밋 수·검증 상태(VERIFIED/UNVERIFIED)가 함께 표시된다.
> ZIP으로 `OWN_COMMIT`을 쓰려면 `.git` 폴더를 포함하거나 `commits.txt`·`changed_files.txt`
> export를 동봉해야 한다(B5). 둘 다 없으면 `TOTAL`로 폴백하고, 해당 이메일 명의의 커밋이 0건이면
> `ATTRIBUTION_REQUIRED`로 실패한다(MEAS-02A A-1).
>
> ⚠️ 단 `session.html`(P03)·`result.html`(후채점)은 **아직 FastAPI에 연결되지 않았다**.
> 두 페이지 상단에 그 사실을 알리는 배너가 떠 있다. finding 카드의 "검증 세션 시작 →"을
> 누르면 구버전 Pyodide·프록시 경로가 도는 `session.html`로 이동하며, standalone에서는
> 정상 동작하지 않는다. Phase 3·4의 작업이다.

### 모드 B — integrated (팀 통합)

```
React ←→ Spring Boot ←→ FastAPI ──→ NVIDIA LLM
```

FastAPI는 **저장하지 않는다.** 확정 데이터는 전부 응답(또는 완료 콜백)으로 Spring에 반환하고,
영속화·테넌트 격리·감사 로그는 Spring이 전담한다(DB 단일 소유자는 Spring).
목업 프론트도 서빙하지 않고, CORS 미들웨어도 붙지 않는다(호출자가 Spring뿐이라 브라우저
preflight 경로가 없다).

```powershell
$env:APP_MODE = "integrated"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

개발 중 자동 리로드가 필요하면 `--reload`를 붙인다.

### Spring 없이 API 직접 두드리기

`/api/health`는 운영 모니터링용이라 **인증 면제**다. 키 설정 여부와 무관하게 200을 준다:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","mode":"integrated","pipeline_loaded":true}
```

### 두 개의 API 키 — 목업 개발 키 vs 실제 통합 키

`X-Internal-Key`로 받아들이는 키는 **두 종류이고 유효 범위가 다르다.**

| | `STANDALONE_DEV_API_KEY` (개발 키) | `INTERNAL_API_KEY` (실제 키) |
|---|---|---|
| 용도 | 목업 페이지(`trainee/`)가 자기 서버를 호출할 때 | Spring이 FastAPI를 호출할 때 |
| 값 | `iz-get-standalone-dev-key` — **공개 상수**. `app/config.py`에 하드코딩 | **비밀**. 아직 미정이라 기본 빈 값. `.env`로 주입 |
| 어디서 통하나 | `APP_MODE=standalone`**에서만** | 설정돼 있으면 두 모드 모두 |
| integrated에서 | **항상 401로 거부** | 강제됨 |

핵심은 **개발 키가 공개돼도 프로덕션 인증을 우회할 수 없다**는 점이고, 이것은 문서가 아니라
코드로 보장된다 — `app/api/deps.py`가 개발 키를 `app_mode == "standalone"`일 때만 통과시키고,
integrated에서는 *실제 키 설정 여부와 무관하게* 명시적으로 거부한다(`INTERNAL_API_KEY`가 비어
검증이 비활성인 상태에서도 개발 키만은 통과시키지 않는다). 이 불변식은
`tests/test_internal_auth.py`의 `test_dev_key_is_rejected_in_integrated_mode` 등이 고정한다.

이 구조는 **standalone이 로컬 개발 도구 전용 모드**라는 전제 위에 성립한다. 목업 서빙·CORS·공개
개발 키가 모두 그 전제에 묶여 있으므로 **standalone 서버를 외부에 노출하지 말 것.**

덕분에 두 키는 공존할 수 있다 — `.env`에 실제 키를 채워둔 채로도 `run_standalone.ps1`의 목업은
그대로 동작하고, 실제 통합 시에는 `INTERNAL_API_KEY`에 값만 채우면 된다. 목업은 개발 키를
하드코딩해서 보내므로 **사용자가 키를 입력할 필요가 없다**(연결 설정 패널에 키 입력란도 없다).

`/api/v1/*` 업무 엔드포인트는 `INTERNAL_API_KEY`가 설정돼 있으면 `X-Internal-Key` 헤더를 요구한다:

```bash
curl -H "X-Internal-Key: $INTERNAL_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"attempt_id":"...","method":"GITHUB_URL","source":{"repo_url":"..."}}' \
     http://127.0.0.1:8000/api/v1/analyses
```

standalone에서 목업 없이 직접 두드릴 때는 개발 키를 쓰면 된다:

```bash
curl -H "X-Internal-Key: iz-get-standalone-dev-key" \
     http://127.0.0.1:8000/api/v1/analyses/<job_id>
```

> `/api/v1/analyses`는 Phase 2a에서 구현됐다(§3.1/§3.2). 요청 스키마는 API 명세서 §3.1 기준이며
> 확정 시 갱신한다. `OWN_COMMIT`으로 요청하려면 `extraction_scope`와 `commit_email`을 함께 보낸다.

### Swagger UI

서버가 뜬 상태에서 <http://127.0.0.1:8000/docs> 로 접속하면 현재 구현된 엔드포인트를
브라우저에서 바로 호출해볼 수 있다. 기계가 읽을 스펙은 `/openapi.json`이다.

현재 `/openapi.json`의 `paths`에는 `/api/health` 하나만 들어 있다. Phase 2~4를 진행하며
여기가 채워지므로, "지금 실제로 뭐가 구현됐나"를 확인하는 가장 빠른 방법이 `/docs`다.

---

## 자동 테스트

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

현재 결과: **36 passed, 1 skipped** (skip 1건은 네트워크 의존 GitHub clone 테스트).

| 파일 | 검증 내용 |
|---|---|
| `tests/test_pipeline_smoke.py` | Pyodide 없이 서버 CPython에서 `two_tier_scan.scan()` → `score_findings.score()`가 샘플 코드 트리에 대해 에러 없이 완주하고 결과 JSON에 scan/judgment 구조가 있는지. finding 내용의 정확성 검증은 목표가 아니다. `/api/health`가 모드·파이프라인 상태를 보고하는지도 확인 |
| `tests/test_app_modes.py` | 모드별 앱 조립 고정 — CORS는 standalone에만 붙는다, 목업 정적 서빙은 standalone에만 있다, 브라우저 Pyodide 경로가 삭제된 채로 유지된다, 목업 페이지의 정적 참조가 전부 실존한다, standalone 목업 제출 흐름(ZIP→job→폴링→finding)이 동작한다 |
| `tests/test_analyses.py` | 분석 API(§3) — ZIP 제출 E2E, 수집 규칙(SKIP/확장자/.ipynb/zip slip), 커밋 귀속·폴백, 실패 코드, B1 인증 |
| `tests/test_internal_auth.py` | B1 공유 API 키 계약 — 키 누락/오류 시 401, 키 미설정 시 검증 비활성, `/api/health`는 양 모드 모두 인증 면제 |

Phase 1의 xfail(`test_webtool_driver_fetch_path_is_not_served_yet`)은 **Phase 2b에서 삭제됐다** —
그 xfail이 고정하던 "목업 제출 흐름이 죽어 있다"는 상태 자체가 해소됐기 때문이다. 대신
`test_standalone_mockup_submission_flow_works`가 그 자리에서 "흐름이 동작한다"를 고정한다.

> 💡 **Windows 콘솔 인코딩 주의**: 테스트·서버 로그에 한글과 유니코드 기호가 섞여 있어
> 기본 cp949 콘솔에서는 `UnicodeEncodeError`가 날 수 있다. 위처럼 `PYTHONIOENCODING=utf-8`을
> 설정하면 안전하다. bash에서는 `PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest tests/ -v`.

---

## 현재 진행 상태

| Phase | 내용 | 상태 |
|---|---|---|
| Phase 0 | 팀원 분석 레포 내재화 (`pipeline/` 40개 파일) | ✅ 완료 |
| Phase 1 | FastAPI 골격 — `app/` 3계층, 설정, `ResultStore` 인터페이스, health, 스모크 테스트 | ✅ 완료 |
| Phase 2 | P02 분석 API + **`submission.html` 연결** | 예정 |
| Phase 3 | P03 세션 API + **`session.html` 연결** | 예정 |
| Phase 4 | 후채점 API + **`result.html` 연결** | 예정 |
| Phase 5 | standalone 저장 계층(Supabase) + 전체 E2E + 계약 확정 | 예정 |

### 목업 페이지는 Phase 2~4에 걸쳐 순차 연결된다

이게 이 프로젝트의 핵심 작업 원칙이다. 각 Phase는 **API 엔드포인트와 목업 페이지 연결을 함께**
내놓는다. 목업 연결을 뒤로 몰면 그동안 standalone이 죽어 있고, 더 나쁘게는 목업이 브라우저
Pyodide로 도는 동안 정작 통합에 쓸 서버 코드가 검증되지 않는다.

목업이 FastAPI를 호출하게 만들어야 **"standalone으로 테스트한다 = 통합 시 Spring이 호출할
바로 그 코드 경로를 테스트한다"** 가 성립한다.

- 각 Phase 완료 시점에 `.\run_standalone.ps1` 하나로 그 시점까지의 기능을 실제 사용할 수 있어야 한다.
- 연결이 끝난 페이지에서는 해당 Pyodide·프록시 코드를 **삭제**한다(두 구현 병존 금지).
- 아직 연결 안 된 페이지는 "이 단계는 아직 미연결" 안내를 표시한다 — 반쯤 동작하며 조용히
  이상해지는 것보다 낫다.
- 최종 통합 후에도 목업은 계속 살아 있다. `APP_MODE` 전환만으로 standalone/integrated를 오간다.

### 그래서 지금(Phase 1) 실제로 되는 것 / 안 되는 것

**되는 것**

- 양 모드로 서버 기동, `/api/health` 200 응답
- 내재화한 분석 파이프라인이 Pyodide 없이 서버 CPython에서 scan→score 완주 (스모크 테스트로 검증)
- `X-Internal-Key` 인증 계약
- standalone에서 목업 페이지 3종이 HTTP 200으로 **서빙**됨
- **P02 분석 API(명세 §3) + `submission.html` 연결** — GitHub URL·ZIP 제출 → job 폴링 →
  finding + `code_context` 렌더링까지 standalone에서 실제로 동작한다(Phase 2)

**안 되는 것 (솔직하게)**

- **`session.html`(P03 문답)·`result.html`(결과 리포트)은 아직 FastAPI와 연결돼 있지 않다.**
  두 페이지 상단에 미연결 배너가 뜬다. Phase 3~4의 작업이다.
- 분석 결과에서 "검증 세션 시작 →"을 누르면 이동은 되지만 세션은 구버전 브라우저 경로라
  standalone에서 돌지 않는다.
- `/api/v1/*` 업무 엔드포인트는 분석(§3) 2개뿐이다. 세션(§4)·채점(§5)은 미구현.
- B3 완료 콜백(`callback_url`)은 값을 받아 보관만 하고 전송은 미구현이다.
- Supabase 저장은 미구현. standalone에서도 NullStore로 폴백한다(기동 시 경고 출력).
  즉 분석 결과는 서버 인메모리 job에만 있고 프로세스를 재시작하면 사라진다.

> `app/main.py` 등 일부 코드 주석은 목업 연결 시점을 "Phase 5"로 적고 있는데, 이는 계획서 §3의
> Phase 2~4 공통 원칙(나중에 확정)보다 먼저 작성된 것이다. **계획서가 최신이자 기준**이다.

---

## 관련 문서

| 문서 | 내용 |
|---|---|
| [`PLAN_FASTAPI_MIGRATION.md`](./PLAN_FASTAPI_MIGRATION.md) | FastAPI 전환 계획서. 확정된 설계 결정(§0), 운영 모드(§1.5), Phase별 작업(§3), 미결 사항(§5) |
| [`../docs/AI-Backend_API_명세서_v0.1.md`](../docs/AI-Backend_API_명세서_v0.1.md) | AI↔Backend API 명세서 (**내용은 v0.2**, 파일명만 v0.1). 엔드포인트 스키마·B1~B8 협의 결정 |
| [`pipeline/VENDORED.md`](./pipeline/VENDORED.md) | 분석 파이프라인 내재화 기록 — 출처 레포·커밋 SHA·복사 파일 목록·디렉터리 구조 주의사항 |
| [`docs/MOCKUP_PORTING_HISTORY.md`](./docs/MOCKUP_PORTING_HISTORY.md) | 전환 이전 목업(Pyodide) 시절의 이식 방법론·E2E 검증 내역·알려진 동작 차이 (이력 보존) |

`../docs/docs_for_read/`에 기획 문서의 Markdown 변환본이 있다(`README.md`가 인덱스).
단 이 문서들은 확정 스펙이 아니라 계속 바뀌는 기획 자료이므로, 실제 코드나 최근 결정과
다르면 문서를 맹신하지 말고 확인 후 진행한다.
</content>
