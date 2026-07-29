# AI 서비스 (FastAPI)

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
2. 코드 분석    → 분석 문서 · 요구사항 P/F · 문제 3개 · 코드 파편
                                                     제출 마감 후 배치. 1시간 예산
3. 문답         문제 3개 × 레벨 L1~L4                실시간. 학생이 화면에서 대기
4. 보고서       점수 매트릭스 · 교안 참조 · 재시험 대상
```

**셋의 시간축이 겹치지 않는다.** 교안 분석·코드 분석·문답이 동시에 도는 일이 없다.

### 문답 규칙

```
레벨마다 답변을 0~5점으로 즉시 채점
  3점 이상 → 다음 레벨
  3점 미만 → 미리 준비된 힌트를 주고 같은 레벨 재질의 (레벨당 최대 2회)
            힌트 2회 소진 후에도 미달 → 그 문제 종료, 다음 문제의 L1로

힌트를 쓰면 그 레벨의 점수 상한이 내려간다
  무힌트 5점 / 힌트1 후 4점 / 힌트2 후 3점
  기록 점수 = min(LLM 원점수, 상한)
```

| 축 | 이름 | 무엇을 묻나 |
|---|---|---|
| L1 | 코드 기술 | 이 코드가 무엇을 어떻게 하는가 |
| L2 | 설계 논리 | 왜 이렇게 설계했는가 |
| L3 | 반례·한계 | 이 설계가 깨지는 조건은 |
| L4 | 대안 | 다른 선택지와 비교해 왜 이것인가 |

**채점이 세션 진행을 제어한다.** 점수가 다음 턴을 결정하므로 매 턴 Spring으로 나가 저장된다.

**질문과 힌트는 분석 단계에서 미리 만들어 동결한다.** 답변을 보고 힌트를 만들면 학생마다 힌트가 달라져, "몇 번째 힌트에서 통과했는가"가 학생 실력이 아니라 생성 결과의 차이를 재게 되기 때문이다.

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

### 엔드포인트

| 그룹 | 메서드·경로 | 역할 | 방식 |
|---|---|---|---|
| 공통 | `GET /api/health` | 서비스 상태 | 동기 |
| 분석 | `POST /api/v0/analyses` | 코드 분석 요청 | 202 + 폴링 |
| 분석 | `GET /api/v0/analyses/{jobId}` | 분석 상태·결과 | 동기 |
| 세션 | `POST /api/v0/sessions` | 세션 시작 → 첫 질문 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/answers` | 답변 제출 → 채점 + 다음 질문/힌트 | 동기 |
| 세션 | `GET /api/v0/sessions/{id}` | 세션 현재 상태 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/restore` | 유실 세션 복원 | 동기 |
| 채점 | `POST /api/v0/gradings` | 후채점 → **`/reports`로 변경 예정** | 202 + 폴링 |
| 채점 | `GET /api/v0/gradings/{jobId}` | → **`/reports/{jobId}`로 변경 예정** | 동기 |

**신설 예정**: `POST /api/v0/curricula`, `GET /api/v0/curricula/{jobId}` (교안 PDF → teaches)

### 분석

```jsonc
// POST /api/v0/analyses          Content-Type: application/json 또는 multipart/form-data
{
  "attemptId": "att-1", "submissionId": "sub-1",
  "method": "GITHUB_URL",                    // 또는 ZIP_WITH_GITLOG (multipart로 ZIP 첨부)
  "source": { "repoUrl": "https://github.com/...", "branch": "main" },
  "extractionScope": "TOTAL",                // 또는 OWN_COMMIT (이때 commitEmail 필수)
  "questionBudget": 3,
  "callbackUrl": "https://.../internal/ai-callbacks"   // 선택. 현재 수용만 하고 전송 미구현
}
// → 202
{ "jobId": "1b40467e-...", "status": "QUEUED" }
```

```jsonc
// GET /api/v0/analyses/{jobId}   → 200
{
  "jobId": "1b40467e-...", "attemptId": "att-1", "submissionId": "sub-1",
  "status": "SUCCEEDED",                     // QUEUED|RUNNING|SUCCEEDED|PARTIAL|FAILED
  "failureReason": null,
  "startedAt": "2026-07-29T05:00:00Z", "completedAt": "2026-07-29T05:00:12Z",
  "result": {
    "snapshotId": "snap-1",
    "snapshotMeta": { "contentHash": "…64자…", "fileCount": 42, "byteCount": 183920 },
    "appliedScope": "TOTAL", "scopeFallback": false, "fallbackReason": null,
    "commitSha": null,
    "problems": [ /* assessment_problem 컬럼명 그대로. 아래 설명 */ ],
    "questionCountPlanned": 3
  },
  "aiUsage": []
}
```

`problems[]`는 DB `assessment_problem`(+ `problem_reference`) 테이블에 대응하므로 **컬럼 이름을 그대로 쓴다.**

```
problemId, type, status, priority, focusCode, sourcePath, lineStart, lineEnd,
evidenceHash, extractorVersion,
references[{ path, lineStart, lineEnd, evidenceHash, referenceType }]
```

`status` 허용값은 DB CHECK를 따른다: `CANDIDATE / READY / USED / SKIPPED / INVALID`.

### 세션

```jsonc
// POST /api/v0/sessions          → 201
{ "attemptId": "att-1", "analysisJobId": "1b40467e-...",
  "selectedProblemIds": ["prob-1","prob-2","prob-3"],   // 생략 시 전체
  "timeLimitSec": 2400 }

// POST /api/v0/sessions/{id}/answers
{ "clientRequestId": "turn-7", "answerText": "재귀 대신 반복문으로 바꿨습니다" }

// 양쪽 응답 모두 → SessionView
{
  "sessionId": "sess-abc",
  "state": "IN_PROGRESS",                    // IN_PROGRESS|COMPLETED|TIMEOUT|FAILED
  "current": {
    "problemId": "prob-1", "axisCode": "L3_COUNTEREXAMPLE", "sequenceNo": 5,
    "questionText": "…",
    "codeContext": { "path": "src/Solver.java", "lineStart": 42, "lineEnd": 58, "snippet": "…" }
  },
  "progress": { "problemIndex": 1, "problemTotal": 3 },
  "transcript": [ { "problemId": "prob-1", "axisCode": "L1_CODE_DESCRIPTION",
                    "questionText": "…", "answerText": "…", "answeredAt": "…" } ],
  "aiUsage": []
}
```

`clientRequestId`는 세션 내 유일한 멱등키다. 같은 키로 재요청하면 처음 돌려준 응답을 그대로 반환한다.

`POST /sessions/{id}/restore`는 Spring이 저장해둔 transcript로 유실된 세션을 재구성하고 이어질 질문을 돌려준다.

### aiUsage — 모든 응답에 붙는다

DB `ai_usage`(기관별 AI 호출·토큰·비용 원장)에 대응한다. **LLM 호출 1건 = 배열 원소 1개**이므로, 코드 분석 응답에는 6개가 들어간다.

```jsonc
"aiUsage": [
  {
    "idempotencyKey": "9f2c1e3a-…",   // uuid4. 호출마다 발급, 재시도 시 재사용
    "featureCode": "SESSION_DIALOG",  // CODE_ANALYSIS | QUESTION_GENERATION | GRADING
                                      // | SESSION_DIALOG | SUMMARY_DRAFT | CURRICULUM_ANALYSIS
    "modelCode": "glm-5.2",           // Spring이 ai_model 조회해 model_id 확보
    "inputTokenCount": 3200, "outputTokenCount": 180, "cachedTokenCount": 0,
    "status": "SUCCEEDED",            // SUCCEEDED | FAILED | PARTIAL
    "failureCode": null,              // FAILED·PARTIAL이면 필수
    "latencyMs": 1840,
    "occurredAt": "2026-07-29T05:00:12.331Z"
  }
]
```

**필드 이름이 DB 컬럼명과 1:1이다.** Spring은 매핑 고민 없이 그대로 INSERT하면 된다.

DB CHECK 제약 둘을 AI가 지켜서 보낸다 — `cachedTokenCount <= inputTokenCount`, 그리고 `status`가 `SUCCEEDED`면 `failureCode`는 null이고 `FAILED`·`PARTIAL`이면 반드시 채워진다.

**단가·비용은 AI가 보내지 않는다.** AI가 모델 단가표를 들고 있으면 단가가 바뀔 때마다 재배포해야 한다. `ai_model` 테이블을 가진 Spring이 토큰 수에 곱한다.

**실패한 호출도 기록한다.** 실패해도 토큰·비용이 발생하고 재시도 통계에 필요하다.

### 채점 (변경 예정)

```jsonc
// POST /api/v0/gradings          → 202
{ "sessionId": "sess-abc", "scoreRunId": "run-1", "transcript": [ … ] }
{ "jobId": "…", "status": "QUEUED" }
```

현재 코드는 5축 후채점 계약이다. **P04 도입으로 4축 + 보고서 생성으로 바뀔 예정**이며, 백엔드 회신 후 반영한다(§5).

---

## 4. 코드 구조

```
app/
├─ main.py          앱 조립. 라우터 등록만. 로직 없음
├─ config.py        Settings — 환경변수 (engine_mode 포함)
├─ jobs.py          분석 job 인메모리 저장소 + 수명주기(상태 전이)
├─ sessions.py      문답 세션 인메모리 저장소 + 진행(멱등)
├─ gradings.py      채점 job 인메모리 저장소 (jobs.py와 형제)
├─ api/             HTTP 계층 — 백엔드가 보는 면
│  ├─ deps.py         인증
│  ├─ errors.py       예외 핸들러
│  ├─ health.py · analyses.py · sessions.py · gradings.py
├─ schemas/         계약의 실체 — 요청·응답 모델
│  └─ common.py · analysis.py · session.py · grading.py
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

`stub`이면 스키마에 맞는 고정 응답을 돌려준다. **엔진이 하나도 없어도 모든 엔드포인트가 살아 있어서** 백엔드가 엔진 완성을 기다리지 않고 붙여볼 수 있다.

---

## 5. 현재 상태와 앞으로

### 지금

| | |
|---|---|
| 엔드포인트 | **9/9 동작 (전부 스텁)** |
| 테스트 | **36 passed** |
| 붙일 수 있나 | **예.** 인증·에러 형식·camelCase·Swagger·`openapi.json`까지 완성 |

응답은 고정 스텁이지만 **계약 모양은 확정**이다. 백엔드는 9개 전부 지금 바로 붙여볼 수 있다.

### 앞으로

**1단계 — 백엔드 스키마 회신 대기 (지금 막혀 있음)**

P04 도입으로 채점부 계약이 바뀌었다. 회신 전에 스키마를 고치면 두 번 고치게 된다.
질문지: `../qna/2026-07-29/backend-schema-questions.md`

**2단계 — 계약 반영**

```
4축(L1~L4)·0~5점으로 채점 스키마 수정
/gradings → /reports 전환 (5축 후채점 → 보고서 생성)
POST/GET /curricula 신설 (교안 분석)
분석 요청·응답 확장 (requirements · teaches · analysisDocuments · preparedQuestions)
세션 턴에 점수 필드 추가 (rawScore · score · hintsUsed · retryNo · hintText · autonomy)
openapi.json 재생성 → 백엔드 전달
```

**3단계 — 엔진 이식**

팀원 PoC를 Python으로 옮긴다. 브랜치는 `feat/poc_full`.

세부 순서·방법은 **`PLAN_FASTAPI_MIGRATION.md`**에 있다.

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
| `../output_docs/AI파트_현황.md` | 팀 공유용 현황 요약 |
| `../output_docs/미결_논의사항.md` | 아직 안 정해진 것 |
| `../qna/2026-07-29/backend-schema-questions.md` | 백엔드에 던진 스키마 질문 |
| `../docs/AI-Backend_API_명세서_v0.1.md` | AI↔Backend 전체 계약 (내용은 v0.2) |
| `../docs/docs_for_read/` | 기획·요구사항 문서 Markdown 변환본 |
| `../rule/개발/이슈 O/` | 커밋·PR 규칙 |

`../docs/`의 문서는 확정 스펙이 아니라 바뀔 수 있는 기획 자료다. 실제 코드나 최근 논의와 어긋나면 문서를 맹신하지 말고 확인 후 진행한다.

`_legacy/`는 재구축 이전 구현의 로컬 사본이다. `.gitignore` 대상이라 커밋되지 않으며 모듈화 참고용으로만 둔다.
