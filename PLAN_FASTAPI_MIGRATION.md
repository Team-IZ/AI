# AI 파트 작업 계획

> 갱신: 2026-07-30 · 작업 브랜치 `feature/engine-transplant` (`develop`에서 분기)
> **이 문서는 실행용이다.** 무엇을 어떤 순서로, 어떤 방법으로 할지만 적는다.
> 구조·계약의 설명은 `README.md`(팀원용). 백엔드와의 현재 상태판은 이슈 `Team-IZ/Backend#31` 본문(사본 `../qna/2026-07-30/issue-body-v2.md`).

---

## 현재 위치

| | |
|---|---|
| 엔드포인트 | 9/9 동작 (전부 스텁) |
| 테스트 | 41 passed |
| 다음 | **T2 → T2b → T3 → T3b → T4 → T5.** 백엔드 회신을 기다리지 않고 진행한다 |
| 막힌 것 | **C-3(단가 분담) 하나뿐.** 그게 안 오면 `ai_usage` 쓰기 경로가 막히지만 나머지 스키마는 다 진행한다 |

### 완료된 것

빈 FastAPI 골격부터 다시 쌓아 **엔드포인트 9개 + 인증 + 에러 형식 + camelCase 직렬화 + Swagger + `openapi.json`**을 만들었다. 엔진이 하나도 없어도 백엔드가 붙일 수 있는 상태다.

- `main.py`·`config.py`·`api/deps.py`·`api/errors.py` — 앱 조립, 설정, 인증(`X-Internal-Key`), 예외 핸들러
- `schemas/` — `common.py`(camelCase 기반)·`analysis.py`·`session.py`·`report.py`
- `api/` — `health.py`·`analyses.py`(2)·`sessions.py`(4)·`reports.py`(2)
- `engines/` — Protocol + 스텁 + 팩토리. `engine_mode` 설정으로 교체
- `jobs.py`·`sessions.py`·`reports.py` — 인메모리 저장소 + 수명주기·멱등
- 백엔드 계약 C1~C6 합의(2026-07-22), 로컬 통신 자체검증 통과, cloudflared 터널 준비
- **T1 완료** — `/gradings` → `/reports` 전환. 파일 3쌍 개명(`schemas/grading.py`→`report.py`, `app/gradings.py`→`reports.py`, `api/gradings.py`→`reports.py`), 기존 결함 3개(`ALTERNATIVE_COMPARISION` 오타·`COMPELETED`·`AxisEvidence` 중복 정의) 정리. 41 tests
- **S1 완료 (커밋 `4bda015`)** — 이름 통일. `decision_point`/`dp_*` 어휘를 `problem`/`problem_*`로, `depth_level`을 `axis_code`로 교체

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있어 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋 안 됨).

### 코드가 계약을 아직 못 따라간 곳 (2026-07-30 확정분 미반영)

다음 표가 T2·T2b·T3의 실제 작업 목록이다. **문서가 앞서 있고 코드가 뒤에 있다.**

| 파일:줄 | 지금 | 되어야 할 것 | 처리 |
|---|---|---|---|
| `app/engines/stub.py:33` | `"status": "OPEN"` | `"READY"` — `assessment_problem.status` 허용값에 `OPEN`이 없다 | T2 |
| `app/engines/stub.py:32` | `"type": "CODE_RISK"` | `"problem_type": "RISK_POINT"` | T2 |
| `app/engines/stub.py:36` | `"focus_code": "HARDCODED_SECRET"` | `"question_focus_item_id"` (요청 `focusItems[]`에서 받은 UUID) | T2 |
| `app/engines/stub.py:48` | `"reference_type": "PRIMARY"` | `PRIMARY` 폐기. `CALLER` 등 6종 중 하나 | T2 |
| `app/engines/stub.py:29~52` | `problem_no`·`code_snippet`·`stages` 없음 | 셋 다 필수. `stages` 4개 × `hints` 2개를 스텁도 채운다 | T2 |
| `app/schemas/analysis.py:29` | `focus_areas: list[str]` | `focus_items: list[FocusItem]` | T2 |
| `app/schemas/analysis.py:28` | `question_budget` 기본값 `4` | `3` | T2 |
| `app/schemas/analysis.py:64` | `reference_type` 설명이 "PRIMARY 등, 카탈로그 미정(B-3)" | 6종 확정. 설명 교체 | T2 |
| `app/schemas/analysis.py:66~81` | `Hint` 클래스 + `ProblemStage.hints` | **유지.** 힌트는 분석 때 동결되므로 분석 응답에 실린다 | — |
| `app/schemas/analysis.py:72~81` | `ProblemStage`에 검증 없음 | `hints`가 정확히 2개이고 `hint_level`이 `[1, 2]` 순서인지 검사하는 validator **추가** | T2 |
| `app/schemas/analysis.py:83~98` | `Problem`에 검증 없음 | `stages`가 정확히 4개이고 `axis_code`가 `L1`→`L4` 순서인지 검사하는 validator **추가** | T2 |
| `app/schemas/analysis.py:83~98` | `Problem.status` Literal 5종(`CANDIDATE`…) · `focus_code` | 4종(`READY`/`IN_PROGRESS`/`COMPLETED`/`TERMINATED`) · `question_focus_item_id` | T2 |
| `app/schemas/analysis.py` | `problem_no`·`code_snippet`·`problem_type`·`extractor_version` 없음 | 추가 | T2 |
| `app/schemas/report.py:16~21` | `AxisCode` = `L1_CODE_DESCRIPTION`… **그리고 L3/L4가 뒤집혀 있다** | `"L1"`~`"L4"`, L3=대안·L4=반례 | T2b |
| `app/schemas/report.py:43~74` | `LevelScore.raw_score`/`score`, `ReportSummary.total_score` | `best_score`/`confirmed_score`, 세션 총점 제거 | T2b |
| `app/schemas/report.py:52` | `hints_used` `le=2` | 유지(힌트 최대 2회). 단 `attempt_count`(0~3)와 별개 필드임을 명시 | T2b |
| `app/sessions.py:49~57` | `_build_questions()`가 세션 시작 시 질문을 만든다 (P03 구조) | 질문은 분석 때 동결돼 DB에 있다. Spring이 읽어 넘겨준 것을 쓴다 — 생성하지 않는다 | T3·T8 |
| `app/sessions.py:55` | `"axis_code": "L1_CODE_DESCRIPTION"` | `"L1"` | T3 |
| `app/schemas/session.py:56` | `state` Literal에 `TIMEOUT` | `IN_PROGRESS`/`PAUSED`/`COMPLETED`/`FAILED`/`EXPIRED` — `assessment_session.status`에 `TIMEOUT`이 없다 | T3 |
| `app/schemas/session.py` | 턴 점수 필드 없음 | `best_score`·`confirmed_score`·`attempt_count`·`hint_text`·`autonomy` | T3 |

---

## 할 일

### T0 — 백엔드 대기 상태판

**이슈 `Team-IZ/Backend#31` 본문이 항상 최신 상태다.** 사본: `../qna/2026-07-30/issue-body-v2.md`.
2026-07-29 질문지(`../qna/2026-07-29/backend-schema-questions.md`)는 이력이며 대부분 해결됐다.

남은 것은 **DDL 수정 3건 + 코드값 1건 + 답변 4건.** 신설 테이블·컬럼은 없다.

**백엔드가 해줄 것**

| # | 내용 |
|---|---|
| B-1 | `problem_stage.attempt_count` CHECK `0~2` → `0~3` (힌트 2회 = 답변 3회) |
| B-2 | `stage_answer_attempt` 마지막 CHECK의 `attempt_no = 2`를 `IN (2, 3)`으로 |
| B-3 | `stage_answer_attempt` 컬럼 5개(`answer_text`·`score_value`·`score_reason`·`answered_at`·`client_request_id`) NULL 허용 + all-or-nothing CHECK |
| B-4 | 코드값 `MEAS.PROBLEM_TERMINATION_REASON`에 `TERMINATED_AT_L1` 추가 (DDL 변경 없음) |

**B-3이 왜 필요한가**: 질문을 보여주는 시점과 저장되는 시점이 어긋나 있다. 행의 의미를 "답변 1건"에서 **"문답 1라운드"**로 바꿔, 질문이 나갈 때(동결된 질문·힌트를 INSERT) 넣고 답이 오면 채운다. NOT NULL이 하던 역할은 all-or-nothing CHECK가 이어받는다.

**질문·힌트 동결분이 B-3에 정확히 들어간다.** 분석 단계가 문제당 질문 4개 + 힌트 8개를 동결하므로, 분석 직후 `stage_answer_attempt` 행을 미리 만들어 두면 된다.

```
문제 3 × 단계 4 × 시도 3 = 36행   (전부 answer_text NULL)
  attempt_no 1   question, hint NULL
  attempt_no 2   question, hint1        ← 힌트 24개가 여기 들어간다
  attempt_no 3   question, hint2
질문 12개 + 힌트 24개가 정확히 맞는다
```

`stage_hint` 테이블·`problem_stage.question_text` 컬럼 신설 요청을 철회한 것이 결과적으로 맞았다 — B-3 하나로 동결분을 다 수용한다. **백엔드에 다시 보낼 것은 없다.** B-1·B-2·B-4도 그대로 필요하다.

**답 대기 (4건)**

| # | 심각도 | 내용 | 우리 작업을 막나 |
|---|---|---|---|
| C-1 | 🔴 | `question_focus_item` 지칭 방식 — 요청에 `focusItems[]`를 실어 보내는 안 | 아니다. 그 안으로 진행하고 회신 오면 조정 |
| C-2 | 🔴 | `score_run`·`axis_score` 유지 여부 | 아니다. **어느 쪽이든 AI 응답은 같다** |
| C-3 | 🟢 | 단가는 Spring이 채우는가 | **막는다.** 세 컬럼이 NOT NULL인데 AI는 단가를 모른다 |
| C-4 | 🟢 | `source_type` 값 목록 | 아니다. 형식(`{source_id}:{source_type}:{attempt_no}`)만 지키고 값은 나중에 맞춘다 |

**나중에 여쭐 것** — `/reports`와 `report`·`report_generation_run` 정합 · 재시험 기록 위치 · **힌트용 `feature_code`(적응형 힌트 모듈이 붙는 시점에 다시 필요해진다. 지금 물을 것은 아니다)**.

**해소됨(현재 계약 기준)** — "힌트 생성의 `feature_code`"는 지금 물을 필요가 없다. PoC를 직접 읽어 확인한 결과 **힌트는 분석 단계에서 질문과 함께 동결**되므로 `QUESTION_GENERATION`이고, 세션 중 LLM 호출은 채점(`GRADING`) 하나뿐이다. 적응형 힌트가 추가되면 되살아난다(D11).

**팀원 수정 요청 확정 (백엔드 아님)** — PoC `scoring-config.js`의 축 순서가 L3=반례, L4=대안으로 우리 기준과 반대다(§함정). **축 순서는 L3=대안 비교 / L4=반례 대응·한계로 확정됐고 팀원 코드를 고치기로 결정했다.** 요청서: `../qna/2026-07-30/poc-axis-order-fix.md`.

### 백엔드 스키마의 근거

**`../docs/docs_for_read/테이블정의서_v06.md`(2026-07-30 변환)와 이슈 #31 본문을 본다.** 이 문서에 테이블 구조를 복사해 두지 않는다 — 복사하면 곧 낡고, 낡은 사본이 최신 정의서보다 위험하다. 우리가 계약으로 못 박은 값만 아래 §계약 기준값에 남긴다.

로컬 `../Backend/`는 `main` 스캐폴드라 낡았다. 백엔드 코드를 확인할 일이 있으면 `origin/develop`을 본다. AI 연동 도메인(`analysis_job`·`assessment_*`·보고서)은 아직 구현되지 않았고 `ai_usage`만 조회 경로가 있다.

---

### T1 ✅ — 채점 → 보고서 전환 (`/gradings` → `/reports`)

완료. 위 §완료된 것 참조. 남은 정합성 문제는 T2b에서 처리한다.

---

### T2 — 분석 요청·응답 재정렬

**대상**: `schemas/analysis.py`, `engines/stub.py`

**요청에 추가·교체**

```python
focus_items: list[FocusItem] = []        # [{id, name}] 강사 지정 후보. focus_areas 폐기
requirements: list[dict] = []            # [{requirementId, text}]
teaches: list[dict] = []                 # [{id, label, unitId, sourcePages}]
curriculum_id: str | None = None
model_code: str | None = None            # 생략 시 서버 기본값. operator가 고른다
question_budget: int = 3                 # 기본값 4 → 3
```

`focusItems`는 Spring이 후보를 보내고 **AI가 그중 하나의 `id`를 그대로 돌려주는** 방식이다(C-1 제안). `question_focus_item`의 PK가 랜덤 UUID여서 AI 코드에 값을 박을 수 없기 때문이다. 결과적으로 AI가 강사 지정 범위를 벗어날 수 없다.

**응답에 추가**

```python
analysis_document_markdown: str          # code_analysis.analysis_document_markdown 대응
requirement_results: list[dict]          # [{requirementId, verdict: "P"|"F", evidence, note}]
```

`requirementResults`는 **요청 `requirements`와 길이가 같아야 한다.** 모델이 일부를 빠뜨리면 조용히 채우지 말고 `verdict="F"` + `note="판정 실패"`로 명시하고, 길이가 다르면 에러로 막는다.

**`problems[]` 각 항목** — DB `assessment_problem` + `problem_reference` 컬럼명 그대로

```jsonc
{
  "problemId": "...",
  "problemNo": 1,                      // 1~3
  "status": "READY",                   // READY|IN_PROGRESS|COMPLETED|TERMINATED
  "problemType": "DESIGN_CHOICE",      // 5종
  "priority": 0.91,
  "questionFocusItemId": "uuid",       // 요청 focusItems에서 고른 id
  "sourcePath": "app/main.py",
  "lineStart": 12, "lineEnd": 14,
  "codeSnippet": "...",                // 신규. evidenceHash의 대상
  "evidenceHash": "...",
  "extractorVersion": 1,
  "references": [ { "path": "...", "lineStart": 12, "lineEnd": 14,
                    "evidenceHash": "...", "referenceType": "CALLER" } ],
  "stages": [
    { "axisCode": "L1", "questionText": "...", "flagged": false,
      "hints": [ { "hintLevel": 1, "hintText": "..." }, { "hintLevel": 2, "hintText": "..." } ] }
    // L2 · L3 · L4 동일 구조로 총 4개
  ]
}
```

**`stages`는 정확히 4개(L1~L4)이고 각 stage에 `hints` 2개가 실린다.** 질문과 힌트는 분석 단계에서 동결된다(§계약 기준값 → 생성 시점). `Hint` 클래스와 `ProblemStage.hints`는 **그대로 둔다.**

**대신 검증을 추가한다** — 현재 파일에 없다.

```python
# ProblemStage 에: hints가 정확히 2개이고 hint_level이 [1, 2] 순서인가
# Problem 에:      stages가 정확히 4개이고 axis_code가 L1→L2→L3→L4 순서인가
```

동결이 성립하려면 모양이 완전해야 한다. 3단계만 온 문제를 그대로 통과시키면 학생이 L4에서 질문 없는 화면을 보고, 그게 채점 0점으로 기록된다. 스키마에서 막는다.

**`codeSnippet`을 AI가 보내는 이유**: `evidence_hash`가 "code_snippet 기준 해시"이고 해시를 AI가 만든다. Spring이 ZIP을 따로 잘라내면 줄바꿈·BOM이 1바이트만 달라도 해시가 안 맞는다.

`stub.py`도 같이 고쳐 새 필드가 실제로 나오게 한다(배선 확인용). 위 §코드가 계약을 아직 못 따라간 곳의 `stub.py` 5줄이 여기서 처리된다.

**DoD**: Swagger에 새 필드가 보이고, 스텁 응답의 `status`가 `READY`, `stages`가 4개(각 `hints` 2개), `codeSnippet`이 채워진다. 3개짜리 `stages`를 넣은 테스트가 검증 오류로 막힌다.

---

### T2b — 보고서 스키마 교정

**대상**: `schemas/report.py`

1. **`AxisCode`를 `"L1"`~`"L4"`로 교체하고 L3/L4 의미를 바로잡는다.** 기존 값(`L1_CODE_DESCRIPTION` 등)은 DB `problem_stage.axis_code` CHECK(`'L1'`~`'L4'`)와 다르고, **L3=반례·L4=대안으로 뒤집혀 있었다.** 정의서·프론트·PM 기준이 L3=대안, L4=반례다
2. `raw_score` → `best_score`, `score` → `confirmed_score` (DB 컬럼명과 1:1)
3. `attempt_count`(0~3) 추가, `passed`(bool) 추가
4. **세션 총점·축 평균을 응답에서 뺀다.** 점수는 매 턴 `problem_stage`에 이미 저장돼 있고, 요약이 필요하면 Spring이 집계한다. LLM이 아니면 못 만드는 것(서술형 진단·교안 매핑)이 `/reports`의 본체다
5. `QuestionResult` → `problems[]`로 이름을 맞춘다. 문제 만점은 20(4단계 × 5)

```jsonc
// GET /reports/{jobId} → result
{
  "problems": [
    { "problemNo": 1, "problemId": "...", "totalScore": 16, "maxScore": 20,
      "stages": [ { "axisCode": "L1", "confirmedScore": 4, "bestScore": 4,
                    "attemptCount": 1, "passed": true } ] }
  ],
  "reportMarkdown": "...",
  "curriculumRefs": [ /* {teachId, unitId, sourcePages} */ ],
  "retestTargets": [ "problemId" ],
  "versions": { "modelCode": "...", "promptVersion": "...", "rubricVersion": "..." }
}
```

**DoD**: `AxisCode`가 4값이고 L3/L4 주석이 대안/반례 순서다. 세션 총점 필드가 없다. 테스트 통과.

---

### T3 — 세션 턴에 점수 필드 추가

**대상**: `schemas/session.py`, `app/sessions.py`

```python
# Question 에
hint_text: str | None = None
attempt_no: int = 1                # 1=원질문, 2~3=힌트 후 재질의

# TranscriptTurn 에 (Spring이 영속화·restore로 되돌려야 하므로 필수)
best_score: int | None = None      # 힌트 상한 적용 "전" LLM 원점수 0~5
confirmed_score: int | None = None # 상한 적용 "후" 기록 점수
attempt_count: int = 0             # 0~3. 0은 미도달
passed: bool | None = None
hint_text: str | None = None
autonomy: str | None = None        # SELF | SELF_MAINTAINED | PARTIAL

# CodeContext 에
line_end: int | None = None
```

**`best_score`와 `confirmed_score`를 둘 다 두는 이유**: 힌트가 점수 상한을 깎는다. 캡 적용 결과만 남기면 상한 정책을 바꿨을 때 재계산이 불가능하고 감사도 안 된다. 이름은 DB 컬럼(`problem_stage.best_score`·`confirmed_score`)과 1:1로 맞춘다.

`hints_used`는 별도 필드로 보내지 않는다 — `attempt_count - 1`이고, `status='NOT_REACHED'`면 0이다.

`SessionView.state`의 `TIMEOUT`을 `EXPIRED`로 바꾸고 `PAUSED`를 넣는다 — `assessment_session.status` 허용값이 `READY`/`IN_PROGRESS`/`PAUSED`/`COMPLETED`/`FAILED`/`EXPIRED`다.

`app/sessions.py`의 `_Turn` dataclass와 `_to_view()`도 같이 고치고, `_build_questions()`의 `axis_code`를 `"L1"`로 바꾼다. `_build_questions()` 자체의 존재 여부는 T8에서 결정된다.

**DoD**: `/answers` 응답에 방금 턴 점수가 실려 나온다.

---

### T3b — `aiUsage` 스키마 확정 (지금은 `dict` 열림)

**대상**: `schemas/common.py`(모델 추가), `schemas/analysis.py`·`session.py`·`report.py`(타입 교체), 엔진 호출부

**왜**: 모든 응답에 `ai_usage: list[dict[str, Any]]` 자리가 이미 있지만 **모양이 안 정해져 있다.** DB `ai_usage`는 기관별 호출·토큰·비용 원장이고 NOT NULL 컬럼이 많아서, AI가 안 주면 Spring이 행을 만들 수 없다.

```python
class AiUsage(BaseSchema):
    idempotency_key: str          # {source_id}:{source_type}:{attempt_no}
    source_type: str              # 값 목록 백엔드 확정 대기 (C-4)
    source_id: str
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

**단가·비용은 넣지 않는다.** AI가 모델 단가표를 들고 있으면 단가가 바뀔 때마다 재배포해야 한다. `ai_model`을 가진 Spring이 토큰 수에 곱한다(C-3 확인 대기).

**실패한 호출도 기록한다.** 실패해도 토큰·비용이 발생하고 재시도 통계에 필요하다.

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

커밋하고 백엔드·프론트에 전달한다. **T2~T4를 다 끝내고 한 번에 한다** — 중간에 여러 번 주면 백엔드가 헛작업한다. 이슈 #31의 완료 조건에 "AI가 `schemas/` 확정 후 `openapi.json` 갱신·공유"가 들어 있다.

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
| `hint-ladder.js` | 99 | 문제 하나의 L1~L4 질문 + 단계별 힌트 2개를 **한 번에 생성해 동결**(`frozen_at`). 그대로 옮긴다 |
| `question-guard.js` | 56 | 질문·힌트에 선택지가 섞이는 것 차단 → 재생성 |
| `code-fragment.js` | 90 | `{file, lines}`를 실제 파일과 대조해 파편 추출 |
| `requirements.js` | 36 | 요구사항 P/F 판정 |
| `llm-stage.js` | 87 | 매니페스트 스테이지 1개 호출 공용 경로 |

**스테이지 구성** (전부 LLM)

```
분석 배치
  p04-1  코드 분석 문서                  입력: teaches + problems + code
  p04-2  요구사항 P/F 판정
  p04-3  문제 3개 선정
  p04-4  L1~L4 질문 + 힌트 2개씩 동결     문제당 1콜 (질문 4 + 힌트 8)
런타임
  p04-5  답변 채점 (단계 1개, 0~5점)      턴당 1콜. 세션 중 유일한 LLM 호출
세션 후
  p04-6  보고서
```

**세션 루프에는 LLM 호출이 하나뿐이다.** `poc-engine.js:218`이 동결된 `lvl.hints[hintsUsed - 1]`을 꺼내 쓰고, 호출은 `gradeLevel`(p04-5)만 한다. 이식할 때 이 구조를 바꾸지 않는다.

**LLM 클라이언트**: `_legacy/pipeline/feedback/nvidia_client.py`·`nvidia_key_pool.py`가 서버용 원본이라 재활용한다. Worker 프록시(`worker/nvidia-proxy.js`)는 브라우저 제약의 산물이므로 옮기지 않는다.

**레이트리밋**: 무료 티어 분당 40회. PoC의 `shared/traffic-rate.js`가 같은 상수를 들고 있다. **이건 잘라낼 것이 아니라 서버로 옮겨야 할 것이다.** 짧은 초과는 내부 큐로 흡수하고, 한계를 넘으면 `RATE_LIMITED` + `retryAfterSec`로 돌려준다.

**잘라낼 것**: Supabase 저장(`shared/db.js`), Worker 프록시, IndexedDB·sessionStorage, UI·타이머, 브라우저 pdf.js.

> Supabase 관련 보안 주의: 팀 공용 프로젝트가 open signup + RLS read-all이라 cross-tenant 위험이 있고, 팀원이 **브라우저 PoC 한정으로** 수용했다. 제품 결정이 아니다. 이식 과정에서 Supabase 클라이언트·스키마·인증 흐름을 따라 옮기지 않는다. 애초에 FastAPI는 저장하지 않으므로 옮길 것이 없어야 정상이고, Supabase 호출이 필요해 보이면 설계가 틀린 것이니 멈추고 재검토한다.

### 🔴 이식 중 반드시 할 것 — PoC 축 순서를 뒤집는다

`scoring-config.js:22~77`의 `AXES`가 **L3=반례한계, L4=대안**이다. 우리 기준(백엔드 `axis_score`·프론트·PM)과 **반대다.**

```
PoC        L1 코드기술 / L2 설계논리 / L3 반례한계 / L4 대안
우리 기준   L1 코드기술 / L2 설계논리 / L3 대안     / L4 반례·한계
```

이식할 때 `AXES`의 **`order`·`label`·`question_intent`·`values` 루브릭 텍스트를 L3↔L4로 교환**한다. 루브릭이 축에 붙어 있으므로 순서만 맞추고 텍스트를 그대로 두면 **L3 답변이 L4 기준으로 채점된다.** `axisWeights`(`:124`)와 `prompt_manifest.json` p04-4의 축 열거 순서도 같이 본다.

**팀원 코드를 우리가 고치지 않는다.** 워크트리는 읽기 전용이므로 이식본에서 교환하고, **원본 정정은 팀원에게 요청하기로 확정**했다 — 요청서 `../qna/2026-07-30/poc-axis-order-fix.md`.

---

### T8 — 세션 엔드포인트 축소 (방향 확정, 작업 미착수)

**백엔드가 문제·동결 질문·동결 힌트를 분석 직후에 DB에 저장하는 구조를 골랐다.** 세션을 `READY`로 미리 만들고 문제 3행 + 단계 12행 + 문답 라운드 36행을 INSERT해 둔다. 그래서 **세션 시작 시 AI 호출이 필요 없다.**

```
제출 마감 → 코드 분석 배치 → 팀원 수만큼 assessment_session(READY) 생성
                            → 세션마다 assessment_problem 3행 + problem_stage 12행
                                       + stage_answer_attempt 36행 (질문 12 + 힌트 24, 답변란 NULL)
                            ↓
                  학생 접속 → IN_PROGRESS 전환, 즉시 시작 (AI 호출 없음)
```

근거: `assessment_problem.session_id`가 NOT NULL이고 `UNIQUE (session_id, problem_no)`라 문제 행은 세션마다 생긴다. 분석 시점에는 세션이 없으면 문제를 저장할 곳이 없다. `assessment_session.status` 기본값도 `'READY'`다.

| 엔드포인트 | 처분 |
|---|---|
| `POST /sessions` | 없어질 수 있음 |
| `GET /sessions/{id}` | 없어질 수 있음 |
| `POST /sessions/{id}/restore` | 없어질 수 있음 — 모든 요청이 곧 restore가 된다 |
| `POST /sessions/{id}/answers` | **남는다.** 채점 (다음 질문·힌트는 DB에 이미 있다) |

대가는 payload다. `/answers` 요청에 `transcript` + `problems` + 분석 문서를 매번 실어야 해서 후반 턴이 약 32KB로 커진다. 서비스 간 내부 통신이라 무시할 수준이다.

**실제 제거는 아직 하지 않았다.** T2~T5로 계약을 굳히고 `openapi.json`을 넘긴 뒤, 백엔드가 세션 선생성을 구현하는 시점에 맞춰 지운다. 지금 지우면 백엔드가 붙여볼 수 있는 면이 줄어든다.

---

## 계약 기준값

구현할 때 이 값을 그대로 쓴다.

### 공통

```
경로 prefix   /api/v0
필드 표기     camelCase (내부는 snake_case, 직렬화만 변환)
              단 problems[]·stages[] 내부는 DB 컬럼명을 그대로 쓴다
에러          {error, message, retryable}  평탄 구조. timestamp·path 안 씀
헤더 3종      X-Internal-Key(인증, health 면제) · Idempotency-Key(submissionId:attemptNo) · X-Trace-Id
analysisId    Spring이 발급. AI는 만들지도 받지도 않는다
modelCode     UUID(model_id) 대신 문자열로 주고받는다. 기본값은 서버 설정
```

### 이름 (2026-07-30 개명 완료 — 옛 이름을 쓰지 않는다)

```
assessment_problem       구 decision_point       PK problem_id
problem_reference        구 dp_reference         PK reference_id
problem_stage            단계 테이블              PK problem_stage_id, UNIQUE(problem_id, axis_code)
stage_answer_attempt     답변 이력                PK answer_attempt_id, attempt_no 1~3
question_focus_item      구 focus_area_code
project_requirement / project_requirement_assessment   요구사항·판정 결과
curriculum_section / teaches                            교안
code_analysis.analysis_document_markdown (TEXT)         분석 문서

폐기: session_turn · dp_question · question_candidate(안 씀) · depth_level
     score_run / axis_score — AI가 값을 만들지 않는다 (유지 여부는 백엔드 몫, C-2)
```

### 축 — L3/L4 순서에 주의

값은 **`"L1"` `"L2"` `"L3"` `"L4"`** (DB `problem_stage.axis_code` CHECK와 동일).

| 단계 | 무엇을 묻나 | 백엔드 `axis_score.axis_code` 대응 |
|---|---|---|
| L1 | 무엇을 하는 코드인가 | `CODE_UNDERSTANDING` |
| L2 | 왜 그렇게 했는가 | `DESIGN_LOGIC` |
| L3 | **다른 방법과 비교 (대안)** | `ALTERNATIVE_COMPARISON` |
| L4 | **언제 깨지는가 (반례·한계)** | `COUNTEREXAMPLE_RESPONSE` |

> 우리가 L3=반례, L4=대안으로 뒤집어 쓰고 있었다. 정의서·프론트·PM 기준이 위 표다. `schemas/report.py`는 아직 옛 순서다(T2b).

### 질문·힌트 생성 시점 — 분석 배치에서 전부 동결한다

```
분석 배치   문제 3개 + 문제별 L1~L4 질문 12개 + 단계별 힌트 2개씩 24개
런타임      채점만
```

**질문과 힌트는 학생 답변을 보기 전에 만들어져 동결된다.** 근거: 답변을 보고 힌트를 만들면 학생마다 힌트가 달라져, "몇 번째 힌트에서 통과했는가"가 학생 실력이 아니라 생성 결과의 차이를 재게 된다. 같은 문제를 받은 두 학생은 글자 단위로 같은 질문과 같은 힌트를 받아야 한다.

PoC 구현이 이미 그렇다 — `poc-engine.js:110`이 분석 단계에서 `HintLadder.freezeQuestionSet()`을 호출하고, `hint-ladder.js:86,94`가 `frozen_at`을 찍는다. 세션 루프(`poc-engine.js:218`)는 동결된 `lvl.hints[hintsUsed - 1]`을 꺼내 쓸 뿐이다.

그래서 **`AnalysisResult`의 `problems[].stages[]`는 4개(L1~L4)이고 각 stage에 `hints` 2개가 실린다.**

세션 중 LLM 호출: **채점 1콜뿐.** 질문 생성도 힌트 생성도 세션 중에 없다.

### 채점

```
단계당 0~5점, 통과선 3점
힌트 상한   {0회: 5, 1회: 4, 2회: 3}     ← 미확정. 완주 30건 후 재보정
문제당 만점 20 (4단계 × 5)               ← 세션 총점(60)은 AI가 보내지 않는다
attempt_count  0~3  (0 = 미도달)
hints_used     = attempt_count - 1       단 status='NOT_REACHED'면 0
자력도         0회=SELF / 1회=SELF_MAINTAINED / 2회=PARTIAL
실패 시        그 문제 종료, 다음 문제의 L1로
가중치         축별 균등 1.0 (미확정 — 완주 세션 30건 후 재보정)
재시험         문제 단위. 축 평균은 쓰이지 않는다 (기준값 미확정)
```

DB 컬럼 대응 — 우리 `rawScore`/`score`를 이 이름으로 맞춘다.

```
best_score        힌트 상한 적용 "전" 원점수   (구 rawScore)
confirmed_score   상한 적용 "후" 기록 점수     (구 score)
passed            BOOLEAN
status            LOCKED / READY / IN_PROGRESS / PASSED / FAILED / NOT_REACHED / COMPLETED
```

**코드 파편은 연속 범위 하나다** — `lines: [start, end]` 2원소(`code-fragment.js`). `line_start`/`line_end`로 그대로 매핑된다.

### `problem_type` 5종

"왜 이 지점을 골랐나" — `questionFocusItem`의 "무엇을 묻나"와 다른 축이다.

```
DESIGN_CHOICE          대안이 있었는데 이것을 택한 지점
RISK_POINT             규칙 스캔이 잡은 잠재 결함
COMPLEXITY_HOTSPOT     분기·중첩·길이가 몰린 곳
REQUIREMENT_IMPL       특정 요구사항을 구현한 부분
EXTERNAL_INTEGRATION   외부 라이브러리·API 사용 결정
```

### `reference_type` 6종

추가 근거의 역할. **`PRIMARY`는 안 쓴다** — 주 코드 지점이 `assessment_problem`으로 옮겨졌다.

```
CALLER · CALLEE · DEFINITION · TEST · CONFIG · SIMILAR
```

`problem_type`·`reference_type` 둘 다 DB에 CHECK가 없어 형식(영문 대문자 스네이크 케이스)만 맞으면 된다.

### DB CHECK 제약 (테이블정의서 v06 기준)

이 값을 어기면 Spring INSERT가 깨진다. **전체는 정의서를 보고, 여기에는 AI가 실제로 채우는 컬럼만 남긴다.**

```
analysis_job.status         QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED
assessment_problem.status   READY, IN_PROGRESS, COMPLETED, TERMINATED   ← 기본값 READY
assessment_problem.problem_no        BETWEEN 1 AND 3
assessment_problem.line_end          >= line_start
assessment_problem.extractor_version > 0
assessment_session.status   READY, IN_PROGRESS, PAUSED, COMPLETED, FAILED, EXPIRED
problem_stage.axis_code     L1, L2, L3, L4
problem_stage.status        LOCKED, READY, IN_PROGRESS, PASSED, FAILED, NOT_REACHED, COMPLETED
problem_stage.attempt_count BETWEEN 0 AND 2   ← B-1로 0~3 요청 중. 회신 전에는 0~2가 진짜 한계
problem_stage.best_score / confirmed_score    NULL 또는 0~5
stage_answer_attempt.attempt_no      IN (1, 2, 3)
stage_answer_attempt.score_value     BETWEEN 0 AND 5
submission.method           GITHUB_URL, ZIP_WITH_GITLOG
*_scope_code                TOTAL, OWN_COMMIT
termination_reason          COMPLETED_L4, TERMINATED_AT_L2, TERMINATED_AT_L3
                            (+ TERMINATED_AT_L1 요청 중 — B-4. CHECK는 없어 값 자체는 통과한다)
```

**폐기된 값** — 쓰면 INSERT가 깨진다: `assessment_problem.status`의 `OPEN`·`CANDIDATE`·`USED`·`SKIPPED`·`INVALID`, `assessment_session.status`의 `TIMEOUT`·`ABANDONED`, `session_turn.state` 전체.

**AI만 아는 NOT NULL 값** — 응답에 없으면 Spring이 행을 만들 수 없다.

| 테이블 | 컬럼 |
|---|---|
| `code_snapshot` | `content_hash`, `file_count`, `byte_count` |
| `commit_attribution` | `commit_hash`, `authored_at`, `changed_line_count`, `contribution_ratio` |
| `file_attribution` | `path`, `attribution_type`, `commit_count`, `changed_line_count`, `changed_function_count`, `confidence` |
| `assessment_problem` | `problem_no`, `problem_type`, `source_path`, `line_start`, `line_end`, `code_snippet`, `evidence_hash`, `extractor_version` |
| `problem_reference` | `source_path`, `line_start`, `line_end`, `evidence_hash`, `reference_type` |

### `ai_usage` 매핑

AI가 채우는 값과 Spring이 채우는 값이 갈린다. **경계를 넘지 않는다.**

| AI가 준다 | Spring이 채운다 |
|---|---|
| `idempotencyKey` · `sourceType` · `sourceId` | `usage_id` · `org_id` · `actor_user_id` |
| `featureCode` · `modelCode` | `model_id` (modelCode로 `ai_model` 조회) |
| `inputTokenCount` · `outputTokenCount` · `cachedTokenCount` | `request_id` · `trace_id` |
| `status` · `failureCode` | `input_unit_price` · `output_unit_price` · `currency_code` |
| `latencyMs` · `occurredAt` | `estimated_cost` · `actual_cost` · `created_at` |

**단가는 Spring이 채운다** — AI는 단가를 모른다. 단가표를 AI에 두면 단가가 바뀔 때마다 재배포다. (C-3 확인 대기. 이게 안 오면 `ai_usage` 쓰기 경로가 통째로 막힌다.)

**`featureCode`** — DB CHECK에 `CODE_ANALYSIS`·`QUESTION_GENERATION`이 추가돼 있다(Q7-1 반영 완료).

| AI 단계 | 값 |
|---|---|
| 코드 분석 문서 (p04-1) | `CODE_ANALYSIS` |
| 요구사항 P/F (p04-2) | `CODE_ANALYSIS` |
| 문제 선정 (p04-3) | `QUESTION_GENERATION` |
| 질문·힌트 동결 (p04-4) | `QUESTION_GENERATION` |
| 답변 채점 (p04-5) | `GRADING` |
| 보고서 (p04-6) | `SUMMARY_DRAFT` |
| 교안 분석 (p01) | `CURRICULUM_ANALYSIS` |

**힌트 생성용 값은 필요 없다.** 힌트는 p04-4에서 질문과 함께 동결되므로 `QUESTION_GENERATION`이고, 세션 중 호출은 `GRADING` 하나뿐이다.

`SESSION_DIALOG`는 DB CHECK에 아직 남아 있지만 코드 정의서 목록에서 빠졌다. **쓰지 않는다.**

**`failure_code` 5종 (확정)**: `TIMEOUT` · `RATE_LIMITED` · `PROVIDER_ERROR` · `INVALID_JSON` · `CONTEXT_OVERFLOW`. `UPSTREAM_ERROR`는 폐기 — `PROVIDER_ERROR`로 쓴다.

**`idempotency_key` = `{source_id}:{source_type}:{attempt_no}`.** UNIQUE 제약은 제거됐다. `source_type`이 호출 종류를 구분하므로 한 요청의 6콜이 서로 다른 키를 갖는다. `source_type` 값 목록은 백엔드 확정 대기(C-4).

**`ai_model.model_code` 초기 목록에 `glm-5.2`가 이미 있다.** 별도 등록 없이 `modelCode`로 `model_id` 조회가 된다.

### 규모

```
NVIDIA 무료 티어   분당 40회
코드 분석 배치      팀당 6콜 (분석문서 1 + 요구사항 1 + 문제선정 1 + 질문·힌트 동결 3)
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
| D8 | 턴 점수를 wire에 노출한다 | Spring이 매 턴 저장한다. 점수·시도 횟수가 wire에 없으면 복구된 세션이 "힌트를 몇 번 썼는지" 모른 채 재개된다 |
| D9 | `bestScore`·`confirmedScore`를 둘 다 보낸다 | 힌트가 점수 상한을 깎는다. 캡 적용 결과만 남기면 상한 정책 변경 시 재계산 불가, 감사도 불가 |
| D10 | 재시험·문제배정 판정은 Spring이 한다 | AI는 점수와 `retestTargets`만 낸다. 커트라인·배정은 조직 정책이라 DB 주인이 갖는다 |
| D11 | **질문·힌트는 분석 때 미리 만들어 동결한다** | 답변을 보고 힌트를 만들면 학생마다 힌트가 달라져, "몇 번째 힌트에서 통과했는가"가 학생 실력이 아니라 생성 결과의 차이를 재게 된다. 같은 문제를 받은 두 학생은 글자 단위로 같은 질문·힌트를 받아야 한다. PoC가 이미 그 구조다(`freezeQuestionSet`·`frozen_at`). **이식할 때 이 성질을 깨지 않는다** |

> **D11 보충 — 적응형 힌트는 폐기가 아니라 후속이다.** 팀원이 **적응형 힌트 모듈을 개발 중이고 나중에 추가할 예정**이다. **현재 계약은 동결 기준**이고, 붙는 시점에 셋이 따라온다.
>
> 1. 세션 중 LLM 호출이 하나 늘어 **턴당 2콜**이 된다 (§규모의 RPM 계산을 다시 한다)
> 2. `feature_code`에 **힌트용 값이 필요해진다** — 지금은 불필요하지만 그때 백엔드에 요청한다
> 3. **한 기수 안에서 두 모드가 섞이면 점수를 나란히 비교할 수 없다.** 동결 힌트로 받은 4점과 적응형 힌트로 받은 4점은 같은 값이 아니다. **체크포인트 단위로 모드를 고정**해야 한다 (`checkpoint`에 모드 컬럼이 필요할 수 있다 — 그때 백엔드와 논의)
| D12 | 세션 총점·축 평균을 AI가 만들지 않는다 | 점수는 매 턴 `problem_stage`에 저장돼 있다. 집계는 Spring이 SQL로 하면 되고, LLM이 아니면 못 만드는 것만 `/reports`에 담는다 |
| D13 | `codeSnippet`을 AI가 보낸다 | `evidence_hash`가 code_snippet 기준 해시이고 해시를 AI가 만든다. Spring이 따로 잘라내면 줄바꿈·BOM 차이로 해시가 안 맞는다 |
| D14 | 값 이름은 DB 컬럼명을 그대로 쓴다 | 새 어휘를 만들면 백엔드가 "정의서에 없는 컬럼"으로 읽는다. 실제로 `callId`로 한 번 겪었다 |

---

## 함정

이전에 실제로 물렸거나 물릴 뻔한 것들.

| 함정 | 대응 |
|---|---|
| `pytest`가 `_legacy/tests/`를 수집해 깨진다 | `pytest.ini`에 `norecursedirs = _legacy .venv` 필수 |
| Swagger가 중첩 모델 `$ref`를 해석 못 한다 | `$defs` 인라인 펼치기로 해결됨. 새 중첩 모델을 넣을 때 `/docs`를 눈으로 확인 |
| DB CHECK에 없는 상태값을 보낸다 | 위 §DB CHECK 제약의 목록만 쓴다. `OPEN`·`CANDIDATE`·`TIMEOUT`은 전부 폐기값이다 |
| L3/L4를 뒤집어 쓴다 | L3=대안, L4=반례. 옛 문서·옛 코드가 반대로 적고 있다 |
| **PoC 축 순서가 우리와 반대다** | `scoring-config.js`가 L3=반례, L4=대안이다. 이식할 때 `AXES`의 `order`·`label`·루브릭 텍스트를 L3↔L4 교환한다(§T7). 순서만 맞추고 루브릭을 그대로 두면 L3 답변이 L4 기준으로 채점된다 |
| `cachedTokenCount > inputTokenCount` | DB CHECK가 막는다. `model_validator`로 먼저 잡는다 |
| `requirementResults` 길이가 요청과 다르다 | 조용히 채우지 말고 에러. 빠뜨린 항목은 `verdict="F"` + `note` |
| 워크트리 경로에 백슬래시 | Bash에서 `..\ai_poc\x`는 이스케이프로 먹혀 엉뚱한 폴더가 생긴다. `/`를 쓴다 |
| develop 병합 경로가 갈린다 | 팀은 GitHub PR로, 로컬은 직접 push하면 갈라진다. 방식을 팀과 통일할 것 |
| 분석 CPU가 이벤트 루프를 막는다 | T6 참조. `def` 또는 `run_in_executor` |

---

## 브랜치·커밋

**전략**: `feature/*` → 동작·테스트 완료 후 `main` → `main` 기준 `develop` 생성 → 이후 `develop`에서 수정·테스트 후 `main` 병합. GitHub 기본 브랜치는 `develop`.

**커밋**: `type: short description (#issue)` — `feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수.

**T 하나당 1커밋**을 권장한다. 이력이 작업 순서 그대로 남는다.

**주의**: 이 저장소는 사용자가 직접 git 명령을 실행한다. 에이전트는 명령을 만들어 전달만 한다.
