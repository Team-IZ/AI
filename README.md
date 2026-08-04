# AI 서비스 (FastAPI)

> 갱신: **2026-08-03** · **이 문서는 지금 코드가 실제로 하는 일을 적는다.**
> 백엔드와의 계약 현황은 이슈 `Team-IZ/Backend#42`, 작업 계획은 `PLAN_FASTAPI_MIGRATION.md`.

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

🔴 점수 상한은 없다 (2026-08-03 폐기)
  AI는 채점만 하고 점수를 그대로 낸다. 가공하지 않는다
```

**왜 상한을 뺐나**: DB `problem_stage`가 **질문·힌트1·힌트2 각각의 점수를 따로 저장**한다.
어느 답변이 몇 점이었는지가 그대로 남으므로 AI가 미리 눌러 담을 이유가 없다. 자력은
"어느 슬롯에서 통과했나"로 읽는다 — 질문 통과=`SELF` / 힌트1 통과=`SELF_MAINTAINED` /
힌트2 통과=`PARTIAL`.

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

> **워커는 1개로 유지한다.** job·세션 저장소가 인메모리라 `--workers 2` 이상이면 만든 프로세스와 조회 프로세스가 달라져 404가 난다. 시연 규모(동시 10~20명)에서는 제약이 아니다 — 병목은 FastAPI가 아니라 NVIDIA 무료 티어의 분당 40회다.

### 배포 — 자동이다. 신경 쓰지 않는다 (2026-08-03~)

```
https://cpiysizen3.ap-northeast-1.awsapprunner.com     고정 HTTPS. 주소가 안 바뀐다
```

**AWS App Runner가 `main` 브랜치를 물고 자동 배포한다.** 팀원이 그 계정과 설정을 소유하고
있고, **우리는 `main`에 올리기만 하면 된다.** 배포 명령도, 서버 접속도, 주소 공유도 없다.
설정 파일은 `main`의 `apprunner.yaml`(런타임 python3.11 · 포트 8080 · 헬스체크 `/api/health`).

⚠️ **`main` 머지가 곧 재배포다.** 재배포는 프로세스를 갈아치우므로 **진행 중인 job과 세션
멱등 캐시가 끊긴다.** 세션 자체는 무상태라 안 깨지지만(매 요청이 restore), 폴링 중인
`/analyses`·`/reports` job은 사라진다. **머지 시점은 백엔드와 맞춘다.**

⚠️ **인스턴스는 1개로 고정해야 한다.** job 저장소가 인메모리 dict라 2개로 늘면 만든
프로세스와 조회 프로세스가 달라져 폴링이 404가 난다.

<details>
<summary>옛 방식 — EC2 + cloudflared (2026-08-02 ~ 08-03, 폐기)</summary>

서울 EC2 t3.small에 `main`을 체크아웃해 두고 `git pull && systemctl restart iz-get-ai`로
수동 배포했다. quick tunnel URL이 cloudflared 재시작마다 바뀌어서 백엔드가 주소를 설정값으로
들고 있어야 했다. **App Runner 고정 주소로 대체됐다.**

</details>

### 백엔드와 통신 테스트 (배포 없이)

로컬 FastAPI를 터널로 노출해 확인한다. 백엔드는 서울 EC2(`http://13.209.190.34`)에 떠 있다.

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
| `Idempotency-Key` | 경로마다 다르다(아래) | 중복 요청 판별. 같은 키면 처음 만든 `jobId`를 그대로 반환하고 LLM을 다시 부르지 않는다 |
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

**모델은 코드 문자열로 주고받되 방향마다 컬럼이 다르다.** UUID(`model_id`)는 쓰지 않는다.

```
요청 → AI      providerModelCode    ai_model.provider_model_code 값 (예: "nvidia/nemotron-3-ultra-550b-a55b")
                                    공급자에게 그대로 넘길 문자열이라 벤더 접두어가 붙어 있어야 한다
응답 → Spring  aiUsage[].modelCode  AI가 호출에 쓴 provider 문자열을 그대로 에코한다
                                    **AI는 화면 선택값(model_code)을 모른다** — Spring이
                                    provider_model_code로 ai_model을 조회해 model_id를 잡는다
```

요청 필드는 네 곳 모두 같은 이름·같은 규칙이다(`/analyses` · `/curricula` · `/reports` · `/sessions/{id}/answers`). 생략하면 서버 기본값이고, 채점 모델은 operator가 고른다(GradingPolicy).

### 엔드포인트

| 그룹 | 메서드·경로 | 역할 | 방식 |
|---|---|---|---|
| 공통 | `GET /api/health` | 서비스 상태 | 동기 |
| 분석 | `POST /api/v0/analyses` | 코드 분석 요청 | 202 + 폴링 |
| 분석 | `GET /api/v0/analyses/{jobId}` | 분석 상태·결과 | 동기 |
| 세션 | `POST /api/v0/sessions/{id}/answers` | 답변 제출 → 채점 + 다음 질문 (**세션 API는 이것 하나뿐**) | 동기 |
| 보고서 | `POST /api/v0/reports` | 보고서 생성 요청 | 202 + 폴링 |
| 보고서 | `GET /api/v0/reports/{jobId}` | 보고서 결과 | 동기 |

| 교안 | `POST /api/v0/curricula` | 교안 PDF(multipart) 분석 요청 | 202 + 폴링 |
| 교안 | `GET /api/v0/curricula/{jobId}` | 교안 구조·개념 결과 | 동기 |

**세션은 무상태다** (2026-08-03 확정). `POST /sessions` · `GET /sessions/{id}` · `POST /sessions/{id}/restore` **3개는 삭제됐다.** AI는 세션을 들고 있지 않으므로 문제·기록·커서가 매 요청에 실려 오고 — **매 요청이 곧 restore**다. 배포·재시작·인스턴스 중지가 진행 중인 세션을 깨지 않고, 백엔드는 갈래 하나만 구현하면 된다.

**첫 질문은 백엔드가 낸다.** 질문 12개·힌트 24개는 분석 배치에서 동결돼 이미 DB에 있으므로 첫 질문을 받으려고 AI를 부를 필요가 없다. AI는 답변이 올 때부터 관여한다.

`Idempotency-Key`는 `/analyses`(`{submissionId}:{attemptNo}`) · `/curricula`(`{versionId}:{analysisVersion}`) · `/reports`(`{problemId}:{scoreRunId}`)에서 받는다. 같은 키면 처음 `jobId`를 그대로 돌려주고 **LLM을 다시 부르지 않는다.** 세션 `/answers`는 헤더 대신 본문의 `clientRequestId`를 쓴다.

**콜백은 없다.** 요청에 `callbackUrl`을 받지 않는다 — 전부 202 + 폴링이고 AI→백엔드 방향 통신은 0이다.

### 분석

```jsonc
// POST /api/v0/analyses          Content-Type: application/json 또는 multipart/form-data
{
  "attemptId": "att-1", "submissionId": "sub-1",
  "method": "GITHUB_URL",                    // 또는 ZIP_WITH_GITLOG (multipart로 ZIP 첨부)
  "source": { "repoUrl": "https://github.com/owner/repo", "branch": "main" },
  "source": { "repoUrl": "https://github.com/...", "branch": "main" },
  "extractionScope": "TOTAL",                // 또는 OWN_COMMIT (이때 commitEmail 필수)
  "questionBudget": 3,
  "providerModelCode": "nvidia/nemotron-3-ultra-550b-a55b",   // 생략 시 서버 기본값
  "focusItems": [                            // 강사가 체크포인트에 지정한 질문 초점 후보
    { "id": "a3f2-…", "name": "예외 처리" },
    { "id": "b7c1-…", "name": "동시성" }
  ],
  "requirements": [ { "requirementId": "req-1", "text": "로그인 실패 3회 시 잠금" } ],
  "teaches": [ { "id": "tch-1", "label": "의존성 주입", "unitId": "u3", "sourcePages": [12, 13] } ]
}
// → 202
{ "jobId": "1b40467e-…", "status": "QUEUED" }
```

**`GITHUB_URL`은 AI가 서버에서 `git clone --depth 1`로 받는다** (2026-08-03). GitHub API로 파일을 긁지 않는다 — 비인증 한도가 IP당 60회/시간이라 **같은 망의 다른 교육생까지 막히고**, 큰 repo는 tree API가 목록을 잘라 소스가 조용히 빠진다(팀 PoC 실사고). **공개 레포만** 되고, 얕은 클론이라 `.git`은 남지만 커밋이 tip 하나뿐이라 `OWN_COMMIT`은 못 한다. `commitSha`는 이 경로에서만 실제 값이 채워진다.

🔴 **`PARTIAL`이 실제로 나온다.** 요구사항 판정만 실패하고 문제·질문·힌트는 정상으로 나가는 경우다(2026-08-03 신설). 그때 `requirementResults`는 개수를 맞춰 오되 `verdict: "F"` + `note: "판정 실패: …"`로 채워진다 — **`SUCCEEDED`로 저장하면 화면에 "요구사항 전부 미충족"이 사실처럼 뜬다.** 문답은 그대로 진행할 수 있다.

**`focusItems`를 Spring이 보내주는 이유**: `question_focus_item`의 PK가 랜덤 UUID라 AI가 그 값을 알 방법이 없다. 후보를 받아 **AI가 그중 하나의 `id`를 그대로 돌려준다.** 강사 지정 범위를 벗어날 수 없고, 항목 이름이 바뀌어도 AI 재배포가 필요 없다.

```jsonc
// GET /api/v0/analyses/{jobId}   → 200
{
  "jobId": "1b40467e-…", "attemptId": "att-1", "submissionId": "sub-1",
  "status": "SUCCEEDED",                     // QUEUED|RUNNING|SUCCEEDED|PARTIAL|FAILED
  "failureReason": null,                     // PARTIAL·FAILED면 사유가 들어간다
  "startedAt": "2026-07-30T05:00:00Z", "completedAt": "2026-07-30T05:00:12Z",
  "result": {
    "snapshotId": "snap-1",
    "snapshotMeta": { "contentHash": "…64자…", "fileCount": 42, "byteCount": 183920 },
    "appliedScope": "TOTAL", "scopeFallback": false, "fallbackReason": null,
    "commitSha": null,
    "analysisDocument": {                          // code_analysis.analysis_document (JSONB)
      "overview": "결제 흐름을 …",
      "structure": [ { "area": "진입점", "files": ["app/main.py"], "role": "…" } ],
      "decisionPoints": [
        { "title": "결제 수단 분기", "sourcePath": "app/main.py",
          "symbol": "def pay(order, method):", "lineStart": 12, "lineEnd": 20,
          "whyItMatters": "…", "relatedTeachId": null, "evidenceValid": true }
      ],
      "risks": ["…"]
    },
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

`analysisDocument`는 **Markdown이 아니라 JSON이 원본이다.** 문제 선정·보고서 생성이 이 객체를 그대로 프롬프트에 다시 넣는다. 사람이 읽는 화면은 이걸 렌더한 결과다.

`decisionPoints[].lineStart`/`lineEnd`는 **LLM이 센 값이 아니다.** LLM은 `symbol`(소스에 실제로 있는 코드 한 줄을 문자 그대로 복사한 것)만 주고, 그 문자열을 실제 파일에서 찾아 우리가 산정한다. 못 찾으면 `evidenceValid: false`로 남기고 줄 번호를 비운다 — **`evidenceValid: false`인 항목은 근거로 쓰지 않는다.** 스키마가 그 조합(무효인데 줄 번호 있음)을 막는다(OpenAPI로는 표현되지 않는 제약이다).

`problems[]`는 DB `assessment_problem`(+ `problem_reference`) 테이블에 대응하므로 **컬럼 이름을 그대로 쓴다.**

```jsonc
{
  "problemId": "…",
  "problemNo": 1,                      // 1~3
  "status": "READY",                   // READY | IN_PROGRESS | COMPLETED | TERMINATED
  "problemType": "DESIGN_CHOICE",      // 아래 5종
  "priority": 0.91,
  "questionFocusItemId": "a3f2-…",     // 요청 focusItems에서 고른 id
  "teachId": "tch-1",                  // 이 문제가 검증하는 개념. **항상 채워진다**
  "sourcePath": "app/main.py",
  "lineStart": 12, "lineEnd": 14,
  "codeSnippet": "…파일 전체…",         // 🔴 문제를 낸 파일 전체 (화면에 띄울 것)
  "evidenceHash": "…",
  "extractorVersion": 1739284412,      // 정수. INTEGER CHECK (> 0)
  "references": [                      // DB assessment_problem_reference 와 1:1
    { "referenceType": "PRIMARY_BLOCK", "displayOrder": 1,
      "path": "app/main.py", "lineStart": 12, "lineEnd": 14, "evidenceHash": "…" },
    { "referenceType": "QUESTION_HIGHLIGHT", "displayOrder": 2, "axisCode": "L1",
      "path": "app/main.py", "lineStart": 12, "lineEnd": 14, "evidenceHash": "…" },
    // L2·L3·L4 동일. 축별로 한 행씩 = 4개
    { "referenceType": "CURRICULUM_EVIDENCE", "displayOrder": 6,
      "teachId": "tch-1", "evidenceHash": "…" },     // 코드 라인이 없다
    { "referenceType": "CALLER", "displayOrder": 7,
      "path": "app/cli.py", "lineStart": 1, "lineEnd": 1, "evidenceHash": "…" }
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

🔴 **`codeSnippet`은 문제를 낸 파일 전체다** (2026-08-03 확정). 파편만 주면 학생이 판단할 재료가 없다 — 실측에서 L2 질문이 `checkOut`·`checkIn`을 언급하는데 화면엔 선언 한 줄(41자)만 뜨는 일이 났다. **보여줄 구간은 `lineStart`~`lineEnd`**이고 이 값은 codeSnippet의 부분범위가 아니라 **파일 기준 절대 줄 번호**다. 파일이 100,000자를 넘으면 파편만 보낸다(그때도 줄 번호는 파일 기준).

**`evidenceHash`는 파일 전체가 아니라 파편(`lineStart`~`lineEnd`) 기준이다.** 파일 전체 기준이면 무관한 한 줄 수정에도 "근거가 바뀌었다"가 되어 판정이 쓸모없어진다. Spring이 따로 잘라 해시를 다시 만들면 안 된다 — 줄바꿈·BOM이 1바이트만 달라도 안 맞는다.

**채점에는 파일 전체를 넣지 않는다.** 매니페스트 `code_block` 상한이 4,000자라 큰 파일은 앞에서부터 잘리고, 문제 구간이 파일 뒤쪽이면 근거가 사라진 채 채점된다. `sessions._grading_code()`가 줄 범위로 ±8줄을 되잘라 쓴다.

**`extractorVersion`은 정수다** — `assessment_problem.extractor_version`이 `INTEGER CHECK (> 0)`이다. 값은 룰 vendor의 `.py`+`.json` 전부를 해시해 산정한 값이라 **같은 룰이면 같은 값, 데이터 파일만 바뀌어도 다른 값**이다(재현성 근거). 사람이 읽는 버전 번호가 아니고 순서도 없다.

**`teachId`는 문제↔개념 연결이고 항상 채워진다.** 강사가 teach 3개(클래스·상속·캡슐화)를 고르면 문제도 그 셋에 하나씩 붙고, 결과 화면의 "클래스는 L3까지 도달 / 상속은 L2까지 도달"이 이 값으로 그려진다.

🔴 **개념이 코드에 없으면 문항을 만들지 않는다** (2026-08-03 PM 결정). 다른 개념으로 갈아끼우거나 지어내지 않는다 — **모든 학생이 오퍼레이터가 고른 같은 개념을 시험 본다**는 것이 비교 가능성의 근거다.

```
① 최대한 찾는다        p04-3 으로 고르고, 실패한 teach 만 모아 재시도 1회
                      ("파일에 실제 존재하는 선언·호출 문자열을 그대로 써라")
② 그래도 없으면        그 개념은 문항 없음 → unmatchedTeaches 에 담아 보낸다
```

```jsonc
"problems": [ /* 근거를 찾은 개념만. 2개일 수 있다 */ ],
"unmatchedTeaches": [
  { "teachId": "t-상속", "reason": "제출 코드에서 이 개념의 근거를 찾지 못했습니다" }
],
"questionCountPlanned": 3
```

**`unmatchedTeaches`를 명시적으로 보내는 이유**: `problems` 길이 차이로는 "몇 개가 없다"까지만 알 수 있고 **어느 개념이 빠졌는지**는 역산해야 한다. 화면의 개념별 도달 격자에서 `―`(문항 없음)로 그릴 값이다.

🔴 **`―`(문항 없음)과 `0단`(L1 미달)은 다른 것이다.** 도달 단계에 0을 박으면 "안 물어봤다"가 "틀렸다"로 바뀐다 — 문항 없음은 NULL이다.

⚠️ **`isGeneral` 필드는 2026-08-03에 삭제됐다.** teach 앵커 없는 "일반 문제"를 만들지 않기로 했다.

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
// POST /api/v0/sessions/{id}/answers        헤더: X-Trace-Id(선택)
{
  "clientRequestId": "turn-7",
  "answerText": "재귀 대신 반복문으로 바꿨습니다",
  "problems": [ /* 분석이 동결한 문제 3개. 질문 4개 + 힌트 8개가 문제마다 실려 있다 */ ],
  "transcript": [ /* 지금까지 확정된 턴 전부 */ ],
  "cursor": { "problemId": "prob-1", "axisCode": "L3", "hintsUsed": 0 },
  "providerModelCode": "deepseek-ai/deepseek-v4-flash",       // 생략 시 서버 기본값
  "analysisContext": {                       // 선택. 분석 문서에서 두 필드만
    "overview": "주문을 받아 결제로 넘기는 …",
    "structure": [ { "area": "컨트롤러", "files": ["app/api.py"], "role": "요청 수신" } ]
  }
}

// → AnswerResult
{
  "sessionId": "sess-abc",
  "state": "IN_PROGRESS",                    // DB assessment_session.status 8종. AI는 IN_PROGRESS|COMPLETED만 낸다
  "turn": {                                  // 이번에 채점된 턴. 이것만 이어 붙여 저장하면 된다
    "problemId": "prob-1", "axisCode": "L3", "questionText": "…", "answerText": "…",
    "answeredAt": "…", "score": 4, "passed": true,
    "hintsUsed": 0, "hintText": null       // hintsUsed가 어느 슬롯에 저장할지를 정한다
  },
  "cursor": { "problemId": "prob-1", "axisCode": "L4", "hintsUsed": 0 },  // 다음 요청에 그대로 실는다
  "current": {                               // 다음 질문. 끝났으면 null
    "problemId": "prob-1", "axisCode": "L4", "sequenceNo": 1, "hintsUsed": 0,
    "questionText": "…",
    "hintText": null,                        // 3점 미만이었으면 동결분에서 꺼내 채운다
    "codeContext": { "path": "src/Solver.java", "lineStart": 42, "snippet": "…" }
  },
  "progress": { "problemIndex": 1, "problemTotal": 3 },
  "terminationReason": null,                 // 이 턴에 문제가 끝났을 때만 채워진다
  "endedLevel": null,
  "aiUsage": [ /* §aiUsage */ ]
}
```

**요청이 상태를 들고 온다.** AI는 아무것도 기억하지 않으므로 `problems`가 없으면 물을 것이 없다(빈 채로 두면 `COMPLETED`가 나간다). `cursor`를 생략하면 `transcript`를 되짚어 위치를 복원하고, 둘 다 없으면 첫 문제의 L1로 본다.

**응답은 `transcript`를 돌려주지 않는다.** 요청이 이미 들고 온 것이라 되돌리면 같은 payload를 두 번 실어 나른다. 이번 턴(`turn`)만 이어 붙이면 된다.

`clientRequestId`는 세션 내 유일한 멱등키다. 같은 키로 재요청하면 처음 돌려준 응답을 그대로 반환한다. 채점이 실패하면 **503 `GRADING_UNAVAILABLE`(retryable=true)**이고 그 턴은 기록되지 않으므로 같은 키로 재전송하면 된다.

**진행 규칙은 AI가 소유한다** — 통과선 3점 · 힌트 단계당 2회 · 점수 상한 5/4/3 · 사다리(통과→다음 축 / 미달→힌트 / 소진→다음 문제). 백엔드는 커서를 왕복시키기만 하면 되고 같은 규칙을 다시 구현하지 않는다.

**종료 사유도 AI가 말한다.** 문제가 끝나는 판정이 AI 쪽에 있으므로 사유도 여기서 나간다 — 백엔드가 "커서가 다음 문제로 넘어갔으니 끝났나 보다"로 역추론하지 않는다. `assessment_problem.termination_reason`·`ended_level`에 그대로 들어간다.

```
COMPLETED_L4        L1~L4 전부 통과 (완주)        endedLevel = "L4"
TERMINATED_AT_L3    L3에서 힌트 소진 후 미달       endedLevel = "L3"
TERMINATED_AT_L2    L2에서                        endedLevel = "L2"
TERMINATED_AT_L1    L1에서                        endedLevel = "L1"
TERMINATED_AT_L4    L3까지 통과하고 L4에서 막힘     endedLevel = "L4"
```

문제가 안 끝난 턴이면 둘 다 null이다. ⚠️ 코드값 `MEAS.PROBLEM_TERMINATION_REASON`에 `TERMINATED_AT_L1`·`TERMINATED_AT_L4`가 아직 없다(백엔드 B-4). CHECK 제약이 없어 값 자체는 통과한다.

**`analysisContext`는 분석 문서 전체가 아니라 `{overview, structure}` 두 필드다.** 코드 파편 하나로는 전체 흐름이 안 보여서(MVC면 model·view·controller가 다른 파일에 있다) 채점기가 학생 답변의 사실 여부를 못 가린다. 그렇다고 문서를 통째로 넣으면 채점 36회 × 5,000~7,000토큰이다 — `decisionPoints`는 문제 후보 목록이라 문제가 이미 정해진 채점에는 쓸모가 없고 부피의 대부분이다. 두 필드만 뽑으면 500~800토큰이다. **생략하면 파편만으로 채점한다**(기존 동작).

**타이머는 AI 계약에 없다.** 문제당 20분·문제가 바뀌면 리셋·AI 호출 대기 중 정지는 프론트/백엔드가 소유한다 — AI는 그 값을 쓰지 않았다.

`state`는 DB `assessment_session.status`의 허용값을 따른다. **`TIMEOUT`은 폐기값이다** — 시간 초과는 `EXPIRED`다.

**점수 필드가 wire에 나오는 이유**: Spring이 매 턴 저장하고, 세션이 유실되면 그 기록으로 복구한다. 점수·시도 횟수가 wire에 없으면 "이 학생이 힌트를 몇 번 썼는지"를 아무도 모르게 된다.

```
score       0~5 정수. **상한 적용 없는 루브릭 원점수다** (2026-08-03 상한 폐기)
passed      score >= 3.  DB CHECK가 `passed=TRUE AND score>=3` 을 강제한다
hintsUsed   0~2.  **어느 슬롯에 저장할지를 이 값이 정한다**
            0 → question_answer_text / 1 → first_hint_* / 2 → second_hint_*
```

🔴 **`attemptCount`·`autonomy`는 없다.** DB `problem_stage`가 한 행에 답변 3개를 담게
되면서(2026-08-03) 둘 다 파생값이 됐다 — 어느 슬롯이 찼는지, 어느 슬롯이 통과했는지로
계산된다.

### aiUsage — 모든 응답에 붙는다

DB `ai_usage`(기관별 AI 호출·토큰·비용 원장)에 대응한다. **LLM 호출 1건 = 배열 원소 1개**이므로, 코드 분석 응답에는 6개가 들어간다.

```jsonc
"aiUsage": [
  {
    "idempotencyKey": "sub-1:1:ANALYSIS:3",        // {요청 멱등키|sourceId}:{sourceType}:{호출순번}
    "sourceType": "ANALYSIS",                      // ANALYSIS|GRADING|REPORT|CURRICULUM
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

`sourceType` 4종 — `ANALYSIS`(분석 jobId) · `GRADING`(sessionId) · `REPORT`(보고서 jobId) · `CURRICULUM`(교안 jobId). `featureCode`보다 굵은 단위라 한 `sourceType` 안에 `featureCode`가 여럿 나온다(분석 하나에 `CODE_ANALYSIS` + `QUESTION_GENERATION`).

`failureCode` 5종 — `TIMEOUT` · `RATE_LIMITED` · `PROVIDER_ERROR` · `INVALID_JSON` · `CONTEXT_OVERFLOW`.

**필드 이름이 DB 컬럼명과 1:1이다.** Spring은 매핑 고민 없이 그대로 INSERT하면 된다.

DB CHECK 제약 둘을 AI가 지켜서 보낸다 — `cachedTokenCount <= inputTokenCount`, 그리고 `status`가 `SUCCEEDED`면 `failureCode`는 null이고 `FAILED`·`PARTIAL`이면 반드시 채워진다.

**단가·비용은 AI가 보내지 않는다.** AI가 모델 단가표를 들고 있으면 단가가 바뀔 때마다 재배포해야 한다. `ai_model` 테이블을 가진 Spring이 토큰 수에 곱한다.

**실패한 호출도 기록한다.** 실패해도 토큰·비용이 발생하고 재시도 통계에 필요하다.

### 보고서

**보고서는 문제 단위다.** 문제 하나가 끝날 때마다 한 번씩 부르므로 세션 1회에 보고서 3개다.

```jsonc
// POST /api/v0/reports          → 202
{ "problemId": "prob-1", "problemNo": 2,                      // 🔴 안 보내면 3건 다 1이 찍힌다
  "sessionId": "sess-abc",
  "providerModelCode": "deepseek-ai/deepseek-v4-flash",       // 생략 시 서버 기본값
  "transcript": [ /* 이 문제의 턴만 */ ],
  "analysisDocuments": [ { "kind": "CODE_ANALYSIS", "content": { /* AnalysisDocument */ } } ],
  "teaches": [ … ] }
{ "jobId": "…", "status": "QUEUED" }
```

```jsonc
// GET /api/v0/reports/{jobId}   → 200 (result 부분)
{
  "reportMarkdown": "…",                     // 사람이 읽는 본문 (헤딩 5개 고정)
  "narrative": {                             // 같은 내용의 구조화본. 프론트가 파싱하지 않도록
    "summary": "…",
    "strengths": [ { "axis": "캡슐화", "detail": "…", "teachId": null, "studyPointer": null } ],
    "gaps":      [ { "axis": "캡슐화", "detail": "…", "teachId": "tch-1", "studyPointer": "…" } ],
    "autonomyNote": "…",
    "unreachedAxes": ["L3", "L4"]            // 앞 단계에서 끝나 안 물어본 축
  },
  "narrativeFailed": false,                  // true면 narrative가 전부 빈다. 판정은 그때도 확정값
  "problem": {
    "problemNo": 1, "problemId": "…",
    "reachedStage": 2,                       // 0~4. 앞에서부터 연속 통과한 단계 수
    "stages": [                              // 항상 4개. DB problem_stage 4행과 1:1
      { "axisCode": "L1", "questionScore": 5, "questionPassed": true,
        "firstHintScore": null, "firstHintPassed": null,
        "secondHintScore": null, "secondHintPassed": null, "status": "PASSED" },
      { "axisCode": "L2", "questionScore": 1, "questionPassed": false,
        "firstHintScore": 4, "firstHintPassed": true,     // 힌트 1개로 통과
        "secondHintScore": null, "secondHintPassed": null, "status": "PASSED" },
      { "axisCode": "L3", "questionScore": null, "status": "NOT_REACHED" }
      // L4도 NOT_REACHED. 미도달 축은 점수가 전부 null이다
    ]
  },
  "curriculumRefs": [ { "teachId": "tch-1", "unitId": "u3", "sourcePages": [12, 13] } ],
  "retest": true,
  "versions": { "modelCode": "…", "promptVersion": "p04-6", "rubricVersion": "…" }
}
```

**총점이 없다.** 이 구인은 보상을 허용하지 않고(자기 코드를 설명 못 하는데 대안을 잘 알아 총점이 높은 상태는 성립하면 안 된다), 총합은 결측을 0으로 만든다(안 물어본 단계와 못한 단계가 섞인다). 판정값은 `reachedStage`다.

**세션 총점·축 평균도 보내지 않는다.** 점수는 이미 `problem_stage`에 매 턴 저장되어 있고, 요약이 필요하면 Spring이 집계한다. LLM이 아니면 못 만드는 것(서술형 진단·교안 매핑)이 `/reports`의 본체다.

**재시험 판정은 문제 단위**다 — `retest`는 이 문제 하나에 대한 값이고(L1·L2 둘 다 통과해야 false), 세션 전체의 재시험 여부는 Spring이 문제 3개의 값을 모아 판단한다.

### 교안 분석

PDF가 항상 필요하므로 **multipart 하나만 받는다**(`payload` 문자열 + `file`).

```jsonc
// POST /api/v0/curricula        multipart/form-data → 202
// payload = {"versionId": "ver-1", "courseLabel": "Java",
//             "providerModelCode": "minimaxai/minimax-m3"}
// file    = 교안 PDF
{ "jobId": "…", "status": "QUEUED" }
```

🔴 **`courseLabel`은 필수다** (2026-08-03). 생략하면 매니페스트 기본값 `'Java'`가 들어가
다른 과정 교안에서 결과 언어·용어가 섞인다. 교안 업로드 화면이 과정을 이미 알고 있으므로
기본값을 두지 않는다.

**`teaches[]`가 문제 선정의 재료다.** `kind`·`evidence`·`siblingNames` 셋은 화면에 안 띄워도
되지만 저장해뒀다가 `POST /analyses`에 실어 보내야 한다.

```
kind: CODE_EXAMPLE   코드 식별자 추출원 (st.title · function_tool 같은 이름이 여기서 나온다)
kind: CAUTION        L4(언제 깨지는가) 재료 · 선별 순서 신호
evidence             추가 식별자 추출원 — 정의 문장보다 코드 이름이 많다
siblingNames         같은 unit의 다른 개념 = 교안이 대안을 가르쳤다는 신호
```

이 셋은 **p01-2가 이미 답에 담아 보내던 값**이라 LLM 호출이 늘지 않는다(`siblingNames`는
unit 묶음에서 계산). 방향은 PM 설계 v2 §7 — 코드를 훑어 "중요해 보이는 곳"을 고르는 대신
**교안에서 식별자 사전을 만들어 코드에서 찾는다.**

**결과는 항상 한국어다.** 매니페스트(vendor)에 언어 지시가 없어 영어 교안을 넣으면 영어로
나왔다 — 우리 소유 경로(`stages.call(extra_user=...)`)로 지시를 붙였다. `unitTitle`·
`canonicalDescription`이 한국어가 되고, **기술 용어·API 이름·코드 식별자는 원문을 유지한다**
(예: `guardrail(안전장치)`, `deterministic`). 억지로 옮기면 나중에 그 개념으로 문제를 낼 때
교안 원문과 대조가 안 된다.

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
          "descriptionPageStart": 3, "descriptionPageEnd": 5,
          "kind": "CONCEPT",                      // CONCEPT | CODE_EXAMPLE | CAUTION
          "evidence": "try 블록에서 …",            // 페이지 근거 요약
          "siblingNames": ["finally", "raise"] },  // 같은 unit의 다른 개념
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
├─ sessions.py      문답 세션 진행 규칙 + 채점. **무상태** — 상태는 요청이 들고 온다
├─ reports.py       보고서 job 인메모리 저장소 (jobs.py와 형제)
├─ usage.py         ai_usage 원장 행 만들기. 네 경로가 함께 쓴다
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
| 엔드포인트 | **8/8 동작** (세션 무상태 전환으로 11 → 8) |
| 테스트 | **198 passed** |
| 붙일 수 있나 | **예.** 인증·에러 형식·camelCase·Swagger·`openapi.json`까지 완성 |

**엔진 이식이 끝났다.** 룰 스캔 → 분석 문서 → 요구사항 P/F → 문제 선정 → 4축 질문·힌트 동결 → 채점 → 보고서까지 실호출로 동작을 확인했다(`app/engines/analysis/`). 팀원 PoC 규칙부와 NVIDIA 클라이언트는 vendor해 두고 우리 래퍼가 감싼다 — 갱신 절차는 `vendor/SOURCE.md`, 우리 수정 이력은 `vendor/PATCHES.md`.

> ✅ 축 값 `"L1"`~`"L4"`(L3=대안 비교 / L4=반례·한계), `focusItems`, `codeSnippet`, `requirementResults`, `analysisDocument`(JSON)가 전부 스펙에 있다. ⚠️ `bestScore`/`confirmedScore`는 2026-08-03에 `score` 하나로 합쳐졌다.
>
> ⚠️ **질문·힌트는 전면 동결이다.** 혼합 모드(L1·L2만 동결)는 폐기됐다 — **4축 전부** 분석 배치에서 만든다.
>
> ```
> L1~L4   questionText 필수 · hints 정확히 2개(hintLevel 1, 2 순서)
> ```
>
> 이 규칙은 **OpenAPI 문법으로 표현이 안 된다.** 스키마 검증기가 막고 있고 여기 산문으로만 적혀 있다.
>
> ⏳ **아직인 것**: 백엔드 연동, `references[]` 채우기. 배포·주소는 해결됐다(App Runner 자동 배포). 순서는 `PLAN_FASTAPI_MIGRATION.md`.

### 백엔드 대기 2건

이슈 `Team-IZ/Backend#31` 본문이 현재 상태판이다.

| # | 내용 | 우리 작업을 막나 |
|---|---|---|
| C-4 | ~~`source_type` 값 목록~~ | **닫힘.** 2026-08-03에 우리가 `ANALYSIS`·`GRADING`·`REPORT`·`CURRICULUM`로 정해 통보한다 |
| C-5 | `curriculum_analysis.extraction_status`·`quality_status` 코드 카탈로그 | 아니다. CHECK가 없어 `str`로 두고 나중에 맞춘다 |

C-1~C-3은 **회신 완료**다.

- **C-1** 분석 요청에 `focusItems: [{id, name}]`를 싣고 AI가 `questionFocusItemId`로 하나를 돌려준다
- **C-2** `score_run`·`axis_score` 제거 예정. 점수의 단일 소유자는 `problem_stage`이고 축 어휘는 `'L1'`~`'L4'` 한 벌
- **C-3** **비용은 Spring이 계산한다.** AI는 토큰·모델·지연·상태만 보낸다. 백엔드에 단가 관리 화면(`PUT /platform/operations/models/{modelId}/pricing`)과 비용 집계 화면이 이미 있는 것을 2026-08-03에 확인했다

**DDL 요청은 2건이고 전달본은 `../qna/2026-08-03/issue-body-v2.md`(= 이슈 `Team-IZ/Backend#42`)다.**

```
B-12  🔴 assessment_problem_reference 에 code_text TEXT
      학생에게 보여줄 코드가 DB 어디에도 안 들어간다
B-13  🔴 code_analysis 에 analysis_document JSONB
      문제 출제 근거이자 채점 컨텍스트가 사라진다
```

⚠️ **옛 요청 목록(B-1~B-11)은 폐기다.** `테이블정의서_v06` 기준이었고, 백엔드가 새 MEAS에서
`problem_stage`를 다시 짜면서 대부분 해결됐다.

```
해결   B-9  질문·힌트 저장 자리    question_text · first_hint_text · second_hint_text 가 이미 있다
       B-1  attempt_count 0~3     컬럼이 사라지고 답변 슬롯 3개로 대체
       B-2  attempt_no IN (2,3)   stage_answer_attempt 테이블 자체가 사라짐
       B-4  TERMINATED_AT_L1·L4   termination_reason 컬럼이 사라짐
철회   B-10 teach_id · is_general  일반 문제를 안 만들기로 했다 (2026-08-03 PM)
       B-5  TEXT → JSONB          컬럼이 통째로 사라져 B-13 신설로 바뀜
       B-3 · B-8                  2026-08-03 이전에 이미 철회
해결   B-11 ai_model 등록          2026-08-04 3건 등록 완료
       B-12 code_text · B-13 analysis_document JSONB   컬럼 추가 완료
       teaches kind·evidence·sibling_names             컬럼 추가 완료
```

**확인 완료**(2026-08-04): 도달 단계는 팀원 개별 속성 · 문항 없음은 `NOT_GENERATED` ·
`contextType` 값 집합.

### 앞으로

```
백엔드 연동 — 주소를 설정값으로, 채점 타임아웃 30초 이상
RELATED_CONTEXT 근거 — 심볼 테이블이 없어 아직 못 만든다
유료 전환 — 529 실패율 64%. 채점 목표 15초를 못 지키는 원인 (팀 논의)
교안 소요 실측 — 34쪽 451초. 200쪽 상한을 아직 못 준다
(먼 항목) 적응형 힌트 모듈 대응 — 턴당 2콜, 힌트용 featureCode, 체크포인트 단위 모드 고정
```

완료: 세션 턴 점수 필드 · `aiUsage` 스키마·배선 · `/curricula` 신설 · 엔진 이식 · **세션 무상태 전환**(2026-08-03).

**미확정값** — 재시험 커트라인(L1·L2 기준은 가설이다). 힌트 점수 상한은 2026-08-03에 폐기됐다. 세부 순서·방법은 **`PLAN_FASTAPI_MIGRATION.md`**에 있다.

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
