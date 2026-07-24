# AI 파트 FastAPI 구축 계획

> 마지막 갱신: 2026-07-24
> 작업 브랜치: `feature/engine-transplant` (9단계 이식용, `develop`에서 분기)
> 이 문서가 **AI 파트 진행 상황의 단일 기준**이다. 새 세션은 여기부터 읽는다.
> 구조·계약의 설명은 `README.md`에 있다. 이 문서는 **무엇을 어떤 순서로 할지**만 다룬다.

---

## 현재 상태

**재구축 중. 빈 골격에서 시작한다.**

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있었다. 팀 역할이 "골격·백엔드 통신 담당"으로 정리되면서 PoC를 더 이상 이 브랜치에서 다루지 않기로 했고, 전량 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋되지 않음).

| 항목 | 상태 |
|---|---|
| 완료 단계 | **1~8** (앱 골격 · 설정·인증 · 분석 2 · 엔진 소켓 · job 수명주기 · 세션 4 · 채점 2) |
| 엔드포인트 | **9 / 9 완성** (health · analyses 2 · sessions 4 · gradings 2) |
| 테스트 | **36 passed** |
| 백엔드 계약 | C1~C6 확정(2026-07-22) — §3 |
| 다음 작업 | **Postman 컬렉션 작성(8단계 DoD) → 9단계 팀원 엔진 이식** |

살아 있는 엔드포인트:

```
GET  /api/health                          인증 면제
POST /api/v0/analyses                     JSON + multipart(ZIP) 양쪽. 멱등성 키 처리
GET  /api/v0/analyses/{job_id}            고정 결과 반환. 모르는 id는 404
POST /api/v0/sessions                     세션 시작 → 첫 질문(201). 동기
POST /api/v0/sessions/{id}/answers        답변 제출 → 다음 질문/종료. client_request_id 멱등
GET  /api/v0/sessions/{id}                세션 상태 조회. 모르는 id는 404
POST /api/v0/sessions/{id}/restore        유실 세션을 transcript로 재구성
POST /api/v0/gradings                     세션 transcript 5축 후채점 요청(202). 비동기 job
GET  /api/v0/gradings/{job_id}            채점 상태·5축 점수·근거 조회. 모르는 id는 404
```

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
| 3단계 완료 | `schemas/common.py`(camelCase 기반)·`schemas/analysis.py`·`api/errors.py`·`api/analyses.py`. JSON·multipart 양쪽 수용, 멱등성 키 동작 확인(같은 키 = 같은 `jobId`) |
| 4단계 완료 | `GET /analyses/{job_id}` + 응답 스키마. 404 `JOB_NOT_FOUND`, 타임스탬프 ISO 8601 UTC 확인 |

**2026-07-23 — 5·6단계 + 브랜치 개편**

| 한 일 | 결과 |
|---|---|
| 브랜치 개편 | 꼬였던 구 `develop`을 `develop-old`로 백업(로컬+원격 보존, 나중 삭제). 재구축 브랜치 `feature/fastapi-skeleton`을 정리해 **`develop`을 새 기준선**으로 삼음. 기본 브랜치는 GitHub에서 `develop` |
| 5단계 완료 | `app/engines/`(`base.py` Protocol · `stub.py` · `__init__.py` 팩토리)·`config.py` `engine_mode`·`api/analyses.py` 의존성 주입·`tests/test_engines.py`. `_stub_result` 인라인 제거, findings를 `decision_point` 컬럼명으로 교정. **21 passed** |
| 6단계 완료 | `app/jobs.py`(인메모리 저장소 + 수명주기)·`api/analyses.py` `BackgroundTasks` 배선·`tests/test_jobs.py`. 202 즉시 반환 후 QUEUED→RUNNING→SUCCEEDED/FAILED 전이, 폴링으로 관측. **24 passed** |
| develop 반영 | `feature/engine-socket`(5·6단계)을 `develop`에 fast-forward 병합·push. 7단계용 `feature/sessions` 분기 |
| Swagger 버그 수정 | `POST /analyses` 요청 스키마의 중첩 모델(`AnalysisSource`) `$ref`가 components에 없는 경로를 가리켜 Swagger가 해석 실패하던 것(3단계부터 잠복)을 `$defs` 인라인 펼치기로 해결 |
| 7단계 완료 | `schemas/session.py`(단수 — `analysis.py`와 짝)·`app/sessions.py`(저장소, `jobs.py`와 짝)·`api/sessions.py`(4개 엔드포인트)·`main.py` 등록·`tests/test_sessions.py`. 동기 리소스, `client_request_id` 멱등, 내부 상태(dataclass)/와이어 DTO(pydantic) 분리, restore로 transcript 재구성. **31 passed** |

| develop 재정합 | GitHub에서 feature/sessions PR #3가 세션 코드 올라가기 전 상태로 develop에 미리 머지돼(내용 없는 머지 커밋) 로컬 develop과 갈라짐. `git merge origin/develop`으로 흡수(로컬이 상위집합, 충돌 없음) 후 push. 이후 8단계용 `feature/gradings` 분기 |
| 8단계 완료 | `schemas/grading.py`·`app/gradings.py`(저장소·스텁, `jobs.py`와 형제)·`api/gradings.py`(2개)·`main.py` 등록·`tests/test_gradings.py`. 분석과 같은 비동기 job 패턴. 5축 채점 결과 계약(`axis_scores` 5개·총점·평균·재현성 `versions`) 확정. **엔드포인트 9/9 완성, 36 passed** |
| openapi 스냅샷·통신 테스트 | `openapi.json` 커밋(백엔드 Postman Import용). 로컬 통신 자체검증 통과(9개 전 경로, 인증 헤더 동작). 백엔드 터널 통신은 cloudflared로 준비(계획: `../output_docs/AI-Backend_통신테스트_계획_2026-07-24.md`), 팀원 일정 대기 |
| PoC 워크트리 최신화 | `ai_poc/qna`(`feat/code_Q&A` → 1277784)·`ai_poc/pdf`(`feat/pdf_analysis` → 9884447) 최신 참조로 이동. 9단계 이식 대비 P02(룰 finding 추출)·P03·교안분석 구조 분석. 논의는 `../output_docs/`(월요일 안건·전체 논의·통신 미해결) |

**파일명 규칙(기존 코드가 정한 것)**: `schemas/`는 **단수**(`analysis.py`·`session.py`·`grading.py`), `api/`·저장소 모듈은 **복수**(`analyses.py`·`sessions.py`·`gradings.py`·`jobs.py`).

**브랜치 운영 주의**: 팀이 GitHub PR로 develop에 머지하는 흐름과, 로컬에서 직접 develop push하는 흐름이 섞이면 이번처럼 갈라진다. develop 반영 방식을 팀과 통일할 것.

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

### 1단계 — 앱 골격 ✅

| | |
|---|---|
| 산출물 | `app/main.py`, `app/api/health.py`, `pytest.ini`, `tests/test_health.py` |
| 배우는 것 | `FastAPI()`, `APIRouter`, `include_router`, `TestClient` |
| DoD | `uvicorn app.main:app`으로 뜨고 `GET /api/health`가 200. `/docs`에 노출 |
| 주의 | `pytest.ini`에 `norecursedirs = _legacy .venv` 필수 — 없으면 `_legacy/tests/`를 수집해 깨진다 |

### 2단계 — 설정과 인증 ✅

| | |
|---|---|
| 산출물 | `app/config.py`, `app/api/deps.py`, `tests/test_auth.py` |
| 배우는 것 | `BaseSettings`, `Depends`, `Header`, 라우터 단위 의존성 |
| DoD | `X-Internal-Key` 없거나 틀리면 401. `/api/health`는 면제 |
| 결정 | 키 미설정(빈 값)이면 검증을 건너뛴다 — 로컬 개발 편의. 운영에서는 반드시 설정 |

### 3단계 — 분석 요청 스텁 ✅

| | |
|---|---|
| 산출물 | `app/schemas/common.py`, `app/schemas/analysis.py`, `app/api/analyses.py`, `tests/test_analyses.py` |
| 배우는 것 | pydantic 요청 모델, `status_code=202`, 422 검증, `Literal` enum |
| DoD | `POST /api/v0/analyses`가 202 + `{jobId, status:"QUEUED"}`. 필수 필드 누락 시 422 |
| 선결 | ✅ 해소 — §3의 C1~C6이 2026-07-22 확정됐다 |
| 주의 | `method=ZIP_WITH_GITLOG`는 multipart라 JSON과 요청 형태가 다르다. 한 오퍼레이션에서 Body와 Form을 섞을 수 없으므로 라우터가 Content-Type으로 분기한다 |
| 주의 | `Idempotency-Key` 헤더를 받아 기억하는 자리를 여기서 만든다(값은 `submissionId:attemptNo`). 같은 키 재요청은 처음 `jobId`를 202로 반환 |

### 4단계 — 분석 조회 스텁 ✅

| | |
|---|---|
| 산출물 | `app/schemas/analysis.py`(응답 모델 추가), `app/api/analyses.py` |
| 배우는 것 | 경로 파라미터, `response_model`, 404 처리 |
| DoD | `GET /api/v0/analyses/{jobId}`가 고정 결과 반환. 모르는 id는 404 |

### 5단계 — 엔진 소켓 ← 다음 (코드 미착수)

| | |
|---|---|
| 산출물 | `app/engines/__init__.py`·`base.py`·`stub.py`, `tests/test_engines.py`, `config.py`·`api/analyses.py` 수정 |
| 배우는 것 | `Protocol`, 의존성 주입으로 구현체 교체(`Depends`, `dependency_overrides`) |
| DoD | `engine_mode` 설정으로 스텁/실물이 갈린다. 라우터는 어느 쪽인지 모른다 |
| 원칙 | 엔진은 FastAPI·pydantic·HTTP를 모른다. `dict` in, `dict` out |

**설계 세부 (다음 세션에서 이대로 구현)**

1. **`base.py` — `AnalysisEngine` Protocol.** 메서드 하나: `analyze(request: dict, zip_bytes: bytes | None = None) -> dict`. Protocol이라 팀원 코드가 상속·import할 필요 없이 시그니처만 맞으면 된다. request는 `body.model_dump()`(snake_case 키), 반환은 `AnalysisResult` 스키마에 대응하는 snake_case dict.

2. **`stub.py` — `StubAnalysisEngine`.** 지금 `analyses.py`에 인라인된 `_stub_result()`를 이리로 이사. **동시에 `findings[]`를 `decision_point` 컬럼명으로 교정**한다(현재 `findingId` 등 임의 이름 → `dp_id`·`type`·`status`·`priority`·`focus_code`·`source_path`·`line_start`·`line_end`·`evidence_hash`·`extractor_version` + `references[{path,line_start,line_end,evidence_hash,reference_type}]`). `type`·`reference_type` 값 문자열은 카탈로그 미정(B-3)이라 잠정값(`CODE_RISK`/`PRIMARY`). 요청 값 일부를 반영(예: `applied_scope = request["extraction_scope"]`, `byte_count = len(zip_bytes)`)해 배선이 실제로 연결됐는지 드러나게 한다.

3. **`__init__.py` — `get_analysis_engine()`.** `engine_mode=="stub"`이면 `StubAnalysisEngine()`, `"real"`이면 `NotImplementedError`로 **시끄럽게 실패**(조용히 스텁 폴백 금지 — 가짜 데이터가 운영까지 흘러감). FastAPI 의존성으로 쓴다.

4. **`config.py`** — `engine_mode: Literal["stub","real"] = "stub"` 추가.

5. **`analyses.py`** — `_stub_result()` 삭제. `create_analysis`에 `engine: AnalysisEngine = Depends(get_analysis_engine)` 주입. `_create_job(body, engine, zip_bytes)`가 `engine.analyze(body.model_dump(), zip_bytes)` 호출 후 `AnalysisResult.model_validate(raw)`로 검증(엔진이 계약 어기면 여기서 터지게).

6. **`test_engines.py`** — 핵심은 `test_router_uses_injected_engine`: `app.dependency_overrides[get_analysis_engine] = FakeEngine`으로 갈아끼우고 응답에 FakeEngine 값이 나오는지 확인(`finally`에서 `clear()`). 통과 = 9단계에서 라우터 안 건드려도 엔진 교체됨. 그 외: 스텁 계약 모양, 요청 값 반영, finding이 decision_point 컬럼명 사용, real 모드 `NotImplementedError`.

**DoD 목표: 21 passed.**

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

### `findings[]`의 결정권 — 누가 무엇을 정하는가

`findings[]`는 DB `decision_point`(+ `dp_reference`) 테이블에 대응한다. **"명세가 없다"가 아니라 층마다 결정권자가 다르다.**

| 항목 | 결정권 | 상태 |
|---|---|---|
| 필드 이름·타입·필수 여부 | **DB 명세**(테이블정의서 v06) | ✅ 확정 |
| `status` 허용값 | DB CHECK | ✅ `CANDIDATE/READY/USED/SKIPPED/INVALID` |
| `type`·`referenceType` 값 문자열 | AI 초안 → 백엔드 승인 | ❓ 카탈로그 자체가 없음(질문지 B-3) |
| `priority` 산출 로직, 무엇을 finding으로 볼지 | **AI 팀원(엔진)** | 엔진 종속 |
| 엔진 출력 → DB 스키마 매핑 | **이 브랜치** | 9단계 |
| JSON 표기·구조 확정 | **이 브랜치** | ✅ |

**즉 스텁의 `findings[]`는 `decision_point` 컬럼 이름을 그대로 써야 한다.** 임의로 지은 이름(`findingId` 등)을 두면 백엔드가 그걸로 DTO를 만들고 나중에 전부 갈아엎게 된다.

```
dpId, type, status, priority, focusCode, sourcePath, lineStart, lineEnd,
evidenceHash, extractorVersion, references[{path, lineStart, lineEnd, evidenceHash, referenceType}]
```

**9단계에서 부딪힐 위험**: `line_start`·`line_end`가 NOT NULL인데, 구조적 finding(파일 간 관계 등)은 특정 줄에 대응하지 않는다. 옛 구현에도 "Tier-A finding은 line_start=null" 문제가 기록돼 있었다. **DB가 못 받는 값을 엔진이 낼 수 있다** — 그때 AI 팀원과 협의한다.

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

**2026-07-23 개편 반영**: 꼬였던 구 `develop`은 `develop-old`로 백업(로컬+원격, 나중 삭제). 재구축 골격이 정리된 새 `develop`이 기준선이고 GitHub 기본 브랜치도 `develop`이다. 단계별 `feature/*`를 develop에 fast-forward 병합하며 나아간다(5·6단계 = `feature/engine-socket` 병합 완료). 현재 작업 브랜치는 **`feature/sessions`**(7단계용).

**PR은 아직 내지 않는다.** 단계 학습이 목적이라 develop에 직접 병합 중. 팀 공유 시점에 정리해 올린다.

**커밋 규칙** (`../rule/개발/이슈 O/Git 커밋 & PR 가이드.docx`)

```
type: short description (#issue)
```

`feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수. 브랜치 네이밍은 `feature/기능명`·`fix/버그명`·`hotfix/긴급수정명`.

**단계마다 1커밋**을 권장한다. 9단계 = 9커밋이면 이력이 학습 순서 그대로 남는다.
