# AI 서비스 (FastAPI)

> 갱신: 2026-07-30

교육생이 제출한 코드를 분석해 **문답 문제를 뽑고**, 학생과 **문답을 진행하며 채점**하고, 끝나면 **보고서**를 내는 서비스. Spring Boot가 호출하는 내부 서비스다.

```
React(Frontend) ──▶ Spring Boot(Backend) ──▶ FastAPI(이 저장소) ──▶ NVIDIA LLM
                          │
                          └── DB (단일 소유자)
```

- React는 FastAPI를 직접 부르지 않는다. **FastAPI의 호출자는 Spring뿐이다.**
- **FastAPI는 DB를 갖지 않는다.** 결과를 응답으로 돌려줄 뿐이고 저장은 전부 Spring이 한다. 코드 원문도 임시 작업공간에만 두고 지운다.

---

## 1. 서비스 흐름

```
0. 교안 분석    교안 PDF → teaches 추출              교안당 1회. LMS 업로드 시점
1. 코드 제출    ZIP 또는 GitHub URL + 요구사항 + teaches 3개
2. 코드 분석    → 분석 문서 · 요구사항 P/F · 문제 3개 + 질문 12개 · 힌트 24개(동결)
                                                     제출 마감 후 배치. 1시간 예산
3. 문답         문제 3개 × 단계 L1~L4                실시간. 학생이 화면에서 대기
4. 보고서       문제별 점수 · 서술형 진단 · 교안 참조 · 재시험 대상
```

**셋의 시간축이 겹치지 않는다.** 교안 분석·코드 분석·문답이 동시에 도는 일이 없다.

### 문답 규칙

```
단계마다 답변을 0~5점으로 즉시 채점
  3점 이상 → 다음 단계
  3점 미만 → 동결된 힌트를 주고 같은 단계 재질의 (단계당 최대 2회)
            힌트 2회 소진 후에도 미달 → 그 문제 종료, 다음 문제의 L1로

힌트를 쓰면 그 단계의 점수 상한이 내려간다
  무힌트 5 / 힌트1 후 4 / 힌트2 후 3      ← 미확정값. 완주 30건 후 재보정
  confirmedScore = min(bestScore, 상한)
```

| 단계 | 무엇을 묻나 | DB `axis_score.axis_code` 대응 |
|---|---|---|
| L1 | 무엇을 하는 코드인가 | `CODE_UNDERSTANDING` |
| L2 | 왜 그렇게 했는가 | `DESIGN_LOGIC` |
| L3 | 다른 방법과 비교 (대안) | `ALTERNATIVE_COMPARISON` |
| L4 | 언제 깨지는가 (반례·한계) | `COUNTEREXAMPLE_RESPONSE` |

wire와 DB `problem_stage.axis_code`에는 **짧은 값 `"L1"`~`"L4"`**를 쓴다.

**채점이 세션 진행을 제어한다.** 점수가 다음 턴(힌트를 줄지, 다음 단계로 갈지, 이 문제를 끝낼지)을 결정하므로 매 턴 Spring으로 나가 저장된다.

### 질문·힌트는 언제 만드나 — 분석 배치에서 전부 동결

```
분석 배치   문제 3개 + 문제별 L1~L4 질문 12개 + 단계별 힌트 2개씩 24개
런타임      채점만
```

**질문과 힌트는 학생 답변을 보기 전에 만들어져 동결된다.** 답변을 보고 힌트를 만들면 학생마다 힌트가 달라져, "몇 번째 힌트에서 통과했는가"가 학생 실력이 아니라 생성 결과의 차이를 재게 된다. 같은 문제를 받은 두 학생은 글자 단위로 같은 질문과 같은 힌트를 받아야 한다.

**세션 중 LLM 호출은 채점 1콜뿐이다.** 질문 생성도 힌트 생성도 세션 중에 없다.

**적응형 힌트(학생 답변을 보고 그 자리에서 만드는 힌트)는 후속 예정이다.** 모듈이 개발 중이고 나중에 추가되지만, **현재 계약은 위 동결 기준**이다. 붙으면 턴당 호출이 2콜로 늘고, 두 방식의 점수를 나란히 비교할 수 없으므로 체크포인트 단위로 모드를 고정하게 된다.

---

## 2. 실행

```bash
# 최초 1회
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env        # 값을 채운다. .env는 절대 커밋하지 않는다

# 개발 서버
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload

# 테스트
./.venv/Scripts/python.exe -m pytest -q
```

Swagger UI: **http://127.0.0.1:8000/docs** — 여기서 엔드포인트를 직접 클릭 테스트할 수 있다.
기계용 스펙: **`openapi.json`** — Postman에 Import하면 바로 요청이 만들어진다.

**로컬 가드 켜기 (선택, 권장):** `git config core.hooksPath .githooks` 한 번 실행하면
커밋 전에 `tools/check_no_secrets.py`(D9 — NVIDIA 키 커밋 방지)와
`tools/lint_llm_calls.py`(D3/D4 — 인라인 프롬프트·LLM 우회 호출 차단)가 자동으로 돈다.
안 켜도 CI(`.github/workflows/ci.yml`)가 같은 두 검사를 강제한다.

> **워커는 1개로 유지한다.** job·세션 저장소가 인메모리라 `--workers 2` 이상이면 만든 프로세스와 조회 프로세스가 달라져 404가 난다. 시연 규모(동시 10~20명)에서는 제약이 아니다 — 병목은 FastAPI가 아니라 NVIDIA 무료 티어의 분당 40회다.

### 백엔드와 통신 테스트 (배포 없이)

Spring이 Railway에 떠 있으면 로컬 FastAPI를 터널로 노출해 확인한다.

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 다른 터미널 (cloudflared — 가입·토큰 불필요)
cloudflared tunnel --url http://localhost:8000
#   → https://xxxx.trycloudflare.com 발급 → 백엔드에 전달 (prefix /api/v0)
```

- `.env`의 `INTERNAL_API_KEY`에 값이 있으면 헤더 `X-Internal-Key`가 필요하다(health만 면제). 비우면 인증이 꺼진다.
- quick tunnel은 실행마다 URL이 바뀌고, 창을 닫거나 PC가 절전에 들어가면 끊긴다.
- 상세 절차: `../output_docs/AI-Backend_통신테스트_계획_2026-07-24.md`

### 계약이 바뀌면 openapi.json 갱신

```bash
./.venv/Scripts/python.exe -c "from app.main import app; import json,io; io.open('openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(),ensure_ascii=False,indent=2))"
```

---

## 3. API 명세

### 공통 규약

| 항목 | 값 |
|---|---|
| 경로 prefix | `/api/v0` — 서비스 버전이 아니라 "개발 단계 API"라는 성숙도 표시. 계약이 안정되면 양쪽이 함께 v1으로 올린다 |
| 필드 표기 | **camelCase** (`jobId`, `snapshotId`). 파이썬 내부는 snake_case로 쓰고 직렬화만 변환한다 |
| 동기/비동기 | 사람이 화면 앞에서 기다리면 동기, 아니면 202 + 폴링 |

**요청 헤더 3종**

| 헤더 | 값 | 용도 |
|---|---|---|
| `X-Internal-Key` | 공유 비밀 | 서비스 간 인증. `GET /api/health`만 면제 |
| `Idempotency-Key` | `submissionId:attemptNo` | 중복 요청 판별. 같은 키면 처음 만든 `jobId`를 그대로 반환하고 재분석하지 않는다 |
| `X-Trace-Id` | 추적 ID | Spring이 `analysis_job.trace_id`로 저장 |

**에러 형식** — 평탄 구조. `timestamp`·`path`는 쓰지 않는다.

```json
{
  "error": "INVALID_REQUEST",
  "message": "method=GITHUB_URL에는 source.repoUrl이 필요합니다",
  "retryable": false
}
```

`error`는 기계가 분기하는 코드 문자열, `message`는 사람이 읽는 설명, `retryable`은 Spring의 재시도 판단용이다.

**`analysisId`는 Spring이 발급한다.** AI는 만들지도 받지도 않고, Spring이 `jobId`로 연결한다.

**모델은 코드 문자열로 주고받는다.** 요청 `modelCode`(생략 시 서버 기본값), 응답 `aiUsage[].modelCode`. UUID(`model_id`)는 쓰지 않는다 — Spring이 `ai_model`에서 조회한다.

### 엔드포인트

| 그룹 | 메서드·경로 | 역할 | 방식 |
|---|---|---|---|
| 공통 | `GET /api/health` | 서비스 상태 | 동기 |
| 분석 | `POST /api/v0/analyses` | 코드 분석 요청 | 202 + 폴링 |
| 분석 | `GET /api/v0/analyses/{jobId}` | 분석 상태·결과 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/answers` | 답변 제출 → 채점 (다음 질문·힌트는 이미 동결돼 있다) | 동기 |
| 세션 | `POST /api/v0/sessions` | 세션 시작 → 첫 질문 | 동기 · **축소 예정** |
| 세션 | `GET /api/v0/sessions/{id}` | 세션 현재 상태 | 동기 · **축소 예정** |
| 세션 | `POST /api/v0/sessions/{id}/restore` | 유실 세션 복원 | 동기 · **축소 예정** |
| 보고서 | `POST /api/v0/reports` | 보고서 생성 요청 | 202 + 폴링 |
| 보고서 | `GET /api/v0/reports/{jobId}` | 보고서 결과 | 동기 |

| 교안 | `POST /api/v0/curricula` | 교안 PDF(multipart) 분석 요청 | 202 + 폴링 |
| 교안 | `GET /api/v0/curricula/{jobId}` | 교안 구조·개념 결과 | 동기 |

**"축소 예정"이 무슨 뜻인가**: 백엔드가 문제 3행 + 단계 12행을 분석 직후에 저장하고 세션을 `READY`로 미리 만드는 구조를 골랐다. 그러면 세션 시작 시 AI 호출이 필요 없고 `/answers`만 남는다. 지금은 셋 다 동작하지만 제거될 수 있으니 새로 의존하지 말 것.

### 분석

```jsonc
// POST /api/v0/analyses          Content-Type: application/json 또는 multipart/form-data
{
  "attemptId": "att-1", "submissionId": "sub-1",
  "method": "GITHUB_URL",                    // 또는 ZIP_WITH_GITLOG (multipart로 ZIP 첨부)
  "source": { "repoUrl": "https://github.com/...", "branch": "main" },
  "extractionScope": "TOTAL",                // 또는 OWN_COMMIT (이때 commitEmail 필수)
  "questionBudget": 3,
  "modelCode": "glm-5.2",                    // 생략 시 서버 기본값
  "focusItems": [                            // 강사가 체크포인트에 지정한 질문 초점 후보
    { "id": "a3f2-…", "name": "예외 처리" },
    { "id": "b7c1-…", "name": "동시성" }
  ],
  "requirements": [ { "requirementId": "req-1", "text": "로그인 실패 3회 시 잠금" } ],
  "teaches": [ { "id": "tch-1", "label": "의존성 주입", "unitId": "u3", "sourcePages": [12, 13] } ],
  "callbackUrl": "https://.../internal/ai-callbacks"   // 선택. 현재 수용만 하고 전송 미구현
}
// → 202
{ "jobId": "1b40467e-…", "status": "QUEUED" }
```

**`focusItems`를 Spring이 보내주는 이유**: `question_focus_item`의 PK가 랜덤 UUID라 AI가 그 값을 알 방법이 없다. 후보를 받아 **AI가 그중 하나의 `id`를 그대로 돌려준다.** 강사 지정 범위를 벗어날 수 없고, 항목 이름이 바뀌어도 AI 재배포가 필요 없다.

```jsonc
// GET /api/v0/analyses/{jobId}   → 200
{
  "jobId": "1b40467e-…", "attemptId": "att-1", "submissionId": "sub-1",
  "status": "SUCCEEDED",                     // QUEUED|RUNNING|SUCCEEDED|PARTIAL|FAILED
  "failureReason": null,
  "startedAt": "2026-07-30T05:00:00Z", "completedAt": "2026-07-30T05:00:12Z",
  "result": {
    "snapshotId": "snap-1",
    "snapshotMeta": { "contentHash": "…64자…", "fileCount": 42, "byteCount": 183920 },
    "appliedScope": "TOTAL", "scopeFallback": false, "fallbackReason": null,
    "commitSha": null,
    "analysisDocumentMarkdown": "## 구조\n…",       // code_analysis.analysis_document_markdown
    "requirementResults": [
      { "requirementId": "req-1", "verdict": "P", "evidence": "AuthService:41", "note": null }
    ],
    "problems": [ /* 아래 */ ],
    "questionCountPlanned": 3
  },
  "aiUsage": [ /* §aiUsage */ ]
}
```

`requirementResults`는 **요청 `requirements`와 길이가 같다.** 모델이 일부를 빠뜨리면 조용히 채우지 않고 `verdict: "F"` + `note: "판정 실패"`로 명시한다.

`problems[]`는 DB `assessment_problem`(+ `problem_reference`) 테이블에 대응하므로 **컬럼 이름을 그대로 쓴다.**

```jsonc
{
  "problemId": "…",
  "problemNo": 1,                      // 1~3
  "status": "READY",                   // READY | IN_PROGRESS | COMPLETED | TERMINATED
  "problemType": "DESIGN_CHOICE",      // 아래 5종
  "priority": 0.91,
  "questionFocusItemId": "a3f2-…",     // 요청 focusItems에서 고른 id
  "sourcePath": "app/main.py",
  "lineStart": 12, "lineEnd": 14,
  "codeSnippet": "def main():\n    …",  // evidenceHash의 대상
  "evidenceHash": "…",
  "extractorVersion": "v0",            // 문자열. 룰 버전을 붙일 수 있게
  "references": [
    { "path": "app/cli.py", "lineStart": 30, "lineEnd": 33,
      "evidenceHash": "…", "referenceType": "CALLER" }
  ],
  "stages": [
    { "axisCode": "L1", "questionText": "…", "flagged": false,
      "hints": [ { "hintLevel": 1, "hintText": "…" },
                 { "hintLevel": 2, "hintText": "…" } ] }
    // L2 · L3 · L4 동일 구조. stages는 항상 4개
  ]
}
```

**`stages`는 항상 4개(L1~L4)이고 각 stage에 `hints` 2개가 실린다.** 질문·힌트는 여기서 동결되고 문답 중에는 새로 만들지 않는다(§1). `flagged`는 보기형(①②③)이 섞여 재생성에도 실패한 질문 표시로, 화면에 "검수 필요"로 띄운다.

**Spring이 받아서 할 일**: 문제 3행 + `problem_stage` 12행 + `stage_answer_attempt` 36행(질문 12개·힌트 24개를 넣고 답변란은 NULL)을 미리 만들어 두면, 세션 진행 중 AI에 질문을 물을 필요가 없다.

**`codeSnippet`을 AI가 보내는 이유**: `evidenceHash`가 `codeSnippet` 기준 해시이고 해시를 AI가 만든다. Spring이 ZIP을 따로 잘라내면 줄바꿈·BOM이 1바이트만 달라도 해시가 안 맞는다.

**`problemType` 5종** — "왜 이 지점을 골랐나". `questionFocusItem`의 "무엇을 묻나"와 다른 축이다.

```
DESIGN_CHOICE          대안이 있었는데 이것을 택한 지점
RISK_POINT             규칙 스캔이 잡은 잠재 결함
COMPLEXITY_HOTSPOT     분기·중첩·길이가 몰린 곳
REQUIREMENT_IMPL       특정 요구사항을 구현한 부분
EXTERNAL_INTEGRATION   외부 라이브러리·API 사용 결정
```

**`referenceType` 6종** — 추가 근거의 역할. `PRIMARY`는 쓰지 않는다(주 코드 지점이 `assessment_problem`으로 옮겨졌다).

```
CALLER · CALLEE · DEFINITION · TEST · CONFIG · SIMILAR
```

### 세션

```jsonc
// POST /api/v0/sessions/{id}/answers
{ "clientRequestId": "turn-7", "answerText": "재귀 대신 반복문으로 바꿨습니다" }

// → SessionView
{
  "sessionId": "sess-abc",
  "state": "IN_PROGRESS",                    // IN_PROGRESS|PAUSED|COMPLETED|FAILED|EXPIRED
  "current": {
    "problemId": "prob-1", "axisCode": "L3", "sequenceNo": 5, "attemptNo": 1,
    "questionText": "…",
    "hintText": null,                        // 3점 미만이었으면 동결분에서 꺼내 채운다
    "codeContext": { "path": "src/Solver.java", "lineStart": 42, "lineEnd": 58, "snippet": "…" }
  },
  "progress": { "problemIndex": 1, "problemTotal": 3 },
  "transcript": [
    { "problemId": "prob-1", "axisCode": "L1", "questionText": "…", "answerText": "…",
      "answeredAt": "…", "bestScore": 4, "confirmedScore": 4,
      "attemptCount": 1, "passed": true, "hintText": null, "autonomy": "SELF" }
  ],
  "aiUsage": [ /* §aiUsage */ ]
}
```

`clientRequestId`는 세션 내 유일한 멱등키다. 같은 키로 재요청하면 처음 돌려준 응답을 그대로 반환한다.

`state`는 DB `assessment_session.status`의 허용값을 따른다. **`TIMEOUT`은 폐기값이다** — 시간 초과는 `EXPIRED`다. (코드 미반영: `schemas/session.py`가 아직 `TIMEOUT`을 쓴다.)

**점수 필드가 wire에 나오는 이유**: Spring이 매 턴 저장하고, 세션이 유실되면 그 기록으로 복구한다. 점수·시도 횟수가 wire에 없으면 "이 학생이 힌트를 몇 번 썼는지"를 아무도 모르게 된다.

```
bestScore        힌트 상한 적용 "전" LLM 원점수 0~5   (DB problem_stage.best_score)
confirmedScore   상한 적용 "후" 기록 점수             (DB problem_stage.confirmed_score)
attemptCount     0~3. 0은 앞 단계에서 끊겨 미도달
hintsUsed        보내지 않는다 = attemptCount - 1 (미도달이면 0)
autonomy         SELF(힌트 0) | SELF_MAINTAINED(1) | PARTIAL(2)
```

### aiUsage — 모든 응답에 붙는다

DB `ai_usage`(기관별 AI 호출·토큰·비용 원장)에 대응한다. **LLM 호출 1건 = 배열 원소 1개**이므로, 코드 분석 응답에는 6개가 들어간다.

```jsonc
"aiUsage": [
  {
    "idempotencyKey": "01H8XABC…:QUESTION_L1:1",   // {sourceId}:{sourceType}:{attemptNo}
    "sourceType": "QUESTION_L1",                   // 값 목록 백엔드 확정 대기
    "sourceId": "01H8XABC…",                       // 작업 PK
    "featureCode": "QUESTION_GENERATION",
    "modelCode": "glm-5.2",           // Spring이 ai_model 조회해 model_id 확보
    "inputTokenCount": 3200, "outputTokenCount": 180, "cachedTokenCount": 0,
    "status": "SUCCEEDED",            // SUCCEEDED | FAILED | PARTIAL
    "failureCode": null,              // FAILED·PARTIAL이면 필수
    "latencyMs": 1840,
    "occurredAt": "2026-07-30T05:00:12.331Z"
  }
]
```

`featureCode` — `CODE_ANALYSIS`(분석 문서·요구사항 판정) · `QUESTION_GENERATION`(문제 선정, 질문·힌트 동결) · `GRADING`(채점) · `SUMMARY_DRAFT`(보고서) · `CURRICULUM_ANALYSIS`(교안). 힌트는 질문과 함께 동결되므로 별도 값이 필요 없다.

`failureCode` 5종 — `TIMEOUT` · `RATE_LIMITED` · `PROVIDER_ERROR` · `INVALID_JSON` · `CONTEXT_OVERFLOW`.

**필드 이름이 DB 컬럼명과 1:1이다.** Spring은 매핑 고민 없이 그대로 INSERT하면 된다.

DB CHECK 제약 둘을 AI가 지켜서 보낸다 — `cachedTokenCount <= inputTokenCount`, 그리고 `status`가 `SUCCEEDED`면 `failureCode`는 null이고 `FAILED`·`PARTIAL`이면 반드시 채워진다.

**단가·비용은 AI가 보내지 않는다.** AI가 모델 단가표를 들고 있으면 단가가 바뀔 때마다 재배포해야 한다. `ai_model` 테이블을 가진 Spring이 토큰 수에 곱한다.

**실패한 호출도 기록한다.** 실패해도 토큰·비용이 발생하고 재시도 통계에 필요하다.

### 보고서

```jsonc
// POST /api/v0/reports          → 202
{ "sessionId": "sess-abc", "transcript": [ … ],
  "analysisDocumentMarkdown": "…", "teaches": [ … ] }
{ "jobId": "…", "status": "QUEUED" }
```

```jsonc
// GET /api/v0/reports/{jobId}   → 200 (result 부분)
{
  "problems": [
    { "problemNo": 1, "problemId": "…", "totalScore": 16, "maxScore": 20,
      "stages": [ { "axisCode": "L1", "confirmedScore": 4, "bestScore": 4,
                    "attemptCount": 1, "passed": true } ] }
  ],
  "reportMarkdown": "…",
  "curriculumRefs": [ { "teachId": "tch-1", "unitId": "u3", "sourcePages": [12, 13] } ],
  "retestTargets": [ "prob-2" ],
  "versions": { "modelCode": "glm-5.2", "promptVersion": "p04-6", "rubricVersion": "1" }
}
```

**세션 총점·축 평균은 보내지 않는다.** 점수는 이미 `problem_stage`에 매 턴 저장되어 있고, 요약이 필요하면 Spring이 집계한다. LLM이 아니면 못 만드는 것(서술형 진단·교안 매핑)이 `/reports`의 본체다.

문제당 만점은 20(4단계 × 5). **재시험 판정은 문제 단위**이고, 커트라인은 조직 정책이라 Spring이 정한다 — AI는 `retestTargets`만 낸다.

### 교안 분석

PDF가 항상 필요하므로 **multipart 하나만 받는다**(`payload` 문자열 + `file`).

```jsonc
// POST /api/v0/curricula        multipart/form-data → 202
// payload = {"versionId": "ver-1", "modelCode": "glm-5.2"}
// file    = 교안 PDF
{ "jobId": "…", "status": "QUEUED" }
```

```jsonc
// GET /api/v0/curricula/{jobId}  → 200 (result 부분)
{
  "versionId": "ver-1",
  "analysisVersion": 1, "heuristicVersion": 1, "promptVersion": 1,
  "extractionStatus": "EXTRACTED", "qualityStatus": "OK", "fallbackUsed": false,
  "sections": [
    { "moduleNo": 1, "title": "예외 처리", "pageStart": 1, "pageEnd": 12,
      "teaches": [
        { "canonicalName": "try-except", "normalizedName": "try except",
          "canonicalDescription": "예외를 잡아 처리하는 구문",
          "descriptionPageStart": 3, "descriptionPageEnd": 5 },
        { "canonicalName": "finally", "normalizedName": "finally",
          "canonicalDescription": null,
          "descriptionPageStart": null, "descriptionPageEnd": null }
      ] }
  ]
}
```

DB 3계층(`curriculum_analysis` → `curriculum_section` → `teaches`)을 그대로 따른다. **AI는 UUID를 만들지 않는다** — 구조만 돌려주고 Spring이 INSERT하며 키를 발급한다.

**`normalizedName`을 AI가 만든다.** DB에 `PARTIAL UNIQUE (org_id, section_id, normalized_name)`가 걸려 중복 판정 기준이 되므로, Spring이 다시 정규화하면 AI가 본 것과 달라진다(`evidenceHash`와 같은 이유).

**설명이 없는 개념이 정상이다.** 교안에 개념만 등장하고 설명이 없는 경우가 흔해 세 필드가 NULL로 온다. `teaches.status`(ACTIVE/INACTIVE/MERGED)와 병합 처리는 UUID를 알아야 하는 운영 판단이라 Spring 몫이다.

교안 분석은 LLM을 무겁게 쓴다(교안 1개에 1~2분 이상). **수업 중이 아니라 LMS 업로드 시점에 도는 것**이 전제이고, 그래서 `Idempotency-Key`로 중복 실행을 막는다.

---

## 4. 코드 구조

```
app/
├─ main.py          앱 조립. 라우터 등록만. 로직 없음
├─ config.py        Settings — 환경변수 (engine_mode 포함)
├─ jobs.py          분석 job 인메모리 저장소 + 수명주기(상태 전이)
├─ sessions.py      문답 세션 인메모리 저장소 + 진행(멱등)
├─ reports.py       보고서 job 인메모리 저장소 (jobs.py와 형제)
├─ api/             HTTP 계층 — 백엔드가 보는 면
│  ├─ deps.py         인증
│  ├─ errors.py       예외 핸들러
│  ├─ health.py · analyses.py · sessions.py · reports.py
├─ schemas/         계약의 실체 — 요청·응답 모델
│  └─ common.py · analysis.py · session.py · report.py
└─ engines/         팀원 PoC 코드가 들어오는 자리
   ├─ __init__.py     get_analysis_engine() 팩토리 — 설정 보고 구현 선택
   ├─ base.py         계약(Protocol)
   └─ stub.py         엔진 없을 때 고정 응답
tests/
```

**파일명 규칙**: `schemas/`는 단수(`analysis.py`), `api/`·저장소 모듈은 복수(`analyses.py`·`jobs.py`).

층은 셋뿐이다.

| 층 | 존재 이유 | 금지 |
|---|---|---|
| `api/` | HTTP를 아는 유일한 곳 | 분석 로직 금지 |
| `schemas/` | 계약을 한곳에서 읽게 | 동작 금지, 데이터 모양만 |
| `engines/` | 팀원 코드 격리 | HTTP·pydantic 몰라야 함 |

`services/` 층은 두지 않는다. 라우터 하나가 60줄을 넘으면 그때 뽑는다.

### 엔진 소켓

```python
class AnalysisEngine(Protocol):
    def analyze(self, request: dict, zip_bytes: bytes | None = None) -> dict: ...
```

**엔진은 FastAPI를 모른다.** `dict`를 받아 `dict`를 준다. 이유가 둘이다.

- 팀원 코드를 옮길 때 FastAPI 지식이 필요 없다. 순수 함수로 만들면 끝난다
- 엔진을 CLI에서 단독 실행할 수 있어 디버깅이 쉽다

`api/`가 결과 dict를 pydantic 모델로 감싸 응답한다. 그 변환이 유일한 접착점이다.

```python
engine_mode: Literal["stub", "real"] = "stub"
```

`stub`이면 스키마에 맞는 고정 응답을 돌려준다. **엔진이 하나도 없어도 모든 엔드포인트가 살아 있어서** 백엔드가 엔진 완성을 기다리지 않고 붙여볼 수 있다. 반대로 `real`인데 엔진이 없으면 조용히 스텁으로 떨어지지 않고 시끄럽게 실패한다 — 가짜 데이터가 운영까지 흘러가는 것을 막는다.

---

## 5. 현재 상태와 앞으로

### 지금

| | |
|---|---|
| 엔드포인트 | **11/11 동작 (전부 스텁)** |
| 테스트 | **58 passed** |
| 붙일 수 있나 | **예.** 인증·에러 형식·camelCase·Swagger·`openapi.json`까지 완성 |

완료된 것: `/gradings` → `/reports` 전환, 이름 통일(`decision_point`→`problem`, `depth_level`→`axis_code`), **분석·보고서 스키마를 DB 계약에 정렬**.

응답은 고정 스텁이지만 **계약 모양은 정해져 있다.** 백엔드는 9개 전부 지금 바로 붙여볼 수 있다.

> ✅ **`/analyses`·`/reports`는 §3 명세대로 코드에 반영됐고 `openapi.json`도 갱신됐다.** 축 값 `"L1"`~`"L4"`(L3=대안 비교 / L4=반례 대응), `focusItems`, `codeSnippet`, `requirementResults`, `bestScore`/`confirmedScore`가 전부 스펙에 들어가 있다. `Problem.stages`는 `minItems/maxItems: 4`, `ProblemStage.hints`는 `2`로 나가므로 **동결 구조를 스펙만 보고 알 수 있다.**
>
> ⏳ **아직인 것**: 세션 시작 시 질문 생성 제거(동결이라 Spring이 DB에서 읽어 넘겨준다 — 세션 엔드포인트 축소와 함께), 엔진 이식. 순서는 `PLAN_FASTAPI_MIGRATION.md`.

### 백엔드 대기 2건

이슈 `Team-IZ/Backend#31` 본문이 현재 상태판이다.

| # | 내용 | 우리 작업을 막나 |
|---|---|---|
| C-4 | `source_type` 값 목록 | 아니다. 형식만 지키고 값은 나중에 맞춘다 |
| C-5 | `curriculum_analysis.extraction_status`·`quality_status` 코드 카탈로그 | 아니다. CHECK가 없어 `str`로 두고 나중에 맞춘다 |

C-1~C-3은 **회신 완료**다.

- **C-1** 분석 요청에 `focusItems: [{id, name}]`를 싣고 AI가 `questionFocusItemId`로 하나를 돌려준다
- **C-2** `score_run`·`axis_score` 제거 예정. 점수의 단일 소유자는 `problem_stage`이고 축 어휘는 `'L1'`~`'L4'` 한 벌
- **C-3** **비용은 Spring이 계산한다.** AI는 토큰·모델·지연·상태만 보낸다. 모델을 고르는 주체가 백엔드·프론트라 단가도 그쪽이 먼저 안다

DDL 수정 요청 4건(`attempt_count` 0~3 · `attempt_no=3` 허용 · `stage_answer_attempt` NULL 허용 + all-or-nothing CHECK · `TERMINATED_AT_L1` 코드값)도 같은 이슈에 있다.

### 앞으로

```
세션 시작 시 질문 생성 제거 — 질문은 분석 때 동결돼 DB에 있다 (세션 엔드포인트 축소와 함께)
엔진 이식 (팀원 PoC feat/poc_full) — P02 규칙부, P04 LLM 스테이지
(먼 항목) 적응형 힌트 모듈 대응 — 턴당 2콜, 힌트용 featureCode, 체크포인트 단위 모드 고정
```

완료: 세션 턴 점수 필드 · `aiUsage` 스키마 · `/curricula` 신설 · `openapi.json` 갱신.

**미확정값** — 힌트 점수 상한 `{5, 4, 3}`, 재시험 커트라인, `ai_usage.source_type` 값 목록. 세부 순서·방법은 **`PLAN_FASTAPI_MIGRATION.md`**에 있다.

---

## 6. 팀원 PoC 브랜치

엔진은 여기서 이식한다. 브라우저에서 도는 PoC이고 LLM 호출은 Cloudflare Worker 프록시를 거친다.

| 브랜치 | 내용 | 워크트리 |
|---|---|---|
| **`feat/poc_full`** | **통합 PoC(P04).** 이식 대상 | `../ai_poc/poc_full` |
| `feat/code_Q&A` | 구 P02 코드분석 / P03 문답 | `../ai_poc/qna` |
| `feat/pdf_analysis` | P01 교안 분석 | `../ai_poc/pdf` |

워크트리는 **읽기 전용(detached HEAD)** 이다. 절대 수정·커밋하지 않는다. 팀원 코드를 고쳐야 하면 팀원에게 요청한다.

### 이식할 때

- **JS는 설계 선택이 아니라 브라우저 제약의 결과다.** CORS·키 노출 때문에 프록시를 거쳐야 했고 UI가 얽혀 있었다. 서버에는 그 제약이 없으므로 **전부 Python으로 옮긴다.** Node를 띄우지 않는다
- **프롬프트·파라미터는 매니페스트가 계약이다.** P04는 `app/prompt_manifest.json` + `app/scoring-config.js` 두 파일. 프롬프트만 바뀌면 이 파일들만 다시 가져오면 되고, 제어 흐름이 바뀔 때만 코드를 손댄다
- **질문·힌트 동결(`hint-ladder.js`)은 그대로 옮긴다.** 문제 하나당 L1~L4 질문 4개 + 힌트 8개를 한 번에 만들어 `frozen_at`을 찍는 구조가 우리 계약과 같다
- 🔴 **PoC의 축 순서가 우리와 반대다.** `scoring-config.js`가 L3=반례, L4=대안이다. 이식할 때 `AXES`의 `order`·`label`·루브릭 텍스트를 L3↔L4 교환한다. 순서만 맞추고 루브릭을 그대로 두면 L3 답변이 L4 기준으로 채점된다. 워크트리는 읽기 전용이므로 원본 정정은 팀원에게 요청한다
- **잘라낼 것**: Supabase 저장(DB 주인은 Spring), Worker LLM 프록시(서버는 직접 호출), IndexedDB·sessionStorage, UI·타이머, 브라우저 pdf.js(서버 라이브러리로 교체 — 결과가 동일하지 않다)
- **규칙 스캔부는 CPU라 이벤트 루프를 막을 수 있다.** `async def` 안에서 동기로 돌리면 문답 중인 학생까지 굳는다. `def`(threadpool)나 `run_in_executor`로 뺀다

---

## 7. 개발 규칙

**브랜치**: `feature/*` → 동작·테스트 완료 후 `main` → `main` 기준 `develop` 생성 → 이후 `develop`에서 수정·테스트 후 `main` 병합. 기본 브랜치는 `develop`.

**커밋**

```
type: short description (#issue)
```

`feat` `fix` `refactor` `style` `docs` `chore` `remove` 중 하나. 동사원형 소문자로 시작, 마침표 없음, 50자 이내, 이슈가 있으면 번호 필수.

**PR**: 제목 `[feat] add login page UI`, 본문에 `closes #번호`. 1 PR = 1 기능, 파일 10개 이내 권장, 최소 1인 승인.

**커밋 전 확인**: `.env` 스테이징 금지, 브랜치 확인, 빌드 통과, 디버그 로그 제거, `main`/`develop` 직접 작업 금지.

상세는 `../rule/개발/이슈 O/Git 커밋 & PR 가이드.docx`.

---

## 8. 참고 문서

| 문서 | 내용 |
|---|---|
| `PLAN_FASTAPI_MIGRATION.md` | AI 파트 작업 계획·진행 (내부용) |
| `../qna/2026-07-30/issue-body-v2.md` | 백엔드 이슈 #31 본문 사본 — **AI↔백엔드 현재 상태판** |
| `../output_docs/AI파트_현황.md` | 팀 공유용 현황 요약 |
| `../output_docs/미결_논의사항.md` | 아직 안 정해진 것 |
| `../docs/docs_for_read/테이블정의서_v06.md` | DB 테이블·CHECK 제약 (2026-07-30 변환) |
| `../docs/AI-Backend_API_명세서_v0.1.md` | AI↔Backend 전체 계약 (내용은 v0.2) |
| `../docs/docs_for_read/` | 기획·요구사항 문서 Markdown 변환본 |
| `../rule/개발/이슈 O/` | 커밋·PR 규칙 |

`../docs/`의 문서는 확정 스펙이 아니라 바뀔 수 있는 기획 자료다. 실제 코드나 최근 논의와 어긋나면 문서를 맹신하지 말고 확인 후 진행한다.

`_legacy/`는 재구축 이전 구현의 로컬 사본이다. `.gitignore` 대상이라 커밋되지 않으며 모듈화 참고용으로만 둔다.
