# AI 파트 작업 계획

> 갱신: 2026-07-29 · 작업 브랜치 `feature/engine-transplant` (`develop`에서 분기)
> **이 문서는 실행용이다.** 무엇을 어떤 순서로, 어떤 방법으로 할지만 적는다.
> 구조·계약의 설명은 `README.md`(팀원용). 미결 논의는 `../output_docs/미결_논의사항.md`.

---

## 현재 위치

| | |
|---|---|
| 엔드포인트 | 9/9 동작 (전부 스텁) |
| 테스트 | 36 passed |
| 다음 | **T1~T5 (계약 반영).** 백엔드 회신을 기다리지 않고 진행한다 |
| 막힌 것 | **T3b의 `featureCode` 값 확정뿐** — `ai_usage`는 이미 존재하는 테이블이라 CHECK 제약이 실제로 막는다 (Team-IZ/Backend#31) |

### 완료된 것

빈 FastAPI 골격부터 다시 쌓아 **엔드포인트 9개 + 인증 + 에러 형식 + camelCase 직렬화 + Swagger + `openapi.json`**을 만들었다. 엔진이 하나도 없어도 백엔드가 붙일 수 있는 상태다.

- `main.py`·`config.py`·`api/deps.py`·`api/errors.py` — 앱 조립, 설정, 인증(`X-Internal-Key`), 예외 핸들러
- `schemas/` — `common.py`(camelCase 기반)·`analysis.py`·`session.py`·`grading.py`
- `api/` — `health.py`·`analyses.py`(2)·`sessions.py`(4)·`gradings.py`(2)
- `engines/` — Protocol + 스텁 + 팩토리. `engine_mode` 설정으로 교체
- `jobs.py`·`sessions.py`·`gradings.py` — 인메모리 저장소 + 수명주기·멱등
- 백엔드 계약 C1~C6 합의(2026-07-22), 로컬 통신 자체검증 통과, cloudflared 터널 준비

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있어 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋 안 됨).

---

## 할 일

### T0 — 백엔드 회신 받기 (T3b만 막는다)

**이슈**: `Team-IZ/Backend#31` · 원본 질문지 `../qna/2026-07-29/backend-schema-questions.md`

백엔드는 **AI 연동부를 아직 구현하지 않았다.** 그래서 질문지는 "이미 만들었나?"가 아니라 **"이 모양으로 만들어 달라"**는 스펙 요청 형태로 써 뒀다. 제안 DDL을 그대로 붙여 놓아서 백엔드가 판단만 하면 된다.

> **회신 대기가 T1~T5를 막지 않는다.** 처음에는 "백엔드가 이미 5축 테이블을 만들었을 수 있다"는 전제로 전면 대기를 걸었으나, 코드 확인 결과 **AI 연동 도메인이 통째로 없다.** 아직 없는 테이블에 대한 항목은 우리가 먼저 확정하고 `openapi.json`을 주는 쪽이 순서상 맞다 — 백엔드는 그 스펙을 보고 만들면 된다.
>
> **진짜로 막히는 건 `ai_usage` 관련 둘(Q7-1·Q7-3)뿐이다.** 그 테이블은 이미 존재하므로 CHECK 제약과 UNIQUE 제약이 실제로 INSERT를 막는다. 나머지 8건은 제안대로 진행한다.

**급한 것 넷**

| # | 내용 | 막는 것 |
|---|---|---|
| **Q0-1** | AI 연동을 새 bounded context로 열 것인가 | 백엔드 쪽 착수 구조. 아래 §백엔드 현황 |
| **Q4-1** | `decision_point` 1:N 질문 — `dp_question` 신설 가능한가 | 사전 생성 질문·힌트(L1~L4 × 힌트 2)를 실을 자리. T2 |
| **Q7-1** | `ai_usage.feature_code` CHECK에 `CODE_ANALYSIS`·`QUESTION_GENERATION` 추가 가능한가 | 현재 허용값 4개에 **코드 분석·질문 생성이 없다.** T3b |
| **Q7-3** | `idempotency_key`에 AI가 호출마다 발급한 uuid4를 쓸 것인가 | UNIQUE 제약. 한 요청에 LLM 호출이 6번이라 헤더 키를 그대로 쓰면 충돌. DDL 변경은 없다. T3b |

나머지 7개(Q2-1 분석문서 저장 구조 · Q3-1 요구사항 테이블 · Q5-1 턴 점수 컬럼 · Q6-1 교안 테이블 · Q7-2 채점의 feature_code · Q7-4 failure_code 목록 · Q9-1 채점 결과 테이블)는 🟡.

### 백엔드 현황 (2026-07-29, 코드 분석 문서 기준)

> ⚠️ **이건 스냅샷이지 현재 상태가 아니다.** 근거는 코드 분석 문서 한 장이고 `develop` 기준으로 보인다. 백엔드는 **진행 중인 feature 브랜치가 따로 여러 개** 있고, 팀원 본인이 **명세서·구조 일부가 낡았다**고 말했다. 아래 판단(특히 "AI 연동 도메인이 없다")을 확정 사실로 쓰지 말고, 중요한 결정 전에는 최신 브랜치를 다시 확인하거나 팀원에게 물어라.

**AI 연동 도메인은 아직 없다(스냅샷 기준).** bounded context 6개가 전부 사용자·조직·반·기수 관리다.

```
auth          로그인·JWT·리프레시 토큰·계정 활성화·초대 수락
member        매니저 초대·교육생 CSV 등록·SMTP 발송·멤버 조회
classroom     반 생성·교육생 배정·매니저 배정
cohort        기수 생명주기
organization  기관 관리·정책·통계
operations    AI 사용량·비용·스토리지·운영 설정      ← ai_usage 여기
```

전역: `JwtFilter` · `CurrentUserResolver` · `GlobalExceptionHandler` + `ErrorResponse` · `ApiPathConfig` · `SwaggerConfig`

**우리 계획에 영향 주는 것 셋**

1. **`ai_usage`는 이미 있다.** `operations/domain/AiUsage.java`·`AiModel.java`, `infrastructure/AiUsageRepository.java`·`OrgAiCostTotal.java`. 조회는 구현돼 있고 **쓰기 경로만 없다.** `AiModel`이 있으므로 `modelCode → model_id` 조회가 실제로 가능하고, 단가·비용을 Spring이 계산하자는 제안도 `OrgAiCostTotal`이 이미 비용을 집계하고 있어 자연스럽게 맞는다
2. **`analysis_job`·`decision_point`·`assessment_session`·`session_turn`·보고서 도메인이 통째로 없다.** 새 컨텍스트를 여는 작업이라 백엔드 착수 비용이 작지 않다 — 우리 요청을 최소로 유지하고 제안 DDL을 그대로 붙여준 판단이 맞았다
3. **JDBC 직접 사용(JPA 자동 DDL 아님).** 테이블마다 DDL과 `Jdbc*Repository`를 손으로 써야 한다. 컬럼을 하나 늘리는 것도 공짜가 아니므로 **스키마 요청을 늘리지 않는다**

`GlobalExceptionHandler` + `ErrorResponse`가 이미 있는 것도 확인됐다. 우리 에러 계약(`{error, message, retryable}` 평탄 구조)이 그 DTO로 그대로 역직렬화되도록 맞춰둔 것이 유효하다.

회신이 오면 그 파일에 기록하고, 확정분을 아래 §계약 기준값으로 옮긴 뒤 T1을 시작한다.
**회신 전에 스키마를 고치지 않는다** — 두 번 고치게 된다.

---

### T1 — 채점 → 보고서 전환 (`/gradings` → `/reports`)

**대상**: `schemas/grading.py` → `schemas/report.py`, `app/gradings.py` → `app/reports.py`, `api/gradings.py` → `api/reports.py`, `main.py`, `tests/test_gradings.py`

**왜**: 5축 후채점의 근거가 사라졌다. 점수는 문답 도중 턴마다 확정되므로 세션 후 남는 비동기 작업은 보고서 생성뿐이다.

**방법**

1. 파일 3개를 `report`/`reports` 이름으로 옮긴다. 비동기 job 패턴은 그대로 재사용한다
2. `AxisCode`를 4축으로 교체 — `L1_CODE_DESCRIPTION`·`L2_DESIGN_LOGIC`·`L3_COUNTEREXAMPLE`·`L4_ALTERNATIVE`
3. `AxisScore.score`를 `ge=0, le=5`로 (현재 `ge=1`). 총점 범위 주석도 0~25로
4. **기존 파일의 결함 3개를 여기서 같이 고친다**
   - `ALTERNATIVE_COMPARISION` → 오타. 4축 교체로 자연 소멸
   - `COMPELETED` → `COMPLETED` (`GradingJobStatus.status`)
   - `AxisEvidence` 클래스가 두 번 정의돼 있다(뒤가 앞을 덮음). 하나만 남긴다
5. 응답에 필드 추가

```python
report_markdown: str
curriculum_refs: list[dict]   # [{teachId, unitId, sourcePages}] 부족 파트 → 교안 위치
retest_targets: list[str]     # [dpId]
summary: dict                 # 문제×레벨 점수 매트릭스
```

**DoD**: `POST /api/v0/reports` 202 → `GET /api/v0/reports/{jobId}`로 4축 점수와 보고서가 나온다. 테스트 통과.

---

### T2 — 분석 요청·응답 확장

**대상**: `schemas/analysis.py`, `engines/stub.py`

**방법** — 요청에 추가

```python
requirements: list[dict] = []      # [{id, text}] 구현 P/F 체크리스트
teaches: list[dict] = []           # [{id, label, unitId, sourcePages}] 3개
curriculum_id: str | None = None
question_budget: int = 3           # 의미 변경: "뽑을 DP 후보 수"
```

응답에 추가

```python
analysis_documents: list[dict]     # [{kind, content}]  ← 처음부터 배열로 둔다
requirement_results: list[dict]    # [{id, status: PASS|FAIL, evidence}]
# findings[] 각 항목에
#   prepared_questions: [{depthLevel, questionText, hints: [{level, text}] , flagged}]
```

**`analysis_documents`를 배열로 두는 이유**: 현재 PoC는 문서 1개지만 4종 고도화가 예고돼 있다. 나중에 단수→복수로 바꾸면 계약이 깨진다.

`stub.py`도 같이 고쳐 새 필드가 실제로 나오게 한다(배선 확인용).

**DoD**: Swagger에 새 필드가 보이고, 스텁 응답에 값이 채워진다.

---

### T3 — 세션 턴에 점수 필드 추가

**대상**: `schemas/session.py`, `app/sessions.py`

**방법**

```python
# Question 에
hint_text: str | None = None
retry_no: int = 0                  # 0=원질문, 1~2=힌트 재질의

# TranscriptTurn 에 (Spring이 영속화·restore로 되돌려야 하므로 필수)
raw_score: int | None = None       # LLM 원점수 0~5
score: int | None = None           # min(raw_score, hintCap) 기록 점수
hints_used: int = 0                # 0~2
retry_no: int = 0
hint_text: str | None = None
autonomy: str | None = None        # SELF | SELF_MAINTAINED | PARTIAL

# CodeContext 에
line_end: int | None = None
```

**`raw_score`와 `score`를 둘 다 두는 이유**: 힌트가 점수 상한을 깎는다. 캡 적용 결과만 남기면 상한 정책을 바꿨을 때 재계산이 불가능하고 감사도 안 된다.

`app/sessions.py`의 `_Turn` dataclass와 `_to_view()`도 같이 고친다.

**DoD**: `/answers` 응답에 방금 턴 점수가 실려 나오고, `restore`가 그 값을 되돌려 받는다.

---

### T3b — `aiUsage` 스키마 확정 (지금은 `dict` 열림)

**대상**: `schemas/common.py`(모델 추가), `schemas/analysis.py`·`session.py`·`report.py`(타입 교체), 엔진 호출부

**왜**: 모든 응답에 `ai_usage: list[dict[str, Any]]` 자리가 이미 있지만 **모양이 안 정해져 있다.** DB `ai_usage` 테이블은 기관별 호출·토큰·비용 원장이고 NOT NULL 컬럼이 많아서, AI가 안 주면 Spring이 행을 만들 수 없다.

**방법** — `schemas/common.py`에 공용 모델을 하나 만들고 세 응답이 공유한다.

```python
class AiUsage(BaseSchema):
    idempotency_key: str          # uuid4. 호출마다 새로 발급. 재시도 시에는 재사용
    feature_code: str             # 아래 매핑표
    model_code: str               # Spring이 ai_model 조회해 model_id 확보
    input_token_count: int = Field(ge=0)
    output_token_count: int = Field(ge=0)
    cached_token_count: int = Field(default=0, ge=0)
    status: Literal["SUCCEEDED", "FAILED", "PARTIAL"]
    failure_code: str | None = None   # FAILED·PARTIAL이면 필수
    latency_ms: int = Field(ge=0)
    occurred_at: datetime
```

**필드 이름을 DB 컬럼명과 1:1로 맞춘다.** 처음에 `callId`라는 이름을 새로 지었다가 백엔드가 "정의서에 없는 컬럼"으로 오해했다. 새 어휘를 만들 이유가 없다.

**DB CHECK 제약 둘을 스키마가 강제해야 한다.** 어기면 Spring INSERT가 실패한다.

```python
@model_validator(mode="after")
def _check_db_constraints(self):
    # CHECK (cached_token_count >= 0 AND cached_token_count <= input_token_count)
    if self.cached_token_count > self.input_token_count:
        raise ValueError("cachedTokenCount는 inputTokenCount를 넘을 수 없습니다")
    # CHECK ((status='SUCCEEDED' AND failure_code IS NULL)
    #     OR (status IN ('FAILED','PARTIAL') AND failure_code IS NOT NULL))
    if (self.status == "SUCCEEDED") != (self.failure_code is None):
        raise ValueError("status와 failureCode가 짝이 맞지 않습니다")
    return self
```

**LLM 호출 1건 = 배열 원소 1개.** 코드 분석 응답이면 6개가 들어간다.

**단가·비용은 넣지 않는다.** AI가 모델 단가표를 들고 있으면 단가가 바뀔 때마다 재배포해야 한다. `ai_model` 테이블을 가진 Spring이 토큰 수에 곱하는 쪽이 맞다.

**실패한 호출도 기록한다.** 실패해도 토큰·비용이 발생하고 재시도 통계에 필요하다. `status != SUCCEEDED`면 `failure_code`를 반드시 채운다(DB CHECK가 강제한다).

**DoD**: 스텁이 실제 값이 든 `aiUsage`를 돌려주고, 엔진이 붙으면 실제 호출 기록이 채워진다.

---

### T4 — 교안 분석 엔드포인트 신설

**대상**: `schemas/curriculum.py`(신규), `api/curricula.py`(신규), `app/curricula.py`(신규), `main.py`

**방법**: `jobs.py`·`api/analyses.py`의 비동기 job 패턴을 그대로 복사한다. 새 개념이 없다.

```
POST /api/v0/curricula          교안 PDF(multipart) → 202 {jobId, status}
GET  /api/v0/curricula/{jobId}  → {teaches: [{id, label, unitId, sourcePages}]}
```

교안 분석은 LLM을 무겁게 쓴다(스테이지 5개 + 재작성 루프, 교안 1개에 1~2분 이상). **수업 중이 아니라 LMS 업로드 시점에 도는 것**이 전제다.

**DoD**: 엔드포인트 11개, 테스트 통과.

---

### T5 — openapi.json 재생성 → 백엔드 전달

```bash
./.venv/Scripts/python.exe -c "from app.main import app; import json,io; io.open('openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(),ensure_ascii=False,indent=2))"
```

커밋하고 백엔드·프론트에 전달한다. **T1~T4를 다 끝내고 한 번에 한다** — 중간에 여러 번 주면 백엔드가 헛작업한다.

---

### T6 — P02 규칙부 이식

**출처**: `../ai_poc/poc_full` 의 `cognition/`·`judgment/`·`feedback/` (Python, vendored)
**난이도**: 낮음 — 순수 Python stdlib이라 거의 그대로 import된다

**방법**

1. `app/engines/analysis/` 아래로 옮긴다
2. `webtool_driver.py`가 Pyodide용 진입점이니 참고만 하고 서버용 호출부를 새로 쓴다
3. `AnalysisEngine` Protocol에 맞춰 `analyze(request, zip_bytes) -> dict` 하나로 감싼다

**함정**: CPU 작업이다. `async def` 안에서 동기로 돌리면 이벤트 루프가 막혀 **문답 중인 학생까지 굳는다.** `def`(threadpool)로 두거나 `run_in_executor`로 뺀다.

---

### T7 — P04 LLM 스테이지 이식

**출처**: `../ai_poc/poc_full/app/` — 전체 1,175줄

| 파일 | 줄 | 역할 |
|---|---|---|
| `prompt_manifest.json` | — | p04-1~6 프롬프트·파라미터. **계약이다. 문자열을 코드에 박지 않는다** |
| `scoring-config.js` | 177 | 축×값 루브릭 + 임계값 + 힌트 상한. 선언적이라 그대로 옮김 |
| `poc-engine.js` | 385 | 3문제 × 4레벨 루프 + 레벨별 즉시 채점 |
| `hint-ladder.js` | 99 | 질문+힌트 2단을 한 번에 생성해 동결 |
| `question-guard.js` | 56 | 질문·힌트에 선택지가 섞이는 것 차단 → 재생성 |
| `code-fragment.js` | 90 | `{file, lines}`를 실제 파일과 대조해 파편 추출 |
| `requirements.js` | 36 | 요구사항 P/F 판정 |
| `llm-stage.js` | 87 | 매니페스트 스테이지 1개 호출 공용 경로 |

**스테이지 구성** (전부 LLM)

```
p04-1  코드 분석 문서        입력: teaches + findings + code
p04-2  요구사항 P/F 판정
p04-3  문제 3개 선정
p04-4  L1~L4 질문 + 고정 힌트 생성   문제당 1콜
p04-5  답변 채점 (레벨 1개, 0~5점)   턴당 1콜
p04-6  보고서
```

**LLM 클라이언트**: `_legacy/pipeline/feedback/nvidia_client.py`·`nvidia_key_pool.py`가 서버용 원본이라 재활용한다. Worker 프록시(`worker/nvidia-proxy.js`)는 브라우저 제약의 산물이므로 옮기지 않는다.

**레이트리밋**: 무료 티어 분당 40회. PoC의 `shared/traffic-rate.js`가 같은 상수를 들고 있다. **이건 잘라낼 것이 아니라 서버로 옮겨야 할 것이다.** 짧은 초과는 내부 큐로 흡수하고, 한계를 넘으면 `RATE_LIMITED` + `retryAfterSec`로 돌려준다.

**잘라낼 것**: Supabase 저장(`shared/db.js`), Worker 프록시, IndexedDB·sessionStorage, UI·타이머, 브라우저 pdf.js.

> Supabase 관련 보안 주의: 팀 공용 프로젝트가 open signup + RLS read-all이라 cross-tenant 위험이 있고, 팀원이 **브라우저 PoC 한정으로** 수용했다. 제품 결정이 아니다. 이식 과정에서 Supabase 클라이언트·스키마·인증 흐름을 따라 옮기지 않는다. 애초에 FastAPI는 저장하지 않으므로 옮길 것이 없어야 정상이고, Supabase 호출이 필요해 보이면 설계가 틀린 것이니 멈추고 재검토한다.

---

### T8 — 세션 무상태 전환 (결정 대기)

백엔드가 매 턴 저장한다면 FastAPI가 세션을 또 들고 있을 이유가 약하다. 전환하면 `app/sessions.py` 저장소와 `POST /sessions/{id}/restore`가 통째로 사라진다(모든 요청이 곧 restore가 되므로).

대가는 payload다. `/answers` 요청에 `transcript` + `findings` + 분석 문서를 매번 실어야 해서 후반 턴이 약 32KB로 커진다. 서비스 간 내부 통신이라 무시할 수준이지만 **계약이 바뀌므로 백엔드 합의가 필요하다.**

`../output_docs/미결_논의사항.md` §2에 올려뒀다. 합의 전에는 착수하지 않는다.

---

## 계약 기준값

구현할 때 이 값을 그대로 쓴다.

### 공통

```
경로 prefix   /api/v0
필드 표기     camelCase (내부는 snake_case, 직렬화만 변환)
              단 findings[] 내부는 DB 컬럼명을 그대로 쓴다
에러          {error, message, retryable}  평탄 구조. timestamp·path 안 씀
헤더 3종      X-Internal-Key(인증, health 면제) · Idempotency-Key(submissionId:attemptNo) · X-Trace-Id
analysisId    Spring이 발급. AI는 만들지도 받지도 않는다
```

### DB CHECK 제약 (테이블정의서 v06, 06_MEAS)

이 값을 어기면 Spring INSERT가 깨진다.

```
analysis_job.status        QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED
decision_point.status      CANDIDATE, READY, USED, SKIPPED, INVALID
assessment_session.status  READY, IN_PROGRESS, PAUSED, TIMEOUT, COMPLETED, ABANDONED, FAILED
session_turn.state         PENDING, ANSWERED, SKIPPED, SAVED
submission.method          GITHUB_URL, ZIP_WITH_GITLOG
*_scope_code               TOTAL, OWN_COMMIT
```

**AI만 아는 NOT NULL 값** — 응답에 없으면 Spring이 행을 만들 수 없다.

| 테이블 | 컬럼 |
|---|---|
| `code_snapshot` | `content_hash`, `file_count`, `byte_count` |
| `commit_attribution` | `commit_hash`, `authored_at`, `changed_line_count`, `contribution_ratio` |
| `file_attribution` | `path`, `attribution_type`, `commit_count`, `changed_line_count`, `changed_function_count`, `confidence` |
| `decision_point` | `source_path`, `line_start`, `line_end`, `evidence_hash`, `priority`, `extractor_version` |

### 채점 (P04, `scoring-config.js` 실측)

```
축       L1 코드기술 · L2 설계논리 · L3 반례·한계 · L4 대안     4축, 각 0~5점
통과선   3점
힌트     레벨당 최대 2회. 점수 상한 {0회: 5, 1회: 4, 2회: 3}
기록점수 min(LLM 원점수, 상한)
자력도   0회=SELF / 1회=SELF_MAINTAINED / 2회=PARTIAL
문제 수  제출당 3개
가중치   축별 균등 1.0 (미측정 — 완주 세션 30건 후 재보정)
실패 시  그 문제 종료, 다음 문제의 L1로
```

**코드 파편은 연속 범위 하나다** — `lines: [start, end]` 2원소(`code-fragment.js`). `line_start`/`line_end`로 그대로 매핑되고 스키마 변경이 없다.

### `ai_usage` 매핑 (DB 테이블정의서 기준)

AI가 채우는 값과 Spring이 채우는 값이 갈린다. **경계를 넘지 않는다.**

| AI가 준다 | Spring이 채운다 |
|---|---|
| `idempotencyKey` · `featureCode` · `modelCode` | `usage_id` · `org_id` · `actor_user_id` |
| `inputTokenCount` · `outputTokenCount` · `cachedTokenCount` | `source_type` · `source_id` · `request_id` · `trace_id` |
| `status` · `failureCode` | `input_unit_price` · `output_unit_price` · `currency_code` |
| `latencyMs` · `occurredAt` | `estimated_cost` · `actual_cost` · `created_at` |

**단계별 `featureCode`** — 현재 DB CHECK 허용값은 `GRADING`·`SESSION_DIALOG`·`SUMMARY_DRAFT`·`CURRICULUM_ANALYSIS` 넷뿐이라 **코드 분석·질문 생성에 쓸 값이 없다**(Q7-1로 추가 요청).

| AI 단계 | 제안 값 | 상태 |
|---|---|---|
| 코드 분석 문서 (p04-1) | `CODE_ANALYSIS` | 추가 필요 |
| 요구사항 P/F (p04-2) | `CODE_ANALYSIS` | 추가 필요 |
| 문제 선정·질문 생성 (p04-3·4) | `QUESTION_GENERATION` | 추가 필요 |
| 답변 채점 (p04-5) | `GRADING` | 있음 (Q7-2 확인) |
| 보고서 (p04-6) | `SUMMARY_DRAFT` | 있음 |
| 교안 분석 (p01) | `CURRICULUM_ANALYSIS` | 있음 |

**`idempotency_key`는 UNIQUE다.** 헤더의 `Idempotency-Key`(`submissionId:attemptNo`)는 요청 단위인데 한 요청에 LLM 호출이 6번이라 그대로 쓰면 충돌한다. **호출마다 uuid4를 새로 발급**해 그걸 보낸다(Q7-3). 재시도할 때는 **이미 만든 키를 재사용**해야 중복 판별이 성립한다.

**`ai_model.model_code` 초기 목록에 `glm-5.2`가 이미 있다.** 별도 등록 없이 `modelCode`로 `model_id` 조회가 된다.

**`failure_code` 초안** — `RATE_LIMITED` · `TIMEOUT` · `INVALID_JSON` · `CONTEXT_OVERFLOW` · `UPSTREAM_ERROR` (Q7-4로 승인 요청)

### 규모

```
NVIDIA 무료 티어   분당 40회
코드 분석 배치      팀당 6콜 (분석문서 1 + 요구사항 1 + 문제선정 1 + 질문·힌트 3)
                   30팀 = 180콜 = 약 4.5분. 1시간 예산에 여유
문답 실시간         턴당 1콜(채점만). 동시 10~20명 = 13~27 RPM
결론               Redis 불필요. 워커 1개 유지
```

진짜 병목은 호출 수가 아니라 **컨텍스트 길이**다. 코드를 12,000자로 잘라 프롬프트에 넣으므로(`requirements.js:18`) 큰 레포는 잘린 코드로 요구사항이 판정된다.

---

## 설계 결정 (뒤집지 말 것)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 층은 `api/` `schemas/` `engines/` 셋만 | 층마다 존재 이유를 한 문장으로 못 대면 만들지 않는다. `services/`는 라우터가 60줄 넘으면 그때 |
| D2 | 엔진은 FastAPI를 모른다 (`dict` in/out) | 팀원이 FastAPI 몰라도 기여 가능. CLI 단독 실행으로 디버깅 가능 |
| D3 | 스텁이 1급 시민 (`engine_mode`) | 엔진 없이도 계약이 살아 있어야 백엔드가 대기하지 않는다. `real` 모드에 엔진이 없으면 **시끄럽게 실패**한다 — 조용한 스텁 폴백은 가짜 데이터를 운영까지 흘려보낸다 |
| D4 | JS 엔진은 Python으로 포팅. Node 안 띄운다 | JS는 브라우저 제약의 산물이지 설계 선택이 아니다 |
| D5 | 이 브랜치에서 PoC를 만들지 않는다 | 역할 분담. 검증은 Swagger·Postman으로만 |
| D6 | 기존 구현은 `_legacy/`로 물리되 삭제하지 않는다 | 모듈화 참고용. `.gitignore` 대상이라 커밋 오염 없음 |
| D7 | 워커 1개 전제 | 인메모리 저장소. 40 RPM이 병목이라 워커를 늘려도 처리량이 안 늘고, 오히려 레이트리밋 카운터가 갈라진다 |
| D8 | 턴 점수를 wire에 노출한다 | Spring이 매 턴 저장한다. 점수·힌트 사용횟수가 wire에 없으면 복구된 세션이 "힌트를 몇 번 썼는지" 모른 채 재개돼 `restore`가 성립하지 않는다 |
| D9 | `rawScore`·`score`를 둘 다 보낸다 | 힌트가 점수 상한을 깎는다. 캡 적용 결과만 남기면 상한 정책 변경 시 재계산 불가, 감사도 불가 |
| D10 | 재시험·문제배정 판정은 Spring이 한다 | AI는 점수와 `retestTargets`만 낸다. 커트라인·배정은 조직 정책이라 DB 주인이 갖는다 |
| D11 | 힌트는 질문과 함께 미리 만들어 동결한다 | 답변을 보고 만들면 학생마다 힌트가 달라져 "몇 번째 힌트에서 통과했는가"가 측정값이 못 된다. 이식할 때 이 성질을 깨지 않는다 |

---

## 함정

이전에 실제로 물렸거나 물릴 뻔한 것들.

| 함정 | 대응 |
|---|---|
| `pytest`가 `_legacy/tests/`를 수집해 깨진다 | `pytest.ini`에 `norecursedirs = _legacy .venv` 필수 |
| Swagger가 중첩 모델 `$ref`를 해석 못 한다 | `$defs` 인라인 펼치기로 해결됨. 새 중첩 모델을 넣을 때 `/docs`를 눈으로 확인 |
| DB CHECK에 없는 상태값을 보낸다 | 위 §계약 기준값의 목록만 쓴다. `ANALYZING`·`READY`는 다른 테이블 값이다 |
| 워크트리 경로에 백슬래시 | Bash에서 `..\ai_poc\x`는 이스케이프로 먹혀 엉뚱한 폴더가 생긴다. `/`를 쓴다 |
| develop 병합 경로가 갈린다 | 팀은 GitHub PR로, 로컬은 직접 push하면 갈라진다. 방식을 팀과 통일할 것 |
| 분석 CPU가 이벤트 루프를 막는다 | T6 참조. `def` 또는 `run_in_executor` |

---

## 브랜치·커밋

**전략**: `feature/*` → 동작·테스트 완료 후 `main` → `main` 기준 `develop` 생성 → 이후 `develop`에서 수정·테스트 후 `main` 병합. GitHub 기본 브랜치는 `develop`.

**커밋**: `type: short description (#issue)` — `feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수.

**T 하나당 1커밋**을 권장한다. 이력이 작업 순서 그대로 남는다.

**주의**: 이 저장소는 사용자가 직접 git 명령을 실행한다. 에이전트는 명령을 만들어 전달만 한다.
