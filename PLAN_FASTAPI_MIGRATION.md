# AI 파트 작업 계획

> 갱신: 2026-07-30 · 작업 브랜치 `feature/contract-p04` (`develop`에서 분기)
> **이 문서는 실행용이다.** 무엇을 어떤 순서로, 어떤 방법으로 할지만 적는다.
> 구조·계약의 설명은 `README.md`(팀원용). 백엔드와의 현재 상태판은 이슈 `Team-IZ/Backend#31` 본문(사본 `../qna/2026-07-30/issue-body-v2.md`).

---

## 현재 위치

| | |
|---|---|
| 엔드포인트 | 11/11 동작 (전부 스텁) |
| 테스트 | 58 passed |
| 다음 | **T5 2차 재생성 → T6·T7 이식.** T8(세션 축소)은 백엔드가 세션 선생성을 구현하는 시점에 맞춘다 |
| 막힌 것 | **없다.** C-4·C-5는 값 카탈로그라 회신 없이도 진행한다 |

### 완료된 것

빈 FastAPI 골격부터 다시 쌓아 **엔드포인트 9개 + 인증 + 에러 형식 + camelCase 직렬화 + Swagger + `openapi.json`**을 만들었다. 엔진이 하나도 없어도 백엔드가 붙일 수 있는 상태다.

- `main.py`·`config.py`·`api/deps.py`·`api/errors.py` — 앱 조립, 설정, 인증(`X-Internal-Key`), 예외 핸들러
- `schemas/` — `common.py`(camelCase 기반)·`analysis.py`·`session.py`·`report.py`
- `api/` — `health.py`·`analyses.py`(2)·`sessions.py`(4)·`reports.py`(2)
- `engines/` — Protocol + 스텁 + 팩토리. `engine_mode` 설정으로 교체
- `jobs.py`·`sessions.py`·`reports.py` — 인메모리 저장소 + 수명주기·멱등
- 백엔드 계약 C1~C6 합의(2026-07-22), 로컬 통신 자체검증 통과, cloudflared 터널 준비
- **T1 완료** — `/gradings` → `/reports` 전환. 파일 3쌍 개명(`schemas/grading.py`→`report.py`, `app/gradings.py`→`reports.py`, `api/gradings.py`→`reports.py`), 기존 결함 3개(`ALTERNATIVE_COMPARISION` 오타·`COMPELETED`·`AxisEvidence` 중복 정의) 정리. 41 tests
- **T1b 완료 (커밋 `4bda015`)** — 이름 통일. `decision_point`/`dp_*` 어휘를 `problem`/`problem_*`로, `depth_level`을 `axis_code`로 교체
- **T2·T2b 완료** — 분석·보고서 스키마를 DB 계약에 정렬. `AxisCode`를 `"L1"`~`"L4"`로 바꾸고 **L3=대안 비교 / L4=반례 대응**으로 바로잡음, `Problem`에 `problem_no`·`code_snippet`·`problem_type`·`question_focus_item_id` 추가, `stages` 4개(L1→L4 순서)·`hints` 2개(`[1,2]` 순서) 검증 추가, `AnalysisResult.problems`를 `list[Problem]`으로 교체, 요청에 `focus_items`·`requirements`·`teaches`·`model_code` 추가, 응답에 `analysis_document_markdown`·`requirement_results` 추가(`jobs.py`가 요청 `requirements`와 개수 일치를 검사), 보고서는 `best_score`/`confirmed_score`로 개명하고 세션 총점 제거. 45 tests
- **T5 1차 완료** — `openapi.json` 재생성. `stages` `minItems/maxItems: 4`, `hints` `2`가 스펙에 드러나 백엔드가 동결 구조를 코드 없이 읽는다. **범위는 `/analyses`·`/reports`까지** — T3(세션)·T4(교안) 반영 후 한 번 더 생성한다
- **T7b 진행 중 (2026-07-31, 104 tests)** — **룰 → p04-1 → p04-3 → p04-4 전 구간 실동작.** 실측(step-3.7-flash): **문제 3/3 · 질문 12개**, LLM 5회, 340초, 토큰 24,124. 질문이 실제 코드 위치를 인용하고 선택지 없이 축별로 갈린다
  - 신설: `stages.py`(매니페스트 호출·JSON 파싱·예산 자동 증액) · `fragments.py`(symbol → 줄 번호) · `topics.py`(문제 선정 + 검증 2단계 + 폴백) · `scoring.py`(축 루브릭·힌트 사다리) · `guard.py`(선택지 금지) · `questions.py`(질문 4개 동결)
  - vendor 추가: `prompt_manifest.json`(p04-0.2.0)
  - ⚠️ **p04-1 지연 편차가 크다: 36초 → 115초 → 195초.** 같은 프롬프트인데 5배 넘게 흔들린다. 배치 소요시간을 예측값으로 못 쓴다
  - 남은 것: p04-2(요구사항 P/F) · p04-7(힌트) · 엔진 조립
- **T7a 완료 (2026-07-31, 69 tests)** — LLM 클라이언트 배선. `nvidia_key_pool.py`·`nvidia_client.py`를 `app/llm/vendor/`로(둘 다 상류 nvidia-build에서 vendored된 파일이라 같은 규칙 승계), 우리 소유 `app/llm/client.py`가 키 풀 싱글턴·지연 측정·토큰 추출·실패 분류를 더한다. **실패해도 `usage`를 들고 던진다**(`LlmError.usage`) — 실패한 호출도 토큰을 태운다
- **T6 완료 (2026-07-31)** — 룰 규칙부 이식. **vendor 방식**(원본 12 `.py` + 19 `.json` 무수정 복사 + 우리 소유 `rules.py` 래퍼). ZIP 안전 해제(Zip Slip·심볼릭 링크·ZIP 폭탄 방어), `extractor_version`을 vendor 전체 해시로 산출. `engine_mode="real"` 배선은 T7로 미룸 — 룰만으론 `AnalysisResult`를 못 채운다. 64 tests
- **T2c 완료 (2026-07-31)** — 분석 문서를 Markdown에서 **JSON**으로 교체. `AnalysisDocument`(`overview`·`structure`·`decisionPoints`·`risks`) 신설, `analysis_document_markdown: str` 제거. PoC 파이프라인의 원본이 strict JSON이고 Markdown은 화면 렌더 결과였다. **백엔드에 B-5(컬럼 타입 변경) 요청 발생 — "신설 테이블·컬럼 없음"이 깨진 첫 건.** `openapi.json` 3차 재생성. 59 tests

기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있어 `_legacy/`로 물러났다(`.gitignore` 대상, 커밋 안 됨).

### 코드가 계약을 아직 못 따라간 곳

**스키마는 전부 정렬됐다(T2·T2b·T3·T3b·T4).** 남은 불일치는 하나뿐이고, 그것도 스키마가 아니라 동작이다.

| 파일:줄 | 지금 | 되어야 할 것 | 처리 |
|---|---|---|---|
| `app/sessions.py:40~52` | `_build_questions()`가 세션 시작 시 질문을 만든다 (P03 구조) | 질문은 분석 때 동결돼 DB에 있다. Spring이 읽어 넘겨준 것을 쓴다 — 생성하지 않는다 | T8 |

**T8을 지금 하지 않는 이유**: 백엔드가 세션 선생성(`READY` 상태로 미리 만들기)을 아직 구현하지 않았다. 지금 지우면 백엔드가 붙여볼 수 있는 면이 줄어든다. 백엔드 구현 시점에 맞춰 지운다.

---

## 할 일

### T0 — 백엔드 대기 상태판

**이슈 `Team-IZ/Backend#31` 본문이 항상 최신 상태다.** 사본: `../qna/2026-07-30/issue-body-v2.md`.
2026-07-29 질문지(`../qna/2026-07-29/backend-schema-questions.md`)는 이력이며 대부분 해결됐다.

남은 것은 **DDL 수정 4건 + 코드값 1건 + 답변 2건.** 신설 테이블은 없고, **컬럼 타입 변경이 1건 생겼다(B-5).**

**백엔드가 해줄 것**

| # | 내용 |
|---|---|
| B-1 | `problem_stage.attempt_count` CHECK `0~2` → `0~3` (힌트 2회 = 답변 3회) |
| B-2 | `stage_answer_attempt` 마지막 CHECK의 `attempt_no = 2`를 `IN (2, 3)`으로 |
| B-3 | `stage_answer_attempt` 컬럼 5개(`answer_text`·`score_value`·`score_reason`·`answered_at`·`client_request_id`) NULL 허용 + all-or-nothing CHECK |
| B-4 | 코드값 `MEAS.PROBLEM_TERMINATION_REASON`에 `TERMINATED_AT_L1` 추가 (DDL 변경 없음) |
| **B-5** | **`code_analysis.analysis_document_markdown TEXT` → `analysis_document JSONB`** (2026-07-31 추가. 근거는 T2c) |

**B-5가 왜 생겼나**: 정의서 비고가 *"Markdown은 비정형 문서라 JSONB보다 TEXT가 적합"*이라고 적었는데, **그 전제가 사실과 다르다.** PoC 파이프라인의 분석 문서 원본은 strict JSON이고 Markdown은 화면 렌더 결과다. TEXT로 저장하면 `/reports`에서 되파싱해야 한다. **`assessment` 도메인이 아직 DB·코드 미구현이라 지금이 가장 싼 시점이다.** 자세한 것은 T2c.

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

**답 대기 (2건)**

| # | 심각도 | 내용 | 우리 작업을 막나 |
|---|---|---|---|
| C-4 | 🟢 | `source_type` 값 목록 | 아니다. 형식(`{source_id}:{source_type}:{attempt_no}`)만 지키고 값은 나중에 맞춘다 |
| C-5 | 🟢 | `curriculum_analysis.extraction_status`·`quality_status` 코드 카탈로그 | 아니다. **CHECK가 없는 확장형 업무 코드**라 `str`로 열어뒀다. 스텁은 `EXTRACTED`/`OK`를 쓴다 |

**회신 완료 (2026-07-30)**

- **C-3 해소** — **단가·비용은 Spring이 계산한다. AI는 토큰·모델·지연·상태만 보낸다.** 모델을 고르는 주체가 백엔드·프론트라 단가도 그쪽이 먼저 안다. `input_unit_price`·`output_unit_price`·`currency_code`·`estimated_cost`는 AI 응답에 넣지 않는다

- **C-1 승인** — 요청에 `focusItems: [{id, name}]`를 싣고 AI가 `questionFocusItemId`로 하나를 돌려주는 방식. DDL 변경 없음. 백엔드가 `assessment_problem.question_focus_item_id`(VARCHAR(100))와 `question_focus_item.question_focus_item_id`(UUID)의 타입 불일치를 확인했다
- **C-2 해소** — 07_ENG 시트는 갱신되지 않았고 06_MEAS와 겹치는 테이블(`score_run`·`axis_score`)은 **제거 예정**이다. 점수의 단일 소유자는 `problem_stage`이고 축 어휘도 `'L1'`~`'L4'` 한 벌만 남는다

**나중에 여쭐 것** — `/reports`와 `report`·`report_generation_run` 정합 · 재시험 기록 위치 · **힌트용 `feature_code`(적응형 힌트 모듈이 붙는 시점에 다시 필요해진다. 지금 물을 것은 아니다)**.

**해소됨(현재 계약 기준)** — "힌트 생성의 `feature_code`"는 지금 물을 필요가 없다. PoC를 직접 읽어 확인한 결과 **힌트는 분석 단계에서 질문과 함께 동결**되므로 `QUESTION_GENERATION`이고, 세션 중 LLM 호출은 채점(`GRADING`) 하나뿐이다. 적응형 힌트가 추가되면 되살아난다(D11).

**팀원 수정 요청 확정 (백엔드 아님)** — PoC `scoring-config.js`의 축 순서가 L3=반례, L4=대안으로 우리 기준과 반대다(§함정). **축 순서는 L3=대안 비교 / L4=반례 대응·한계로 확정됐고 팀원 코드를 고치기로 결정했다.** 요청서: `../qna/2026-07-30/poc-axis-order-fix.md`.

### ⚠️ `origin/feature/code-importance-map`은 병합 대상이 아니다

2026-07-31 확인. 팀원이 **`develop`에서 갈라 실험 중인 브랜치**다. `app/engines/codemap/`(엔진 재설계·`analysis_doc.py`가 p04-1 상당)·`app/engines/shared/`(llm·budget·prompts·evidence)·Dockerfile·Cloudflare 배포·CI·pre-commit이 통째로 들어 있어 **우리 작업과 정면으로 중복돼 보이지만, 팀원이 "합칠 일 없다"고 확인했다.**

`diff --stat origin/feat/poc_full ...`으로 보면 우리 서버 파일이 전부 추가로 잡혀 오해하기 쉽다 — **`origin/develop` 기준으로 봐야** 실제로 더한 것이 보인다.

**참고 가치는 있다**(가져오진 않는다): `tools/check_no_secrets.py`+`.githooks/pre-commit`(키 유출 방어), `Dockerfile`·`wrangler.toml`(배포 — 백엔드가 Railway라 우리도 주소가 필요해진다), `tools/check_prompt_drift.py`(매니페스트 드리프트 검사 — 지금 `vendor/SOURCE.md`가 수동으로 하는 일).

### 백엔드 스키마의 근거

**`../docs/docs_for_read/테이블정의서_v06.md`(2026-07-30 변환)와 이슈 #31 본문을 본다.** 이 문서에 테이블 구조를 복사해 두지 않는다 — 복사하면 곧 낡고, 낡은 사본이 최신 정의서보다 위험하다. 우리가 계약으로 못 박은 값만 아래 §계약 기준값에 남긴다.

로컬 `../Backend/`는 `main` 스캐폴드라 낡았다. 백엔드 코드를 확인할 일이 있으면 `origin/develop`을 본다. AI 연동 도메인(`analysis_job`·`assessment_*`·보고서)은 아직 구현되지 않았고 `ai_usage`만 조회 경로가 있다.

---

### T1 ✅ — 채점 → 보고서 전환 (`/gradings` → `/reports`)

완료. 위 §완료된 것 참조. 남은 정합성 문제는 T2b에서 처리한다.

---

### T2 ✅ — 분석 요청·응답 재정렬

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
analysis_document_markdown: str          # ⚠️ T2c에서 analysis_document(JSON)로 교체된다
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
  "extractorVersion": "v0",            // 문자열. 룰 버전을 붙일 수 있게
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

### T2b ✅ — 보고서 스키마 교정

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

### T2c ✅ — 분석 문서를 Markdown에서 JSON으로 교체 (2026-07-31, 59 tests)

**발단**: 2026-07-31 AI 팀원 통보 — *"`.md` 산출물은 변환 산출물이다. raw JSON에서 변환하는 거라 JSON으로 스키마 교체 필요."* **PoC를 직접 읽어 확인했고 맞다.**

**근거 3가지**

1. `prompt_manifest.json`의 `p04-1`이 *"Output strict JSON only"*를 지시하고 반환 형태를 프롬프트에 못 박는다.
   ```jsonc
   { "overview": "3-5문장",
     "structure":       [ { "area": "...", "files": ["실제 경로"], "role": "..." } ],
     "decision_points": [ { "title", "file", "symbol", "why_it_matters", "related_teach" } ],
     "risks":           [ "..." ] }
   ```
2. **다운스트림도 JSON을 먹는다.** `poc-engine.js:106`(문제 선정 p04-3)과 `:457`(보고서 p04-6)이 `JSON.stringify(analysisDoc)`을 그대로 프롬프트에 넣는다. Markdown으로 저장하면 우리가 되파싱해야 한다.
3. Markdown은 어디에도 없다. `app/analysis.html:189`가 그 JSON을 HTML로 그리는 것뿐이다 — 화면 렌더지 파이프라인 산출물이 아니다.

**같이 옮겨야 할 후처리** (`poc-engine.js:90`) — 이게 환각 방지 장치라 스키마에 남는다.

```
LLM은 {file, symbol}만 준다. symbol = 소스에서 문자 그대로 복사한 코드 한 줄.
  → CodeFragment.locateSymbol()이 실제 파일에서 그 문자열을 찾아 줄 번호를 산정
  → 못 찾으면 valid=false. 화면에 "근거 무효"로 표시하고 근거로 쓰지 않는다
```

**줄 번호를 LLM이 세지 않는 이유**: 지어낸 위치를 근거로 보여주면 "코드 파편 = 근거"라는 전제가 무너진다. 매니페스트가 *"줄 번호는 우리가 그 문자열을 찾아 직접 산정한다 — 네가 지어낸 번호는 쓰지 않는다"*로 명시한다.

**대상**

| 파일 | 무엇 |
|---|---|
| `schemas/analysis.py` | `DocumentArea` · `DecisionPoint` · `AnalysisDocument` 신설, `analysis_document_markdown: str` → `analysis_document: AnalysisDocument` |
| `engines/stub.py` | 스텁 문자열 → 빈 구조체 |
| `tests/test_engines.py` | 2곳 |
| `openapi.json` | 재생성 |

```python
class DecisionPoint(BaseSchema):
    title: str
    source_path: str
    symbol: str                              # LLM이 소스에서 그대로 복사한 한 줄
    line_start: int | None = None            # symbol을 찾아 우리가 산정. 못 찾으면 None
    line_end: int | None = None
    why_it_matters: str
    related_teach_id: str | None = None
    evidence_valid: bool                     # symbol을 실제 소스에서 찾았는지
```

**`decision_points`와 `problems`의 관계**: `decision_points`는 후보 풀이고 `problems` 3개는 그중에서 p04-3이 고른 것이다. 겹치지만 같지 않다 — 탈락한 지점도 매니저가 "왜 이 지점을 골랐나"를 판단하려면 문서에 남아야 한다.

**백엔드에 나가는 것**: B-5(컬럼 타입 변경) 1건. **이슈 #31의 "신설 테이블·컬럼 없음"과 ✅ 확정 Q2-1을 정정해야 한다.**

**DoD**: `openapi.json`에 `AnalysisDocument` 스키마가 나오고 `analysisDocumentMarkdown`이 사라진다. 테스트 통과. 백엔드 통지문 발송.

**완료 확인** — `AnalysisDocument`·`DecisionPoint`·`DocumentArea` 3개가 스펙에 나가고 `AnalysisResult.required`가 `analysisDocument`로 바뀌었다. 59 tests.

⚠️ **`evidenceValid=false`면 줄 번호가 비어야 한다는 제약은 OpenAPI로 표현되지 않는다.** pydantic validator에만 있다. 백엔드에는 산문으로 전달했다(이슈 #31 B-5).

`schemas/report.py`의 `analysis_documents: list[{kind, content}]`는 **loose dict 그대로 둔다** — 문서 4종이 각각 무엇인지 미확정이다(U-3). `content`에 `AnalysisDocument`가 그대로 들어가면 된다.

---

### T3 ✅ — 세션 턴에 점수 필드 추가 (질문 생성 제거는 T8과 함께)

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

### T3b ✅ — `aiUsage` 스키마 확정

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

### T4 ✅ — 교안 분석 엔드포인트 신설

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

**1차 완료 (T2·T2b 반영분).** `/analyses`·`/reports` 계약이 스펙에 들어갔다. `Problem.stages`의 `minItems/maxItems: 4`, `ProblemStage.hints`의 `2`가 그대로 나가 백엔드가 동결 구조를 코드 없이 읽는다.

**2차는 T3·T4를 끝내고 한 번에 한다.** 원래 "T2~T4를 다 끝내고 한 번"이었지만 1차를 당겼다 — T3(`/sessions`)·T4(`/curricula`)는 **다른 엔드포인트**라 이번 분을 다시 고치게 하지 않고, C-3에 막힌 T3b를 기다리면 확정된 계약까지 같이 묶이기 때문이다. 전달할 때 범위를 명시한다: **"분석·보고서 확정, 세션·교안은 추가 예정."**

이슈 #31의 완료 조건에 "AI가 `schemas/` 확정 후 `openapi.json` 갱신·공유"가 들어 있다.

---

### T6 ✅ — P02 규칙부 이식 (2026-07-31, 64 tests)

**T6만 진짜 이식이다** (Python → Python). T7은 JavaScript → Python **포팅**이라 성격이 다르다.

**결정: vendor 방식.** 원본 12개 `.py` + 데이터 19개 `.json`을 **한 줄도 안 고치고** `app/engines/analysis/vendor/`에 복사하고, 우리 소유의 `rules.py`가 감싼다. 이유는 팀원이 그 파일들을 계속 고치는 중이라 — 패키지 상대 import로 바꿔놓으면 갱신 때마다 병합이 된다. 원본 유지하면 **복사 한 번**이다. 기준 커밋·갱신 절차는 `vendor/SOURCE.md`.

```
app/engines/analysis/
  vendor/       팀원 소유. 절대 안 고침. 갱신 = 통째로 덮어쓰기 (기준 커밋 15b02fb)
  rules.py      우리 소유. ZIP 안전 해제 → scan/score 호출 → finding을 우리 어휘로
```

**실측** — `app/` 자체를 스캔: 25파일 → finding 10개, hub `common.py`. JSON 데이터도 `__file__` 상대경로로 정상 로드. 코드 수정 0.

**밝혀진 것 — `Problem` 스키마와 안 맞는 곳 5개**

| # | 내용 | 처리 |
|---|---|---|
| 1 | **finding에 줄 번호가 없다**(파일 단위) | T7이 `{file, symbol}` → `locateSymbol`로 산정. T2c의 그 메커니즘 |
| 2 | `priority`가 PoC는 문자열(`"최우선"`), 우리는 float | `rank_score`(3축 가중합)를 쓴다 |
| 3 | **`source_path`가 `None`인 후보가 있다** | `repeated-pattern:duplicate-definition`은 여러 파일에 흩어져 단일 경로가 없다. **버리지 않고 넘긴다** — 대표 파일 선택은 LLM 선정의 판단이다 |
| 4 | `problem_type` 5종이 finding에 없다 | `id` 접두사로 매핑(`rules.py:_PROBLEM_TYPE_BY_PREFIX`). `REQUIREMENT_IMPL`·`EXTERNAL_INTEGRATION`은 룰이 안 만든다 |
| 5 | `extractor_version`이 PoC에 없다 | vendor의 `.py`+`.json` 전부를 해시. **데이터 파일이 결과를 바꾸므로 코드만 해싱하면 안 된다** |

⚠️ **3번이 첫 실행에서 바로 나왔다** — 상위 3개 중 2개가 `source_path: None`이었다. 룰만으로 상위 3개를 뽑으면 2개가 버려진다는 뜻이고, T7의 LLM 선정이 선택이 아니라 필수라는 근거다.

**아직 안 한 것 — `engine_mode="real"` 배선.** 룰 결과에는 `stages`·`codeSnippet`이 없어 `AnalysisResult` 검증을 못 통과한다. **지금 꽂으면 `/analyses`가 500이 된다.** T7에서 LLM이 스키마를 채울 수 있게 됐을 때 한 번에 배선한다. 그때까지 검증 수단은 `tests/test_rules.py`.

**보안 — `rules.py`가 막는 것**: Zip Slip(`../` 경로 탈출), 심볼릭 링크, ZIP 폭탄(항목 20,000개·해제 500MB 상한). 학생이 올린 ZIP이라 항목 이름을 신뢰할 수 없다. 임시 디렉터리는 빠져나갈 때 삭제 — 코드 원문을 남기지 않는다.

**이벤트 루프 함정은 이미 피해져 있다** — `jobs.run_analysis`가 `def`(async 아님)라 Starlette이 threadpool로 돌린다. **`async def`로 바꾸지 말 것.**

<details><summary>원래 계획 (참고)</summary>

**출처**: `../ai_poc/poc_full` 의 `.py` 12개 (기준 커밋 `15b02fb`)

```
cognition/two_tier_scan.py                  2단계 스캔
judgment/idiom_filter.py                    관용구 걸러내기
judgment/importance_rank.py                 후보 우선순위
judgment/isolation_classifier.py  + _hook   고립도 분류
judgment/score_findings.py                  후보 점수
judgment/subrubric.py             + _hook   세부 루브릭
judgment/tier_b_hook.py                     2차 판정
judgment/tier_b_suppression_filter.py       2차 억제
feedback/reflection_signal.py     + _hook   반성 신호
```

**난이도**: 낮음 — 순수 Python stdlib이라 거의 그대로 import된다.

**방법**

1. `app/engines/analysis/` 아래로 옮긴다
2. `webtool_driver.py`가 Pyodide용 진입점이니 참고만 하고 서버용 호출부를 새로 쓴다
3. `AnalysisEngine` Protocol에 맞춰 `analyze(request, zip_bytes) -> dict` 하나로 감싼다
4. 출력이 `Problem` 스키마(문제·근거)에 맞는지 확인. **`stages`는 T7이 채운다** — T6 단계에서는 문제 후보까지만

**함정**: CPU 작업이다. `async def` 안에서 동기로 돌리면 이벤트 루프가 막혀 **문답 중인 학생까지 굳는다.** `def`(threadpool)로 두거나 `run_in_executor`로 뺀다.

**T6이 끝나도 `/analyses`는 완성이 아니다.** 룰이 후보를 좁히고 그 위에서 LLM(p04-3)이 고르는 2단계 구조라, T7이 붙어야 문제 3개가 확정된다. T6 단계에서는 `engine_mode`를 나눠 후보 목록만 확인한다.

</details>

---

### T7 — P04 LLM 스테이지 이식 ← **다음 작업**

**🔜 백엔드에 고지할 것 (B-6) — 선정 근거 필드 1개. p04-3 실동작으로 모양이 확정됐다.**

처음엔 `rules.py`의 `selection_evidence`(subrubric 3축 원점수 + `rank_evidence` 가중치·동점 처리 깊이)를 어떻게 실어보낼지 고민했는데, **p04-3이 topic마다 `rationale`을 낸다.** 매니저가 읽을 "왜 이 문제인가"는 숫자 블롭이 아니라 이 문장이다.

```
"설정값에 따라 다른 분석 엔진 구현체를 주입하는 의존성 주입 패턴의 이해도를
 검증할 수 있으며, 코드의 엔진 모듈과 교안 t1이 직접 연결되는 지점임"
```

→ **요청은 `assessment_problem`에 `selection_rationale TEXT` 한 컬럼**이면 된다. 숫자 근거는 로그에만 남긴다(재현·튜닝용이지 매니저용이 아니다).

**언제 보내나**: p04-4까지 붙여 `Problem` 전체 모양이 확정되면 B-5와 함께. 지금 따로 보내면 백엔드가 두 번 고친다.

**✅ 함께 해소됨 — `source_path: None` 후보 처리.** 룰 후보는 **선택지가 아니라 맥락**이다. 매니페스트가 `code_ref.file`을 "분석 문서에 등장한 파일"로 제약하고 환각 방지는 symbol 검증이 한다. 후보가 문제로 승격되는 구조가 아니라서 대표 파일을 고를 필요가 없다.

### teaches와 문제 수 (2026-07-31 정리)

**`teaches`는 교안 분석 산출물 중 강사·매니저가 고른 최대 3개다.** 모든 학생에게 동일하고, 프로젝트 안내사항으로 학생에게도 공지된다("클래스·상속·캡슐화를 만족하는 프로젝트를 제출하세요"). 요구사항과 성격이 겹치되 요구사항이 더 세부적이다.

p04-3이 *"각 topic은 서로 다른 teach"*를 요구하므로 **문제 수 상한 = 선택된 teach 수**다. 이건 사고가 아니라 강사의 의도다.

**진짜 위험은 커버리지다** — teaches는 같은데 그게 학생 코드에 있는지는 다를 수 있다. 강사가 3개를 골라도 학생이 2개만 구현했으면 문제가 2개가 되고, **만점 분모가 학생마다 달라진다**(40 vs 60).

**→ 폴백은 "teach 없는 일반 문제"로 채운다 (2026-07-31 결정).**

**teach 세트를 바꾸지 않는 것이 조건이다.** 강사가 (클래스, 상속, 캡슐화)로 정하면 **프론트도 그 세 개를 보여준다.** 부족분을 같은 teach 재사용으로 메우면(클래스, 클래스, 캡슐화) 화면과 실제가 어긋난다.

**근거: 미구현 사실은 이미 `requirementResults`가 `F`로 보고한다.** 그걸 점수 분모에도 반영하면 같은 사실을 두 번 벌하고, "이해도 점수"에 "구현 여부"가 섞인다.

```
1차   teach 앵커 문제 — 서로 다른 teach 하나씩       ← 기본
2차   부족분은 teach 없는 일반 문제                  ← 분석 문서의 decision_points에서 뽑음
3차   그래도 부족하면 shortfall 남기고 진행           ← 물을 지점 자체가 없는 소규모 제출물
```

**2차는 LLM을 다시 안 부른다.** p04-1의 `decision_points`가 이미 코드에 앵커된 채로 검증까지 끝나 있다 — 1차에서 안 쓰인 것을 순서대로 쓴다. 매니페스트도 안 건드린다.

**⚠️ 감수하는 것 (나중에 재검토)**: 일반 문제에는 teach가 없어 **보고서의 "교안 복습 위치 지목"이 그 문제엔 안 붙는다.** 지금은 임시안이고, 미구현 teach를 어떻게 다룰지(예: "왜 안 썼는가"를 묻기 — 단 코드 근거가 없어 우리 원칙과 충돌)는 나중에 다시 본다.

### OWN_COMMIT은 별개 모드다 (나중)

`appliedScope`가 `OWN_COMMIT`이 되면 **요구사항도 teaches도 없다고 가정한다**(팀 결정, 2026-07-31). LLM이 코드만 보고 문제 3개를 추천하는 방식이다.

→ **p04-3의 teach 앵커 규칙이 통째로 안 맞는다.** 프롬프트 변형이 필요하고, 그 모드에서는 **룰 후보(`rules.py`의 `rank_score`)가 맥락이 아니라 선정 근거 본체**가 된다. 지금 만드는 것과 다른 경로라 **지금 대비하지 않는다** — A-8이 정해질 때 착수한다.

`TOTAL`인 현재 모드에서는 같은 팀이 같은 레포를 보므로 **팀원끼리 문제 3개가 동일하다.**

**결정된 것 (2026-07-31)**

| | |
|---|---|
| **키** | 팀원 8개를 `.env`의 `NVIDIA_API_KEY_1`~`_8`로. `NvidiaKeyPool.from_env(prefix="NVIDIA_API_KEY_")`가 코드 수정 없이 잡는다. **팀원 브라우저 PoC는 키 1개**(사용자 붙여넣기 → Worker 헤더)이고 풀링은 서버 계보(`_legacy/.../nvidia_key_pool.py`)다 |
| **`prompt_manifest.json`** | **vendor 그대로.** JSON이고 계약이다 — 문자열을 코드에 박지 않는다 |
| **`scoring-config.js`** | **Python 상수 모듈(`scoring.py`)로 옮긴다.** JS라 vendor 불가. JSON으로 값만 뽑으면 루브릭 근거 주석이 날아가는데, 축 순서 사고를 겪은 파일이라 그 주석이 자산이다. **우리가 관리하는 유일한 사본** — 팀원이 루브릭을 고치면 우리도 손대야 한다 |
| **기본 모델** | **용도별 3개**(팀원 결정, 2026-07-31). 요청의 `modelCode`가 오면 그쪽이 이긴다. 라이브 카탈로그 102종에서 ID 확인함 |
| **착수 순서** | **p04-1 먼저.** T2c에서 스키마를 이미 확정했고 다음 스테이지들이 이 JSON을 입력으로 받는다. p04-1이 되면 LLM 경로 전체(키 풀 → 호출 → JSON 파싱 → `ai_usage` 기록)가 한 번에 검증되고, 나머지는 같은 배관에 프롬프트만 갈아끼우는 일이다 |

**출처**: `../ai_poc/poc_full/app/` — 전체 1,175줄

| 파일 | 줄 | 역할 |
|---|---|---|
**기준 커밋**: `15b02fb` (2026-07-30). 축 순서 정정 + `hintMode` 토글이 들어온 시점이다.

| 파일 | 역할 |
|---|---|
| `prompt_manifest.json` | p04-1~7 프롬프트·파라미터. **계약이다. 문자열을 코드에 박지 않는다** |
| `scoring-config.js` | 축×값 루브릭 + 임계값 + 힌트 상한 + **`hintLadder` 강도 spec** + `hintMode`. 선언적이라 그대로 옮김 |
| `poc-engine.js` | 3문제 × 4레벨 루프 + 레벨별 즉시 채점. `:340`이 동결/적응형 분기 |
| `hint-ladder.js` | `freezeQuestionSet()` 질문 4개 동결 + `generateHint()` 힌트 1개 생성 |
| `question-guard.js` | 질문·힌트에 선택지가 섞이는 것 차단 → 재생성 |
| `code-fragment.js` | `{file, symbol}`의 symbol 문자열을 실제 파일에서 찾아 **줄 번호를 산정**하고 파편 추출. 못 찾으면 `valid=false` (T2c) |
| `requirements.js` | 요구사항 P/F 판정 |
| `llm-stage.js` | 매니페스트 스테이지 1개 호출 공용 경로 |

**스테이지 구성** (전부 LLM. 매니페스트 순서는 1·2·3·4·**7**·5·6)

```
분석 배치
  p04-1  코드 분석 문서                  입력: teaches + findings + code. 출력은 JSON (T2c)
  p04-2  요구사항 P/F 판정
  p04-3  문제 3개 선정
  p04-4  L1~L4 질문 생성                 문제당 1콜. 어느 모드든 동결된다
  p04-7  힌트 생성                       동결형이면 여기서 문제당 8콜(단계 4 × 힌트 2)
                                         적응형이면 안 돈다
런타임
  p04-5  답변 채점 (단계 1개, 0~5점)      턴당 1콜
  p04-7  힌트 생성                       적응형일 때만. 오답 확정 직후 1콜
세션 후
  p04-6  보고서
```

**`p04-7`은 하나의 프롬프트를 두 모드가 공유한다.** `{attempts_block}`에 실제 시도가 있으면 적응형, `"(아직 답변 없음)"`이면 동결로 **모델이 판단**한다. 스테이지를 둘로 나누지 않는다.

**적응형 힌트의 입력**: `formatAttemptsBlock()`이 질문·답변·채점 점수뿐 아니라 **채점기가 판정한 `missing`/`evidence`까지** 넣는다. 학생이 뭘 빠뜨렸는지를 힌트가 겨냥할 수 있는 이유다.

**LLM 클라이언트** ✅ **T7a에서 완료.** `app/llm/vendor/`(무수정) + `app/llm/client.py`(우리 소유). Worker 프록시(`worker/nvidia-proxy.js`)는 브라우저 제약의 산물이므로 옮기지 않았다.

### 용도별 기본 모델 (2026-07-31 확정)

| 용도 | `modelCode` | 팀원 실측 | 우리 설정 키 |
|---|---|---|---|
| 코드 분석 | `nvidia/nemotron-3-ultra-550b-a55b` | **2시간** — 목표는 30~60분 | `MODEL_CODE_ANALYSIS` |
| AI 문답 | `mistralai/mistral-medium-3.5-128b` | 3분 안팎 — 개선 필요 | `MODEL_CODE_SESSION` |
| 교안 분석 | `minimaxai/minimax-m3` | 25분 — 강사가 수업 전까지만 끝나면 되므로 허용 | `MODEL_CODE_CURRICULUM` |

**언제든 바뀐다.** 나중에 프론트에서 operator가 고를 수도 있어서 코드가 아니라 설정에 둔다. 우리 엔드포인트 3종과 1:1이라 매핑이 그대로 성립한다.

**⚠️ 코드 분석 2시간이 가장 큰 미해결 과제다.** 줄일 지렛대 3개, 순서대로:
1. **병렬화** — 질문 생성(문제당 1콜 × 3)과 힌트(× 24)는 서로 독립이다. 키 8개로 모델당 320 RPM이라 여유가 크다. **효과가 가장 크다. T7c에서 한다**
2. **스테이지별 모델 분리** — 판단이 무거운 곳(p04-3 문제 선정)만 550b, 정형 출력(질문·힌트)은 빠른 모델로. 지금 구조가 이미 스테이지 단위라 붙이기 쉽다
3. 입력 축소 — 12,000자 truncation이 이미 걸려 있어 여지가 작다

**1번 먼저, 2번은 그다음.** 동시에 하면 어느 쪽이 효과였는지 못 가린다.

**T7a 실측 (2026-07-31)** — 이 모델을 쓰는 이상 T7b가 반드시 알아야 하는 것:

| 관측 | 함의 |
|---|---|
| `content`(답)와 `reasoning_content`(사고 과정)가 **항상 둘 다 온다** | **`reasoning_content`를 답으로 폴백하면 안 된다.** 출력이 잘리면 사고 과정만 남는데 그걸 답으로 넘기면 JSON 스테이지가 "모델이 JSON을 안 줬다"로 오진한다. 실제로 `max_tokens=16` 실측에서 발생했고 `client.py`가 이제 `CONTEXT_OVERFLOW`로 막는다 |
| `"OK"` 2글자에 **출력 36토큰** | 추론형이라 답 길이와 무관하게 사고 토큰을 태운다. `max_tokens`를 매니페스트 값(2400 등)보다 줄이면 안 된다 — 사고가 예산을 먼저 다 쓴다 |
| 지연 0.5~3.1초 | 팀원 실측 1.5~3.9초와 일치. 배치 30콜이면 순차 1~2분 |

**⚠️ 팀원 주석의 *"reasoning_content 경유, 폴백 정상 동작"*은 P03의 tool_calls 경로 얘기다.** 일반 chat에는 해당 없다 — 그대로 옮기면 위 오진이 난다. JSON 모드에서도 `step-3.7-flash`는 `content`에 제대로 넣는 것을 실측했다(폴백은 구버전 `step-3.5-flash`용이었다).

### T7b 실측에서 나온 것 (2026-07-31)

**① 매니페스트의 `max_tokens`는 "답에 필요한 길이"다.** 추론형 모델은 같은 예산에서 사고까지 하므로 답에 닿기 전에 끝난다. p04-1 실측: 답 3,219자(~1,100토큰)인데 완료 토큰 5,840 — 매니페스트 값 2,400으로는 **두 번 다 잘렸다**(1차 `INVALID_JSON`, 2차 `CONTEXT_OVERFLOW`).

**모델 속성이지 프롬프트 속성이 아니므로 매니페스트를 고치지 않는다.** `client.budget_for()`가 모델별 배수를 곱하고, 배수를 모르는 모델은 **잘리면 `stages.call()`이 예산을 두 배로 올려 재시도**한다. nemotron은 콜 하나에 오래 걸려서 실측으로 표를 채우는 비용이 크기 때문이다.

**② LLM이 `symbol`을 여러 줄로 준다.** 매니페스트는 "한 줄"을 요구하지만 실제로는 함수 시그니처 전체를 붙여 왔다. 줄 단위 매칭만 하면 통째로 버려진다 — `fragments.py`가 **첫 줄로 한 번 더 시도**해 살린다.

**④ 원본 결함 3건을 포팅하면서 고쳤다.** `question-guard.js`의 정규식 2건(오타 `[A-DA-D]`, "A와 B 중"이 각 항을 한 어절로만 봐서 *"동기 방식과 비동기 방식 중"*을 통과시킴), `scoring-config.js`의 재시험 기준(L1만 → L1·L2). **팀원 원본도 고쳐야 실측이 같아진다** — 목록은 `../output_docs/미결_논의사항.md` §1c.

**③ 블록 끝 추정에 괄호 깊이가 필요하다.** 여러 줄 시그니처는 닫는 `)` 줄의 들여쓰기가 시작줄과 같아서, 들여쓰기만 보면 **시그니처 한복판에서 끊긴다.** 괄호가 닫힐 때까지 이어붙인 뒤 들여쓰기 규칙으로 넘어간다. 이건 원본 `code-fragment.js`에 없던 개선이다(원본 주석도 이 한계를 인정하고 있다).

**레이트리밋**: `(키, 모델) 쌍당` 분당 40회다(`nvidia_key_pool.py:3~6`). 키를 N개 풀링하면 모델당 N×40. **키 풀 자체를 서버로 옮긴다.** 짧은 초과는 내부 큐로 흡수하고, 한계를 넘으면 `RATE_LIMITED` + `retryAfterSec`로 돌려준다. 유료 전환 시 사라질 제약이므로 **여기에 아키텍처를 맞추지 않는다**(§규모).

**잘라낼 것**: Supabase 저장(`shared/db.js`), Worker 프록시, IndexedDB·sessionStorage, UI·타이머, 브라우저 pdf.js.

> Supabase 관련 보안 주의: 팀 공용 프로젝트가 open signup + RLS read-all이라 cross-tenant 위험이 있고, 팀원이 **브라우저 PoC 한정으로** 수용했다. 제품 결정이 아니다. 이식 과정에서 Supabase 클라이언트·스키마·인증 흐름을 따라 옮기지 않는다. 애초에 FastAPI는 저장하지 않으므로 옮길 것이 없어야 정상이고, Supabase 호출이 필요해 보이면 설계가 틀린 것이니 멈추고 재검토한다.

### ✅ 축 순서 — 해결됐다 (2026-07-30, 팀원 커밋 `fc80044`·`15b02fb`)

전에는 PoC가 L3=반례, L4=대안이라 이식 중 교환이 필요했다. **요청서(`../qna/2026-07-30/poc-axis-order-fix.md`)대로 팀원이 고쳤다.**

```js
L3_대안:     order 3, label "대안 비교",      values 6단계 = 대안 비교 루브릭
L4_반례한계: order 4, label "반례 대응·한계",  values 6단계 = 반례 루브릭
axisWeights = { …, L3_대안: 1.0, L4_반례한계: 1.0 }
prompt_manifest p04-4 → "L1_코드기술, L2_설계논리, L3_대안, L4_반례한계이다"
```

**루브릭 텍스트까지 함께 옮겨졌고 키 이름도 우리가 지정한 문자열 그대로다.** 이식할 때 뒤집을 것이 없다. `scoring-config.js`에 되돌리는 방법까지 주석으로 남아 있다.

⚠️ 다만 **이슈 #31의 옛 댓글에 L3=반례로 적힌 표가 남아 있다.** 백엔드가 그걸 보고 구현하지 않도록 경고 배너를 달았다.

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
code_analysis.analysis_document (JSONB)                 분석 문서 — B-5로 타입 변경 요청 중(T2c)

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

### 🔴 질문·힌트 생성 시점 — 혼합 모드 (2026-07-31 PM 확정)

**아래 절은 낡았다.** "전부 동결"이 아니라 단계별로 갈린다.

```
L1  질문 동결 · 힌트 동결      분석 배치
L2  질문 동결 · 힌트 동결      분석 배치
L3  질문 적응형 · 힌트 적응형   세션 중 (L2 통과 후, 직전 답변·채점 근거를 보고 생성)
L4  질문 적응형 · 힌트 적응형   세션 중 (L3 통과 후)
```

**콜 수**

```
분석 배치   문서1 + 요구사항1 + 선정1 + 질문3(문제당 L1·L2 한 번) + 힌트12  = 18콜
            (전면 동결이었으면 30콜 — 배치는 오히려 가벼워졌다)

세션 중     L1 채점3 · L2 채점3 · L3 질문1+채점3+힌트2 · L4 질문1+채점3+힌트2
            = 문제당 최대 18 → 3문제 54콜
```

**🔴 미해결 — 턴당 대기 시간.** L3·L4 턴은 **채점 → 질문 생성**이 연달아 돌아야 다음 화면이 뜬다. 문답 기본 모델 `mistral-medium-3.5`가 팀원 실측 3분이라 **한 턴에 6분**이 된다. `timeLimitSec: 2400`(40분)에 12질문이 산술적으로 안 들어간다. 완화 수단은 ① 질문 생성을 채점과 병렬로(채점 근거 없이 답변만으로 생성) ② 문답 모델을 빠른 것으로 교체 — **②가 근본이다. PM에게 알려야 한다.**

**스키마 반영 완료** — `ProblemStage.question_text: str | None`, `hints`는 축별 규칙으로 검증한다.

```
L1·L2   questionText 필수 · hints 정확히 2개([1,2] 순서)
L3·L4   questionText null · hints 빈 배열
```

느슨하게 "0개 또는 2개"로 풀지 않은 이유: 그러면 L1에 힌트가 안 와도, L3에 힌트가 와도 통과한다. 전자는 학생이 힌트 없이 재답변하게 되고 후자는 적응 생성분을 덮어쓴다 — **둘 다 에러 없이 동작만 틀린다.**

⚠️ **이 규칙은 `openapi.json`에 안 나온다.** 축별 조건부 제약은 OpenAPI로 표현이 안 돼서 `minItems`가 사라지고 description만 남았다. 백엔드에는 산문으로 전달해야 한다.

**팀원 프롬프트 요청** — p04-4를 L1·L2 2개만 생성하도록, 그리고 L3·L4를 직전 문답 전문 + 채점 근거로 생성하는 스테이지 신설. **고지 완료(2026-07-31).** 그때까지 `questions.py`는 4개를 받아 2개만 쓴다(`_normalize`가 남는 축을 버린다).

<details><summary>낡은 절 — 전면 동결 시절의 근거 (2026-07-30까지)</summary>

### 질문·힌트 생성 시점 — 분석 배치에서 전부 동결한다

```
분석 배치   문제 3개 + 문제별 L1~L4 질문 12개 + 단계별 힌트 2개씩 24개
런타임      채점만
```

**질문과 힌트는 학생 답변을 보기 전에 만들어져 동결된다.** 근거: 답변을 보고 힌트를 만들면 학생마다 힌트가 달라져, "몇 번째 힌트에서 통과했는가"가 학생 실력이 아니라 생성 결과의 차이를 재게 된다. 같은 문제를 받은 두 학생은 글자 단위로 같은 질문과 같은 힌트를 받아야 한다.

PoC 구현이 이미 그렇다 — `poc-engine.js:110`이 분석 단계에서 `HintLadder.freezeQuestionSet()`을 호출하고, `hint-ladder.js:86,94`가 `frozen_at`을 찍는다. 세션 루프(`poc-engine.js:218`)는 동결된 `lvl.hints[hintsUsed - 1]`을 꺼내 쓸 뿐이다.

그래서 **`AnalysisResult`의 `problems[].stages[]`는 4개(L1~L4)이고 각 stage에 `hints` 2개가 실린다.**

세션 중 LLM 호출: **채점 1콜뿐.** 질문 생성도 힌트 생성도 세션 중에 없다.

</details>

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

**단위는 "제출 1건"이다** — 학생 1명(또는 팀 1개)의 코드 1회 분석 + 세션 1개.

**① 호출 수 — 설계 사실. 유료 API로 가도 같다**

```
동결형   분석 30 + 문답 12~36 + 보고서 1  =  43 ~ 67콜
적응형   분석  6 + 문답 12~108 + 보고서 1  =  19 ~ 115콜

분석 30콜의 내역 (동결형)
  p04-1 분석 문서 1 · p04-2 요구사항 1 · p04-3 문제 선정 1
  p04-4 질문      문제당 1 × 3 =  3
  p04-7 힌트      문제당 8 × 3 = 24     ← 단계 4 × 힌트 2, 레벨마다 개별 호출
```

⚠️ **예전에 적어둔 "팀당 6콜"은 틀렸다.** 힌트가 `p04-4`에 묶여 있다고 본 것인데, `p04-7`이 별도 스테이지이고 `hint-ladder.js:103~118`이 레벨마다 개별 호출한다.

동결형은 폭이 좁고(43~67), 적응형은 학생 실력에 따라 6배 흔들린다(19~115).

**② 속도 제한 — 지금 무료 티어 사정. 설계 근거로 쓰지 않는다**

```
NVIDIA 무료 티어   (키, 모델) 쌍당 분당 40회      ← 키당이 아니다
키 8개 풀링        모델당 320 RPM
유료 전환          사라지거나 크게 오름
```

`nvidia_key_pool.py:3~6`이 근거다. **"RPM 때문에 X를 못 한다"는 문장을 쓰지 않는다** — 임시 조건으로 아키텍처를 못 박으면 유료 전환 때 근거가 통째로 무효가 된다.

시연 규모(30명 제출 = 900콜)면 배치 약 3분이라 제약이 아니다.

**③ 유료 전환 시**

호출 수가 아니라 **토큰 수**가 비용이다. 힌트 콜은 프롬프트가 짧아 호출 수 비중만큼 비싸지 않다. `ai_usage`가 정확히 이걸 재려고 있고, 단가를 Spring이 곱하는 구조라 **AI 쪽은 손댈 게 없다**(C-3).

**진짜 병목은 컨텍스트 길이다.** 코드를 12,000자로 잘라 프롬프트에 넣으므로(`requirements.js:18`) 큰 레포는 잘린 코드로 요구사항이 판정된다.

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
| D11 | **질문은 분석 때 미리 만들어 동결한다. 힌트 방식은 확정 전** | 질문 동결은 어느 모드든 유지된다(팀원 `Readme.md` D4 개정: "질문 동결은 그대로다 — 바뀐 건 힌트뿐이다"). 힌트는 아래 참조 |

> **D11 보충 — 힌트 방식이 확정 전이다 (2026-07-30).**
>
> ```
> 동결형   분석 배치에서 힌트를 미리 만들어 둔다. 세션 중 LLM 호출은 채점뿐
> 적응형   학생 오답 + 채점 근거(missing/evidence)를 입력으로 재질의를 그 자리에서 만든다
> ```
>
> **현재 유력한 방향은 적응형이다** (PM 판단: 고정 힌트는 학생이 실제로 뭘 틀렸는지와 무관하게 나가 겨냥이 빗나간다). 팀원 PoC는 **둘 다 구현**해두고 `POCScoring.hintMode`로 토글하며 실측 비교 중이다. 기본값은 `"frozen"`인데, 이는 결론이 아니라 **우리가 준 계약이 동결이라 거기 맞춘 것**이다.
>
> **비교 가능성 근거가 이동했다** — "힌트 텍스트가 동일함"에서 **"사다리 단계 수(레벨당 2회)·강도 정의·점수 상한(5/4/3)이 동일함"**으로. `scoring-config.js`의 `hintLadder`가 힌트1="관점 되짚기", 힌트2="범위 좁힘"으로 **강도를 고정**하고 두 모드가 이 spec을 공유한다. 적응형은 겨냥 대상만 바꾼다. 그래서 "2번째 시도에서 통과"가 여전히 같은 뜻이다.
>
> **적응형으로 확정되면 우리 쪽 변경**
> 1. `AnalysisResult`에 `hintMode` 필드, `ProblemStage.hints`의 `min_length=2` 강제를 모드별 validator로
> 2. `SessionView`에 지금 보여줄 힌트를 담을 필드 신설 (`TranscriptTurn.hint_text`는 기록용이라 별개)
> 3. 세션 중 LLM 호출이 채점 + 힌트로 늘어난다
>
> **백엔드 쪽은 오히려 줄어든다** — 36행 선생성 로직이 사라지고 재질의 때 1행씩 INSERT. DDL 추가·신설 없음. 보류 요청 2건은 이슈 #31 본문 `⏸` 절.
>
> **힌트 가드는 PoC에 이미 있다** — `hint-ladder.js:190`이 `QuestionGuard.check()`를 힌트에도 걸고, 위반 시 재생성, 계속 실패하면 `fallbackHint()` 결정론적 문장으로 대체한다. **힌트 미생성이 구조적으로 불가능**하므로 `attempt_no IN (2,3) AND hint_text IS NOT NULL` CHECK가 항상 만족된다. 다만 폴백 사용 여부(`generated: false`)를 남길 자리가 DB에 없다 — 확정 때 같이 논의.
| D12 | 세션 총점·축 평균을 AI가 만들지 않는다 | 점수는 매 턴 `problem_stage`에 저장돼 있다. 집계는 Spring이 SQL로 하면 되고, LLM이 아니면 못 만드는 것만 `/reports`에 담는다 |
| D13 | `codeSnippet`을 AI가 보낸다 | `evidence_hash`가 code_snippet 기준 해시이고 해시를 AI가 만든다. Spring이 따로 잘라내면 줄바꿈·BOM 차이로 해시가 안 맞는다 |
| D14 | 값 이름은 DB 컬럼명을 그대로 쓴다 | 새 어휘를 만들면 백엔드가 "정의서에 없는 컬럼"으로 읽는다. 실제로 `callId`로 한 번 겪었다. **단 컬럼명이 사실과 다르면 컬럼명을 고친다 — `analysis_document_markdown`이 그 사례(B-5)** |
| D15 | 분석 문서의 원본은 JSON이고 Markdown은 렌더 결과다 | 다운스트림(문제 선정·보고서)이 JSON을 그대로 프롬프트에 넣는다. Markdown으로 저장하면 되파싱해야 한다. 사람이 읽는 화면은 이 JSON을 렌더한 것이다 (T2c) |
| D16 | LLM에게 줄 번호를 세게 하지 않는다 | LLM은 `symbol`(소스에서 복사한 코드 한 줄)만 주고 줄 번호는 우리가 그 문자열을 찾아 산정한다. 못 찾으면 `evidenceValid=false`로 남기고 근거로 쓰지 않는다. "코드 파편이 곧 근거"라는 전제를 지키는 장치다 |

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
