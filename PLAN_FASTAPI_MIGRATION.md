# AI 파트 FastAPI 구축 계획

> 마지막 갱신: 2026-07-22
> 작업 브랜치: `feature/fastapi-skeleton` (`origin` push 완료)
> 이 문서가 **AI 파트 진행 상황의 단일 기준**이다. 새 세션은 여기부터 읽는다.
> 구조·계약의 설명은 `README.md`에 있다. 이 문서는 **무엇을 어떤 순서로 할지**만 다룬다.

---

## 현재 상태

**재구축 중. 빈 골격에서 시작한다.**

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있었다. 팀 역할이 "골격·백엔드 통신 담당"으로 정리되면서 PoC를 더 이상 이 브랜치에서 다루지 않기로 했고, 전량 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋되지 않음).

| 항목 | 상태 |
|---|---|
| 완료 단계 | 1단계(앱 골격) · 2단계(설정·인증) |
| 엔드포인트 | 1 / 9 — `GET /api/health` |
| 테스트 | 2 passed |
| 백엔드 계약 | C1~C6 확정(2026-07-22) — §3 |
| 다음 작업 | **3단계 — `POST /api/v0/analyses` 스텁** |

### 작업 이력

**2026-07-22 — 환경 정리**

| 한 일 | 결과 |
|---|---|
| PC 이전 흔적 정리 | 노트북 경로(`C:\KT_aivle\big-project`) 참조를 현재 경로로 전면 치환. `.venv`가 옛 파이썬(3.12 @ `C:\Users\User`)을 가리켜 깨져 있던 것을 `C:\Python313`으로 재생성 |
| 브랜치 이름 정정 | 규칙에 어긋난 로컬 `feat/fastapi-migration` → `feature/*` 규칙에 맞게 정리. 이후 재구축용 **`feature/fastapi-skeleton`** 신설·push |
| 낡은 원격 참조 정리 | `origin/feature/fastapi-migration`·`origin/feature/verification-ui`는 GitHub에서 이미 삭제된 브랜치였다(`fetch --prune`). 구 구현은 **`origin/develop`에 병합돼 남아 있다** |
| PoC 워크트리 생성 | `../ai_poc/qna`·`../ai_poc/pdf`를 `AI/.git` 공유 워크트리로 생성(detached, 읽기 전용). 별도 clone을 쓰지 않는 이유와 사용법은 루트 `README.md` |

**2026-07-22 — 재구축 결정과 실행**

| 한 일 | 결과 |
|---|---|
| 역할 재정의 | 이 브랜치의 담당은 **골격·백엔드 통신**. PoC는 팀원 브랜치에서만 관리한다 |
| 기존 구현 후퇴 | `app/`·`tests/`·`trainee/`·`shared/`·`pipeline/`·`reference/` 전량을 `_legacy/`로 이동 후 추적 해제(81파일). 삭제가 아니라 **참고용 로컬 사본** |
| 문서 전면 재작성 | `README.md`(구조·계약), 이 문서(계획), 루트 `README.md`(워크스페이스·워크트리·작업 방식) |
| 1단계 완료 | `main.py`·`api/health.py`·`pytest.ini`·`tests/test_health.py`. `GET /api/health` 200 |
| 2단계 완료 | `config.py`·`api/deps.py`·`tests/test_auth.py`. `X-Internal-Key` 인증, production 빈 키 기동 거부 확인 |
| 백엔드 계약 합의 | C1~C6 확정(§3). 질문지는 `../qna/2026-07-22/backend-api-questions.md` |

**이 과정에서 드러난 사실 3개**

1. **P03 문답 로직은 이미 Python에서 JS로 옮겨갔다.** `_legacy/pipeline/feedback/`의 `turn_engine.py`·`generate_questions.py`·`llm_interview_grader.py`는 상위 레포에서 제거된 옛 스냅샷이고, 살아 있는 구현은 `shared/p03-engine.js`(495줄)다. §4 백로그 참고
2. **기존 AI 코드의 `analysis_job.status` 값이 틀렸다.** `ANALYZING`·`READY`는 각각 `measurement_attempt`·`assessment_session`의 값이었다. 올바른 값은 `QUEUED/RUNNING/SUCCEEDED/PARTIAL/FAILED` — §3
3. **PoC가 팀 공용 Supabase를 쓰기 시작했다**(`e43d58c`). cross-tenant 위험을 팀원이 랩 한정으로 수용. 서버로 옮기지 말 것 — §4

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
| DoD | `POST /api/v0/analyses`가 202 + `{jobId, status:"QUEUED"}`. 필수 필드 누락 시 422 |
| 선결 | ✅ 해소 — §3의 C1~C6이 2026-07-22 확정됐다 |
| 주의 | `method=ZIP_WITH_GITLOG`는 multipart라 JSON과 요청 형태가 다르다. 한 오퍼레이션에서 Body와 Form을 섞을 수 없으므로 라우터가 Content-Type으로 분기한다 |
| 주의 | `Idempotency-Key` 헤더를 받아 기억하는 자리를 여기서 만든다(값은 `submissionId:attemptNo`). 같은 키 재요청은 처음 `jobId`를 202로 반환 |

### 4단계 — 분석 조회 스텁

| | |
|---|---|
| 산출물 | `app/schemas/analysis.py`(응답 모델 추가), `app/api/analyses.py` |
| 배우는 것 | 경로 파라미터, `response_model`, 404 처리 |
| DoD | `GET /api/v0/analyses/{jobId}`가 고정 결과 반환. 모르는 id는 404 |

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

## 3. 확정된 백엔드 계약 (2026-07-22 합의)

**3단계를 막던 항목은 전부 해소됐다.** 전체 논의 기록은 `../qna/2026-07-22/backend-api-questions.md`.

| # | 항목 | 확정 내용 |
|---|---|---|
| C1 | 경로 prefix | **`/api/v0`** — 서비스 버전이 아니라 "개발 단계 API"라는 성숙도 표시. 계약이 안정되면 양쪽이 함께 v1으로 올린다 |
| C2 | 필드 표기 | **camelCase** (`jobId`, `snapshotId`) |
| C3 | 에러 형식 | **평탄 구조 `{error, message, retryable}`.** `timestamp`·`path`는 쓰지 않는다 |
| C4 | 멱등성 키 | Spring이 **`submissionId:attemptNo`**를 `Idempotency-Key` 헤더로 보낸다. FastAPI가 기억해 중복을 판별하고, 같은 키면 **처음 만든 `jobId`를 `202`로 그대로 반환**(재분석 없음) |
| C5 | 추적 ID | `X-Trace-Id` 헤더 |
| C6 | `analysisId` | Spring이 발급. **AI는 만들지도 받지도 않는다** — Spring이 `jobId`로 연결 |

### 요청 헤더 3종

| 헤더 | 값 | 비고 |
|---|---|---|
| `X-Internal-Key` | 공유 비밀 | 인증. `GET /api/health`만 면제 |
| `Idempotency-Key` | `submissionId:attemptNo` | 중복 요청 판별 |
| `X-Trace-Id` | 추적 ID | `analysis_job.trace_id`로 저장됨 |

### 에러 응답

```json
{
  "error": "INVALID_REQUEST",
  "message": "method=GITHUB_URL에는 source.repoUrl이 필요합니다",
  "retryable": false
}
```

`error`는 기계가 분기하는 코드 문자열(대문자 스네이크), `message`는 사람이 읽는 설명, `retryable`은 Spring의 재시도 판단용이다. Spring이 기존 `ErrorResponse` DTO로 그대로 역직렬화할 수 있는 모양이다.

### camelCase 구현

pydantic 스키마의 공통 부모에 `alias_generator`를 걸면 파이썬 코드는 snake_case 그대로 쓰고 직렬화만 camelCase가 된다. 스키마마다 손대지 않는다.

```python
class BaseSchema(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
```

### 나중으로 미룬 것 — 멱등성 키 저장소를 Redis로

**팀 결정: FastAPI에 Redis를 도입할 예정이다. 시기는 미정(나중).**

그때까지는 멱등성 키를 **인메모리 dict**로 들고 간다. 따라서 아래 두 경우에 중복 job이 생길 수 있고, **이를 감수하기로 했다**(분석이 두 번 돌아도 결과가 같아 비용 낭비 외의 문제는 없다).

| 상황 | 결과 |
|---|---|
| FastAPI 프로세스 재시작 | 기억 소실 → 같은 키에도 새 job 생성 |
| 워커 2개 이상 | 워커별로 기억이 달라 중복 방지가 깨짐 |

Redis가 들어오면 job 저장소도 함께 옮길 수 있다(§2 6단계의 "워커 1개" 제약과 D7이 같은 뿌리다). 도입 시점에 이 두 가지를 한 번에 처리한다.

### DB 정의서에서 확정된 값 (테이블정의서 v06, 06_MEAS)

**`analysis_job.status`는 DB CHECK 제약을 따른다.** 기존 AI 코드가 쓰던 `ANALYZING`·`READY`는 **다른 테이블의 값을 잘못 가져온 것**이었다(`ANALYZING`은 `measurement_attempt`, `READY`는 `assessment_session` 소속). 그대로 보내면 Spring INSERT가 CHECK 위반으로 실패한다.

```
analysis_job.status        QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED
decision_point.status      CANDIDATE, READY, USED, SKIPPED, INVALID
assessment_session.status  READY, IN_PROGRESS, PAUSED, TIMEOUT, COMPLETED, ABANDONED, FAILED
session_turn.state         PENDING, ANSWERED, SKIPPED, SAVED
submission.method          GITHUB_URL, ZIP_WITH_GITLOG          ← 기존 AI와 일치
*_scope_code               TOTAL, OWN_COMMIT                    ← 기존 AI와 일치
```

**AI만 아는 NOT NULL 값** — 응답에 없으면 Spring이 행을 만들 수 없다.

| 테이블 | 컬럼 |
|---|---|
| `code_snapshot` | `content_hash`, `file_count`, `byte_count` |
| `commit_attribution` | `commit_hash`, `authored_at`, `changed_line_count`, `contribution_ratio` |
| `file_attribution` | `path`, `attribution_type`, `commit_count`, `changed_line_count`, `changed_function_count`, `confidence` |
| `decision_point` | `source_path`, `line_start`, `line_end`, `evidence_hash`, `priority`, `extractor_version` |

### 백엔드 확인 대기 목록

전체 질문지는 **`../qna/2026-07-22/backend-api-questions.md`**. 답이 오면 그쪽에 기록하고 확정분만 이 절로 옮긴다.

**3단계를 막는 항목은 없다.** 남은 것은 스키마 자리만 잡아두면 되는 것들이다 — 타임스탬프 형식(A-5), `null` vs 필드 생략(A-6), `job_type` 허용값(A-4), `PARTIAL` 조건(C-1), 콜백 vs 폴링(C-2).

그 밖의 미결:

| # | 항목 | 내용 |
|---|---|---|
| C7 | 응답 세부 구조 | `findings[]` 내부는 팀원 PoC 결과에 종속. **최상위 필드까지만 고정하고 내부는 열어둔다** |
| C8 | `callback_url` | 명세는 콜백 방식(B3)인데 아직 미구현. 폴링만으로 먼저 붙일지 백엔드와 확인 |

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

현재 작업 브랜치는 **`feature/fastapi-skeleton`**(`origin` push 완료). GitHub의 브랜치는 `main`·`develop`·`feat/code_Q&A`·`feat/pdf_analysis`·`feature/fastapi-skeleton` 다섯이다.

**PR은 아직 내지 않는다.** `origin/develop`에 구 구현이 살아 있어서 지금 PR을 올리면 **대량 삭제 diff**로 보인다. "AI 골격을 재구축했고 PoC는 팀원 브랜치에서만 관리한다"는 배경이 팀에 공유된 뒤에 올린다.

**커밋 규칙** (`../rule/개발/이슈 O/Git 커밋 & PR 가이드.docx`)

```
type: short description (#issue)
```

`feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수. 브랜치 네이밍은 `feature/기능명`·`fix/버그명`·`hotfix/긴급수정명`.

**단계마다 1커밋**을 권장한다. 9단계 = 9커밋이면 이력이 학습 순서 그대로 남는다.
