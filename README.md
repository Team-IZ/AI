# AI 서비스 (FastAPI)

교육생이 제출한 코드를 분석해 **Decision Point**를 뽑고(P02), 그 지점을 두고 **소크라틱 문답 세션**을 진행한 뒤(P03), 세션이 끝나면 전사(transcript) 전체를 대상으로 **5축 루브릭 후채점**(코드이해·설계논리·대안비교·반례대응·자기수정, 축당 1~5점·총 5~25점)을 수행한다. 채점은 세션 중이 아니라 세션 종료 후 1회 이루어진다.

```
React(Frontend) ──▶ Spring Boot(Backend) ──▶ FastAPI(이 저장소) ──▶ NVIDIA LLM
                          │
                          └── DB (단일 소유자)
```

React는 FastAPI를 직접 호출하지 않는다. **FastAPI의 호출자는 Spring뿐이다.**

**FastAPI는 DB를 갖지 않는다.** 결과를 응답이나 콜백으로 돌려줄 뿐이고 저장은 전부 Spring이 한다. 코드 원문도 임시 작업공간에만 두고 TTL로 지운다.

> **상태: 재구축 중.** 기존 구현은 브라우저 PoC와 얽혀 있어 `_legacy/`로 물러났고, 지금은 빈 FastAPI 골격부터 다시 쌓는 중이다. 진행 상황은 `PLAN_FASTAPI_MIGRATION.md`.

---

## 1. 이 저장소의 역할 분담

여기에는 성격이 다른 두 종류의 코드가 들어온다. 섞이면 유지보수가 무너지므로 경계를 먼저 이해할 것.

| 구분 | 내용 | 담당 |
|---|---|---|
| **골격** | FastAPI 앱, HTTP 계약, 인증, job 수명주기, 에러 형식 | 이 브랜치 |
| **엔진** | 코드 분석·문답·채점·교안 분석의 실제 알고리즘 | 팀원 PoC 브랜치에서 이식 |

골격은 **엔진이 없어도 동작해야 한다.** 백엔드 팀원이 엔진 완성을 기다리지 않고 붙여볼 수 있어야 하기 때문이다. 그래서 모든 엔드포인트는 스텁 응답을 먼저 갖추고, 엔진은 나중에 꽂는다.

### 팀원 PoC 브랜치

| 브랜치 | 내용 | 형태 |
|---|---|---|
| `feat/code_Q&A` | 코드 업로드 → 분석 → 문답 → 보고서 | 분석부 Python, 문답부 JavaScript(`shared/p03-engine.js`) |
| `feat/pdf_analysis` | 교안 PDF 분석 → 보고서에 "교안 어디를 복습하라" 표시 | Python 파이프라인 + JS 오케스트레이션 + 브라우저 pdf.js |

두 브랜치 모두 **브라우저에서 도는 PoC**다. 임시 프론트엔드가 붙어 있고, LLM 호출은 Cloudflare Worker 프록시(`worker/nvidia-proxy.js`)를 거친다.

> `feat/pdf_analysis`는 `feat/code_Q&A`의 내용을 `docs/lab/code-qna/` 아래에 통째로 품고 있다. 같은 파일이 경로만 다르게 두 브랜치에 존재하므로 병합 시 충돌한다.

### 이식할 때 유의할 점

- **JS는 설계 선택이 아니라 브라우저 제약의 결과다.** LLM 호출이 CORS·키 노출 때문에 프록시를 거쳐야 했고 UI가 얽혀 있어서 JS로 갔다. 서버에는 그 제약이 없으므로 **전부 Python으로 이식한다.** Node를 띄우지 않는다.
- **프롬프트와 파라미터는 `prompt_manifest.json`이 계약이다.** p01(6단계)·p02(5단계)·p03(7단계)의 프롬프트·기본값이 선언적으로 들어 있고 팀원이 유지보수한다. 프롬프트만 바뀌면 이 파일만 다시 가져오면 되고, 제어 흐름이 바뀔 때만 코드를 손댄다.
- **PDF 추출은 그대로 옮길 수 없다.** PoC는 브라우저 pdf.js를 쓴다. 서버에서는 다른 라이브러리로 바꿔야 하고 결과가 완전히 동일하지 않다.

---

## 2. 백엔드와의 계약

전체 명세는 `../docs/AI-Backend_API_명세서_v0.1.md`(내용은 v0.2). 아래는 요약이다.

| 그룹 | 메서드·경로 | 역할 | 방식 |
|---|---|---|---|
| 공통 | `GET /api/health` | 서비스 상태 | 동기 |
| 분석 | `POST /api/v0/analyses` | 코드 분석 요청 (P02) | 비동기 job (202) |
| 분석 | `GET /api/v0/analyses/{job_id}` | 분석 상태·결과 | 폴링 |
| 세션 | `POST /api/v0/sessions` | 검증 세션 시작 → 첫 질문 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/answers` | 답변 제출 → 다음 질문/종료 | 동기 (멱등키) |
| 세션 | `GET /api/v0/sessions/{id}` | 세션 현재 상태 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/restore` | 유실 세션 복원 | 동기 |
| 채점 | `POST /api/v0/gradings` | transcript 5축 후채점 | 비동기 job (202) |
| 채점 | `GET /api/v0/gradings/{job_id}` | 채점 상태·점수·근거 | 폴링 |

### 동기와 비동기를 나누는 기준

사람이 화면 앞에서 기다리면 동기, 아니면 job이다. 답변 제출은 학생이 대기하므로 동기여야 하고, 분석·채점은 수초~수분이 걸리므로 202를 주고 폴링시킨다.

비동기 job은 생성 시 `202 Accepted` + `{"jobId": "...", "status": "QUEUED"}`를 반환한다. 요청에 `callbackUrl`이 있으면 완료·실패 시 그 주소로 결과를 `POST`하고, `GET` 폴링은 콜백 유실 대비 폴백으로 유지한다.

### 요청 헤더 3종

| 헤더 | 값 | 용도 |
|---|---|---|
| `X-Internal-Key` | 공유 비밀 | 서비스 간 인증. `GET /api/health`만 면제 |
| `Idempotency-Key` | `submissionId:attemptNo` | 중복 요청 판별. 같은 키면 처음 만든 `jobId`를 `202`로 그대로 반환하고 재분석하지 않는다 |
| `X-Trace-Id` | 추적 ID | Spring이 `analysis_job.trace_id`로 저장 |

### 표기 규약 (2026-07-22 확정)

| 항목 | 확정 |
|---|---|
| 경로 prefix | **`/api/v0`** — 서비스 버전이 아니라 "개발 단계 API"라는 성숙도 표시. 계약이 안정되면 양쪽이 함께 v1으로 올린다 |
| 필드 표기 | **camelCase** (`jobId`, `snapshotId`). 파이썬 내부는 snake_case로 쓰고 직렬화만 변환한다 |
| 에러 형식 | **평탄 구조 `{error, message, retryable}`** |

```json
{
  "error": "INVALID_REQUEST",
  "message": "method=GITHUB_URL에는 source.repoUrl이 필요합니다",
  "retryable": false
}
```

`error`는 기계가 분기하는 코드 문자열, `message`는 사람이 읽는 설명, `retryable`은 Spring의 재시도 판단용이다. `timestamp`·`path`는 쓰지 않는다.

**`analysisId`는 Spring이 발급하며 AI는 만들지도 받지도 않는다.** Spring이 `jobId`로 연결한다.

응답 본문의 세부 구조(특히 `findings[]` 내부)는 팀원 PoC 결과에 따라 바뀐다. **엔드포인트·상태코드·최상위 필드까지만 고정하고 내부는 열어둔다.**

미결 항목은 `../qna/2026-07-22/backend-api-questions.md` 참고.

---

## 3. 코드 구조

```
app/
├─ main.py          앱 조립. 라우터 등록만. 로직 없음
├─ config.py        Settings — 환경변수
├─ api/             HTTP 계층 — 백엔드가 보는 면
│  ├─ deps.py         인증
│  ├─ health.py
│  ├─ analyses.py     P02        2개
│  ├─ sessions.py     P03        4개
│  └─ gradings.py     채점       2개
├─ schemas/         계약의 실체 — 요청·응답 모델
│  ├─ common.py       에러 형식, 공통 enum
│  ├─ analysis.py
│  ├─ session.py
│  └─ grading.py
└─ engines/         팀원 PoC가 들어오는 자리
   ├─ base.py         계약(Protocol)
   └─ stub.py         엔진 없을 때 고정 응답
tests/
```

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
    def run(self, source_dir: Path, options: dict) -> dict: ...
```

**엔진은 FastAPI를 모른다.** `dict`를 받아 `dict`를 준다. 이유가 둘이다.

- 팀원 코드를 옮길 때 FastAPI 지식이 필요 없다. 순수 함수로 만들면 끝난다
- 엔진을 CLI에서 단독 실행할 수 있어 디버깅이 쉽다

`api/`가 결과 dict를 pydantic 모델로 감싸 응답한다. 그 변환이 유일한 접착점이다.

### 스텁 전환

```python
engine_mode: Literal["stub", "real"] = "stub"
```

`stub`이면 스키마에 맞는 고정 응답을 돌려준다. 엔진이 하나도 없어도 9개 엔드포인트가 전부 살아 있고, 백엔드가 Swagger·Postman으로 계약을 검증할 수 있다.

---

## 4. 작업 방식

### 쌓는 순서

| # | 만드는 것 | 배우는 것 |
|---|---|---|
| 1 | `main.py` + `health.py` + `pytest.ini` | 앱 생성, `APIRouter` |
| 2 | `config.py` + `deps.py` | `Settings`, `Depends`, `Header` |
| 3 | `schemas/analysis.py` + `POST /analyses` 스텁 | pydantic 모델, 202, 422 |
| 4 | `GET /analyses/{job_id}` 스텁 | 경로 파라미터, `response_model`, 404 |
| 5 | `engines/base.py` + `stub.py` | Protocol, 의존성 주입 |
| 6 | job 수명주기 | `BackgroundTasks`, 상태 전이 |
| 7 | `sessions.py` 4개 스텁 | 세션 리소스 |
| 8 | `gradings.py` 2개 스텁 | 9개 완성 |
| 9 | 팀원 엔진 이식 | 실제 모듈화 |

1~8은 엔진 없이 전부 가능하다. 8단계가 끝나면 백엔드가 붙일 수 있다.

### 검증 방법

**이 브랜치에서는 PoC를 만들지 않는다.** Swagger(`/docs`)와 Postman으로 API 통신만 확인한다.

### 엔진 이식 절차

1. 팀원 브랜치를 `git fetch` 후 해당 파일을 읽는다
2. 프롬프트·파라미터는 `prompt_manifest.json`에서 가져온다
3. 제어 흐름만 Python 순수 함수로 옮겨 `app/engines/`에 넣는다
4. `engine_mode`를 `real`로 바꿔 스텁을 대체한다
5. 계약이 바뀌었으면 `schemas/`와 명세서를 함께 고친다

---

## 5. 실행

```bash
# 최초 1회
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 개발 서버
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload

# 테스트
./.venv/Scripts/python.exe -m pytest -q
```

Swagger UI: http://127.0.0.1:8000/docs

> **워커는 1개로 유지한다.** job 저장소가 인메모리라 `--workers 2` 이상이면 job을 만든 프로세스와 조회하는 프로세스가 달라져 404가 난다. 스케일이 필요해지면 Redis나 DB로 옮긴다.

### 설정

`.env.example`을 `.env`로 복사해 채운다. **`.env`는 절대 커밋하지 않는다.**

---

## 6. 참고

| 문서 | 내용 |
|---|---|
| `../docs/AI-Backend_API_명세서_v0.1.md` | AI↔Backend 전체 계약 |
| `../docs/docs_for_read/` | 기획·요구사항 문서 Markdown 변환본 |
| `../rule/개발/이슈 O/` | 커밋·PR 규칙, 협업 규칙 |
| `PLAN_FASTAPI_MIGRATION.md` | 진행 상황 기록 |

`../docs/`의 문서는 확정 스펙이 아니라 바뀔 수 있는 기획 자료다. 실제 코드나 최근 논의와 어긋나면 문서를 맹신하지 말고 확인 후 진행한다.

`_legacy/`는 재구축 이전 구현의 로컬 사본이다. `.gitignore` 대상이라 커밋되지 않으며 모듈화 참고용으로만 둔다. 이력에는 남아 있으므로 `git show <commit>:app/...`로 꺼낼 수 있다.

### 커밋 규칙

```
type: short description (#issue)
```

`feat` `fix` `refactor` `style` `docs` `chore` `remove` 중 하나. 동사원형 소문자로 시작, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수. PR은 제목 `[feat] add login page UI`, 본문에 `closes #번호`, 1PR=1기능. 자세한 내용은 `../rule/개발/이슈 O/Git 커밋 & PR 가이드.docx`.
