# AI 파트 FastAPI 구축 계획

> 마지막 갱신: 2026-07-22
> 작업 브랜치: `feature/fastapi-migration`
> 이 문서가 **AI 파트 진행 상황의 단일 기준**이다. 새 세션은 여기부터 읽는다.
> 구조·계약의 설명은 `README.md`에 있다. 이 문서는 **무엇을 어떤 순서로 할지**만 다룬다.

---

## 현재 상태

**재구축 중. 빈 골격에서 시작한다.**

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있었다. 팀 역할이 "골격·백엔드 통신 담당"으로 정리되면서 PoC를 더 이상 이 브랜치에서 다루지 않기로 했고, 전량 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋되지 않음).

| 항목 | 상태 |
|---|---|
| 추적 파일 | `.env.example` `.gitignore` `README.md` `PLAN_FASTAPI_MIGRATION.md` `requirements.txt` |
| 엔드포인트 | 0 / 9 |
| 테스트 | 없음 |
| 다음 작업 | **1단계 — `main.py` + `health.py` + `pytest.ini`** |

---

## 1. 목표

**엔진이 하나도 없어도 백엔드가 붙일 수 있는 FastAPI 골격을 만든다.**

완료 기준:

- [ ] 명세상 엔드포인트 9개가 전부 응답한다(내용은 스텁이어도 무방)
- [ ] Swagger `/docs`에 요청·응답 스키마가 정확히 표시된다
- [ ] Postman 컬렉션으로 9개 전부 호출 검증된다
- [ ] 팀원 엔진이 도착하면 `app/engines/` 안쪽만 바꿔서 교체된다
- [ ] 백엔드 팀원이 이 서비스에 대고 개발을 시작할 수 있다

**범위 밖**: 브라우저 PoC, 목업 페이지, standalone 모드, Supabase 저장 계층. 전부 만들지 않는다.

---

## 2. 단계별 계획

각 단계는 **그 자체로 동작하고 커밋 가능한 단위**다. 한 단계에 FastAPI 개념 하나씩 붙인다.

### 1단계 — 앱 골격

| | |
|---|---|
| 산출물 | `app/main.py`, `app/api/health.py`, `pytest.ini`, `tests/test_health.py` |
| 배우는 것 | `FastAPI()`, `APIRouter`, `include_router`, `TestClient` |
| DoD | `uvicorn app.main:app`으로 뜨고 `GET /api/health`가 200. `/docs`에 노출 |
| 주의 | `pytest.ini`에 `norecursedirs = _legacy .venv` 필수 — 없으면 `_legacy/tests/`를 수집해 깨진다 |

### 2단계 — 설정과 인증

| | |
|---|---|
| 산출물 | `app/config.py`, `app/api/deps.py`, `tests/test_auth.py` |
| 배우는 것 | `BaseSettings`, `Depends`, `Header`, 라우터 단위 의존성 |
| DoD | `X-Internal-Key` 없거나 틀리면 401. `/api/health`는 면제 |
| 결정 | 키 미설정(빈 값)이면 검증을 건너뛴다 — 로컬 개발 편의. 운영에서는 반드시 설정 |

### 3단계 — 분석 요청 스텁

| | |
|---|---|
| 산출물 | `app/schemas/common.py`, `app/schemas/analysis.py`, `app/api/analyses.py`, `tests/test_analyses.py` |
| 배우는 것 | pydantic 요청 모델, `status_code=202`, 422 검증, `Literal` enum |
| DoD | `POST /api/v1/analyses`가 202 + `{jobId, status:"QUEUED"}`. 필수 필드 누락 시 422 |
| 선결 | **§3 미결 3건이 정해져야 한다**(prefix·필드 표기·에러 형식) |
| 주의 | `method=ZIP_WITH_GITLOG`는 multipart라 JSON과 요청 형태가 다르다. 한 오퍼레이션에서 Body와 Form을 섞을 수 없으므로 라우터가 Content-Type으로 분기한다 |

### 4단계 — 분석 조회 스텁

| | |
|---|---|
| 산출물 | `app/schemas/analysis.py`(응답 모델 추가), `app/api/analyses.py` |
| 배우는 것 | 경로 파라미터, `response_model`, 404 처리 |
| DoD | `GET /api/v1/analyses/{jobId}`가 고정 결과 반환. 모르는 id는 404 |

### 5단계 — 엔진 소켓

| | |
|---|---|
| 산출물 | `app/engines/base.py`, `app/engines/stub.py`, `tests/test_engines.py` |
| 배우는 것 | `Protocol`, 의존성 주입으로 구현체 교체 |
| DoD | `engine_mode` 설정으로 스텁/실물이 갈린다. 라우터는 어느 쪽인지 모른다 |
| 원칙 | 엔진은 FastAPI·pydantic·HTTP를 모른다. `dict` in, `dict` out |

### 6단계 — job 수명주기

| | |
|---|---|
| 산출물 | `app/jobs.py`(또는 `app/core/jobs.py`), 테스트 |
| 배우는 것 | `BackgroundTasks`, 상태 전이(QUEUED→RUNNING→SUCCEEDED/FAILED) |
| DoD | 202 즉시 반환 후 백그라운드에서 상태가 바뀌고 폴링으로 관측된다 |
| 한계 | 인메모리 dict라 프로세스 재시작 시 유실되고 **워커 1개에서만 동작한다**. 스케일 필요 시 Redis/DB로 이전 |

### 7단계 — 세션 엔드포인트 4개 스텁

| | |
|---|---|
| 산출물 | `app/schemas/session.py`, `app/api/sessions.py` |
| 대상 | `POST /sessions`, `POST /sessions/{id}/answers`, `GET /sessions/{id}`, `POST /sessions/{id}/restore` |
| DoD | 4개 전부 응답. 답변 제출은 **동기**(학생이 화면에서 대기) |
| 주의 | 답변 제출에 멱등키가 있다. 같은 키로 재요청하면 같은 응답을 돌려줘야 한다 |

### 8단계 — 채점 엔드포인트 2개 스텁

| | |
|---|---|
| 산출물 | `app/schemas/grading.py`, `app/api/gradings.py` |
| 대상 | `POST /gradings`(202), `GET /gradings/{jobId}` |
| DoD | **엔드포인트 9/9 완성.** Postman 컬렉션 작성 → 백엔드에 전달 |

### 9단계 — 엔진 이식 시작

이 시점부터는 팀원 결과물 진행에 따라간다. 백로그는 §4.

---

## 3. 팀 협의 대기 — 3단계 전에 필요

백엔드 `origin/develop`의 관례와 기존 AI 명세가 어긋난다. 스키마를 쓰기 전에 정해야 하고, 나중에 바꾸면 9개를 전부 다시 손댄다.

| # | 항목 | 백엔드 | AI 명세 | 잠정 방침 | 상태 |
|---|---|---|---|---|---|
| C1 | 경로 prefix | `/api/v0` | `/api/v1` | `/api/v1` 유지 | ❓ |
| C2 | 필드 표기 | camelCase | snake_case | camelCase로 통일 | ❓ |
| C3 | 에러 형식 | `{timestamp,status,error,message,path,fieldErrors}` | `{error:{code,message,retryable}}` | AI 형식 유지 | ❓ |

**C2가 제일 급하다.** 3단계 첫 스키마부터 갈린다.
**C3 근거**: 백엔드 형식에는 `retryable`이 없다. Spring이 재시도할지 판단할 근거가 사라지므로 AI 형식을 유지하자는 입장.

그 밖의 미결:

| # | 항목 | 내용 |
|---|---|---|
| C4 | 응답 세부 구조 | `findings[]` 내부는 팀원 PoC 결과에 종속. **최상위 필드까지만 고정하고 내부는 열어둔다** |
| C5 | `callback_url` | 명세는 콜백 방식(B3)인데 아직 미구현. 폴링만으로 먼저 붙일지 백엔드와 확인 |

---

## 4. 엔진 이식 백로그

팀원 브랜치에서 가져올 것들. 파트마다 난이도가 다르다.

| 파트 | 출처 | 형태 | 이식 난이도 |
|---|---|---|---|
| P02 코드 분석 | `feat/code_Q&A` : `cognition/`, `judgment/` | **Python** | 낮음 — 거의 그대로 import |
| P03 문답 | `feat/code_Q&A` : `shared/p03-engine.js` (495줄) | **JavaScript** | 높음 — 7단계 상태기계 포팅 |
| 채점·보고서 | `feat/code_Q&A` : `reference/p03-runner.js` | **JavaScript** | 높음 |
| P01 교안 분석 | `feat/pdf_analysis` : `scripts/java_curriculum_nvidia_pipeline.py` + `docs/lab/p01-runner.js` | 혼합 | 중간 — **PDF 추출부는 교체 필수**(브라우저 pdf.js → 서버 라이브러리, 결과 불일치) |
| LLM 호출 | `feat/code_Q&A` : `worker/nvidia-proxy.js` | JavaScript | 낮음 — 서버에서는 프록시 자체가 불필요. `_legacy/pipeline/feedback/nvidia_client.py`·`nvidia_key_pool.py`가 서버용 원본이라 재활용 가능 |

**이식 원칙**

1. 프롬프트·파라미터는 `prompt_manifest.json`에서 가져온다. 코드에 문자열로 박지 않는다
2. 제어 흐름만 Python 순수 함수로 옮긴다
3. `app/engines/` 밖으로 새어나가지 않게 한다
4. 팀원 브랜치를 직접 수정하지 않는다

### 이식 시 잘라내야 할 것 — PoC 인프라 의존

PoC는 브라우저에서 단독 실행되므로 서버에는 필요 없는 인프라가 붙어 있다. **로직만 뽑고 아래는 전부 잘라낸다.**

| PoC 의존 | 이유 | 서버에서는 |
|---|---|---|
| **Supabase** (`shared/db.js`, D213 이후 팀 공용 프로젝트) | 랩이 결과를 스스로 저장해야 했다 | **잘라낸다.** DB 단일 소유자는 Spring. FastAPI는 저장하지 않고 결과를 반환값으로 준다 |
| Cloudflare Worker LLM 프록시 (`worker/nvidia-proxy.js`) | 브라우저에서 NVIDIA를 직접 부르면 CORS·키 노출 | 서버가 직접 호출. 레이트리밋·키로테이션 로직만 Python으로 이관 |
| IndexedDB / `sessionStorage` (`shared/session-state.js`) | 새로고침 복원용 로컬 상태 | job 저장소·세션 상태로 대체 |
| pdf.js (`docs/lab/pdfjs-loader.js`) | 브라우저 PDF 파싱 | 서버 PDF 라이브러리로 교체. **결과가 동일하지 않다** |
| UI·타이머 (`createCountdownController` 등) | 학생 화면용 | 잘라낸다. 시간 제한은 Spring이 관리 |

**Supabase 관련 보안 주의 (2026-07-22, PoC `e43d58c`/`c6a4b32`)**

`feat/code_Q&A`가 팀 공용 Supabase 프로젝트를 쓰도록 바뀌었다. 그 프로젝트는 open signup(`disable_signup=false`, 도메인 제한 없음) + RLS read-all이라 다른 랩 사용자와 테이블을 공유한다. 보안 리뷰에서 cross-tenant 위험이 지적됐고, **팀원이 "랩에서는 감수한다"고 명시적으로 수용했다.**

> 이 수용은 **브라우저 PoC 한정 결정이지 제품 결정이 아니다.** 서버 경로로 새어나가면 안 된다. 이식 과정에서 Supabase 클라이언트·테이블 스키마·인증 흐름을 따라 옮기지 않는다. 애초에 FastAPI는 저장하지 않으므로 옮길 것 자체가 없어야 정상이다 — Supabase 호출이 필요해 보이면 설계가 잘못된 것이니 멈추고 재검토한다.. 필요하면 요청한다

**`_legacy/pipeline/feedback/`의 처분**

| 파일 | 처분 | 이유 |
|---|---|---|
| `nvidia_client.py`, `nvidia_key_pool.py` | 살린다 | 서버용으로 맞는 형태. Worker 프록시가 이것의 브라우저 재구현 |
| `interview_rubric.py`, `reflection_*` | 살린다 | 순수 로직, JS 쪽에도 대응물 존재 |
| `turn_engine.py`, `generate_questions.py`, `llm_interview_grader.py` | 버린다 | 상위 레포에서 이미 제거됨. `p03-engine.js`가 최신 |

---

## 5. 확정된 설계 결정

| # | 결정 | 근거 |
|---|---|---|
| D1 | 층은 `api/` `schemas/` `engines/` 셋만 | 층마다 존재 이유를 한 문장으로 못 대면 만들지 않는다. `services/`는 라우터가 60줄 넘으면 그때 |
| D2 | 엔진은 FastAPI를 모른다 (`dict` in/out) | 팀원이 FastAPI 몰라도 기여 가능. CLI 단독 실행으로 디버깅 가능 |
| D3 | 스텁이 1급 시민 (`engine_mode`) | 엔진 없이도 계약이 살아 있어야 백엔드가 대기하지 않는다 |
| D4 | JS 엔진은 Python으로 포팅. Node 안 띄운다 | JS는 브라우저 제약의 산물이지 설계 선택이 아니다. 서버엔 그 제약이 없다 |
| D5 | 이 브랜치에서 PoC를 만들지 않는다 | 역할 분담. 검증은 Swagger·Postman으로만 |
| D6 | 기존 구현은 `_legacy/`로 물리되 삭제하지 않는다 | 모듈화 참고용. `.gitignore` 대상이라 커밋 오염 없음 |
| D7 | 워커 1개 전제 | 인메모리 job 저장소. 스케일 필요 시 Redis/DB로 이전 |

---

## 6. 브랜치·커밋

**브랜치 전략(팀 합의)**: `feature/*` → 동작·테스트 완료 후 `main` → `main` 기준 `develop` 생성 → 이후 `develop`에서 수정·테스트 후 `main` 병합.

현재 `feature/fastapi-migration`이 `origin/feature/fastapi-migration`보다 앞서 있다. `develop`은 원격에 존재한다.

**커밋 규칙** (`../rule/개발/이슈 O/Git 커밋 & PR 가이드.docx`)

```
type: short description (#issue)
```

`feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수. 브랜치 네이밍은 `feature/기능명`·`fix/버그명`·`hotfix/긴급수정명`.

**단계마다 1커밋**을 권장한다. 9단계 = 9커밋이면 이력이 학습 순서 그대로 남는다.
