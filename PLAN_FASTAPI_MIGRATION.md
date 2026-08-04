# AI 파트 작업 계획

> 갱신 **2026-08-04** · 브랜치 `feature/stabilize` (`develop`에서 분기)
> **실행용 문서다.** 무엇을 어떤 순서로 할지만 적는다. 구조·계약 설명은 `README.md`,
> 기계용 계약은 `openapi.json`. 백엔드 협의는 이슈 `Team-IZ/Backend#42`.

---

## 현재 위치

| | |
|---|---|
| 기능 | **6/6 완성.** 교안 · 코드 분석 · 문제 선정 · 질문·힌트 동결 · 채점 · 보고서 |
| 검증 | **전 구간 실호출 완주** (2026-08-04). 교안 → 분석 → 문답 11턴 → 보고서 2건 |
| 엔드포인트 | **8개**. 세션 무상태 전환으로 11 → 8 |
| 테스트 | **254 passed** |
| 제출 | ZIP · GitHub 링크 둘 다. 링크는 서버에서 `git clone --depth 1` |
| 배포 | App Runner 자동(`main` 푸시 = 배포). 주소 고정. 팀원 소유 — 우리 작업 아님 |
| 계약 | **미결 0건.** DDL·값 집합·`problemId` 사본 여부까지 전부 확정(2026-08-04) |
| 다음 | **§T13 백엔드 연동** — 백엔드가 AI 연동 도메인을 만든 뒤 |
| 기준 | **§기능 동결 스펙** · **§계약 기준값**. 앞선 절과 충돌하면 이 둘이 이긴다 |
| 🔴 위험 | **무료 티어 529.** 채점 목표 15초를 못 지키는 원인. 유료 전환이 근본 해결 |

### 실측값 (2026-08-04, 무료 티어 · 전 구간 실호출)

```
/curricula   34쪽 PDF   310초   섹션 15 · teach 58 · LLM 9콜
/analyses    7파일      221초   문제 2 + 문항없음 1 · LLM 22~25콜 · 5회 재현
채점         성공 콜 중앙 14.5초 (8.6~19.0) · 턴 왕복 중앙 21.3초 · 최대 141.9초
/reports     2건 병렬 50.5초 (순차였다면 101초) · 콜당 입력 1,438 · 출력 247
```

🔴 **성공 콜은 목표 안에 든다. 재시도가 그 위에 얹힌다.** 채점 성공 11콜 중 8개가 15초
이내였는데 턴 왕복 중앙값은 21.3초다 — 차이가 전부 529다. 실패 콜은 중앙 0.7초(즉답)라
토큰은 안 먹지만 6회 붙으면 쌓인다. **모델 교체로 안 풀린다** — 12종 실측(§완료 이력 T14b).

⚠️ **배치 소요를 예측값으로 쓰지 않는다.** 같은 프롬프트에서 p04-1이 36 → 115 → 195초로
흔들렸다. 교안은 청크 수(= PDF 글자 수)에 비례하고, 코드 분석은 프롬프트를 12,000자로
자르므로 코드 크기에 거의 안 비례한다.

---

## 🔴 기능 동결 스펙 (2026-08-02 최종. 이 절이 기준이다)

앞선 절들과 충돌하면 **이 절이 이긴다.**

### 확정된 4개 흐름

```
1. 교안 분석    operator가 교안 PDF 업로드 → 백그라운드 처리
                  3계층 반환: curriculum_analysis → curriculum_section → teaches
2. 코드 분석    팀원 한 명이 팀 코드 업로드 → 제출 1건당 1회
                  ① 코드 분석 문서화 (JSON)
                  ② operator가 설정한 요구사항 P/F 판정
                  ③ 선택된 teach 3개 → 문제 3개 (teach 1개당 문제 1개)
                  ④ 문제마다 L1~L4 질문 4개  = 질문 12개
                  ⑤ 질문마다 힌트 2개        = 힌트 24개
                  ②~⑤ 전부 미리 만들어 저장한다
3. 코드 문답    저장된 문제·힌트를 꺼내 쓴다. AI는 답변 채점만 한다
4. 보고서       문제 하나가 끝날 때마다 1개 생성. 세션 1회 = 보고서 3개
                  학생이 다음 문제를 푸는 동안 병렬로 돌린다
```

### ① 전면 동결 — 세션 중 LLM 호출은 채점 하나뿐

```
L1~L4  질문 동결 · 힌트 동결   전부 분석 배치에서 만들어 DB에 저장
세션    저장분을 꺼내 쓰기만 — AI는 답변 채점만 한다
```

배치는 **질문 12 + 힌트 24**. 문제 3 × 축 4 = 질문 12, 질문마다 힌트 2 = 24.

**왜 전면 동결인가**: ⓐ 세션 중 생성 배선이 통째로 사라진다 ⓑ **무료 티어 실패율 64%에서 학생을 기다리게 하며 LLM을 세 번 부르는 구조가 가장 위험하다** — 배치는 실패해도 재시도하면 되고 아무도 안 기다린다 ⓒ 백엔드가 문답 도메인을 아직 하나도 안 만들어서 "전부 미리 저장, 세션 중엔 꺼내 쓰기"가 가장 단순한 계약이다.

**대가**: L3·L4가 학생 답변을 겨냥하지 못해 질이 떨어진다. 속도 개선 단계에서 되돌린다 — `hints.py`가 두 모드를 다 갖고 있어(`attempts=[]`면 동결) 비용이 낮다.

**비교 가능성 근거는 "힌트 텍스트 동일"이 아니라 "사다리 강도·횟수·점수 상한(5/4/3)이 동일"이다.** 전면 동결은 이 조건을 더 강하게 만족한다.

⚠️ **축별 규칙(L1·L2만 동결 / L3·L4는 null)은 폐기됐다.** 4축 전부 질문 1개 + 힌트 2개다.

### ② 보고서는 문제 단위다

세션 1회 = **보고서 3개**. 문제 하나가 끝날 때마다 1개.

"병렬"의 뜻: 학생이 **다음 문제를 푸는 동안** 백그라운드로 돈다.

⚠️ **마지막 문제의 보고서만 학생이 기다린다.** 앞의 둘은 2분이 걸려도 체감 0이다. 보고서 지연 예산이 의미 있는 건 마지막 하나뿐이다.

### ③ 시험지는 팀당 한 벌 — 학생은 복사해 쓴다

분석은 **제출 1건당 1회**. 팀원 A·B·C 모두 **같은 문제 3 · 같은 질문 12 · 같은 힌트 24**를 받는다.

```
AI가 만드는 것    시험지 1벌 (문제 3 · 질문 12 · 힌트 24)
백엔드가 하는 것   학생 수만큼 복제해 각자 세션에 물린다
```

🔴 **팀원마다 `/analyses`를 부르면 안 된다.** 비용이 팀원 수배가 된다.

⚠️ `assessment_problem.session_id`가 NOT NULL이라 DB상으로는 세션마다 문제가 복제된다. 그 복제는 **백엔드 몫**이고 AI는 관여하지 않는다.

### ④ 세션은 무상태다 (2026-08-03 확정)

```
POST /api/v0/sessions/{id}/answers      ← 세션 API는 이것 하나뿐
삭제: POST /sessions · GET /sessions/{id} · POST /sessions/{id}/restore
```

문제·기록·커서가 매 요청에 실려 온다. **모든 요청이 곧 restore**다.

**왜 바꿨나**: 배포·EC2 Stop/Start·프로세스 재시작이면 인메모리 세션이 **반드시** 날아간다. 그래서 `restore`가 있었는데, 뒤집으면 백엔드가 시작·진행·404복원 **세 갈래를 다 구현**해야 했다. 예외가 아니라 상시 경로였다.

**얻은 것**: ⓐ 백엔드 구현 1갈래 ⓑ 재시작이 세션을 안 깬다(연동 테스트 중 우리가 재시작해도 무사) ⓒ 진행 규칙이 한 곳(AI)에만 있다 ⓓ 세션에 한해 인메모리 제약이 사라진다 ⓔ **재시험이 공짜로 된다** — `problems[]`에 대상 문제만 실으면 AI는 그것만 순회한다(문제 개수가 전부 `len(problems)` 파생).

**대가**: payload 증가(후반 턴 약 32KB). 서비스 간 내부 통신이라 무시할 수준.

---

## 계약 기준값

구현할 때 이 값을 그대로 쓴다.

### 공통

```
경로 prefix   /api/v0
필드 표기     camelCase (내부는 snake_case, 직렬화만 변환)
              단 problems[]·stages[] 내부는 DB 컬럼명을 그대로 쓴다
에러          {error, message, retryable}  평탄 구조. timestamp·path 안 씀
헤더 3종      X-Internal-Key(인증, health 면제) · Idempotency-Key · X-Trace-Id
비동기        /analyses · /curricula · /reports = 202 + 폴링
              /answers 만 동기 (성공 5.8~11.7초 · 재시도 포함 중앙 17초)
콜백          없다. AI→백엔드 방향 통신은 0이다
```

### ID 발급 주체

```
Spring 발급   analysisId · sessionId · submissionId · checkpointId · versionId
AI 발급       problemId · snapshotId     ← Spring이 그대로 PK로 채택한다
```

**`problemId`가 세 API를 관통한다** — 분석 응답 → 세션 요청의 `cursor.problemId` → 보고서 멱등키 `{problemId}:{scoreRunId}`. Spring이 INSERT 때 새 UUID를 발급하면 백엔드가 매핑을 들고 다녀야 한다. AI가 만든 UUID를 그대로 PK로 쓰면 매핑이 사라진다.

### 멱등키

```
POST /analyses    {submissionId}:{attemptNo}
POST /curricula   {submissionId}:{attemptNo}
POST /reports     {problemId}:{scoreRunId}
POST /answers     body 의 clientRequestId
```

같은 키로 재전송하면 **기존 `jobId`를 그대로 돌려주고 LLM을 다시 부르지 않는다.**

### 모델 코드 — 방향마다 컬럼이 다르다 🔴

정의서 `ai_model`에 컬럼이 둘이고 값이 다르다.

```
model_code            "화면 선택값·API 요청·사용량 집계에서 공통으로 쓰는 불변 모델 코드"
                      초기값 11종이 전부 소문자 하이픈
                      step-3.5-flash · mistral-medium-3.5 · qwen3-next-80b
                      nemotron-3-super-120b · qwen3.5-122b · nemotron-super-49b
                      deepseek-v4-pro · llama-4-maverick · mistral-large-3
                      glm-5.2 · minimax-m3
provider_model_code   "AI 공급자가 부여한 원본 모델 식별자".  UNIQUE (provider, provider_model_code)
```

우리가 provider에 실제로 넘기는 문자열은 `nvidia/nemotron-3-ultra-550b-a55b` 형태다 — **벤더 접두어가 붙어 `provider_model_code`에 해당한다.**

```
요청  →  AI       providerModelCode     ai_model.provider_model_code 값
응답  →  Spring   aiUsage.modelCode     ai_model.model_code 값 (model_id 조회용)
```

🔴 **백엔드 swagger의 `"CLAUDE_OPUS_5"`(대문자 스네이크)는 정의서 위반이다.** `model_code` 초기값 11종이 전부 소문자 하이픈이다. 어느 컬럼도 아닌 표기라 백엔드가 고쳐야 한다.

🔴 **우리 분석 기본 모델 `nemotron-3-ultra-550b-a55b`가 `ai_model` 초기 목록에 없다.** 등록 안 되면 `model_id` 조회 실패로 `ai_usage` INSERT가 깨진다.

**모델은 operator가 고른다.** 생략 시 서버 기본값(`config.py`의 용도별 기본).

### 캘리브레이션 — AI에 API를 만들지 않는다 (2026-08-03 확정)

백엔드에 `GradingPolicy`·`PUT /platform/operations/grading-model`(전 기관 재캘리브레이션)·`CalibrationProgress`가 있다. 확인 결과 **`GradingPolicy`는 채점 규칙이 아니라 채점 모델 정책이다** — 통과선·힌트 횟수·점수 상한 같은 숫자가 하나도 없다. **규칙 중복은 없고 경계가 깨끗하다.**

재캘리브레이션은 **보정이 아니라 격리로 푼다.**

```
1. 세션 시작 시 모델을 고정한다. 그 세션의 모든 채점이 같은 값을 쓴다
2. 모델 변경은 그 시점 이후 시작되는 분석·세션부터 적용한다
3. 이미 확정된 점수는 건드리지 않는다. 어느 모델로 낸 점수인지는 ai_usage 에 남는다
```

**근거**: ⓐ 점수는 절대 비교가 아니다 — 같은 팀·같은 문제·같은 힌트 안에서만 비교가 성립하고 총점도 가중치도 없다 ⓑ **소급 보정은 학생이 본 점수와 기록된 점수를 다르게 만든다** — 비보상 원칙 정면 위반.

이러면 백엔드의 `calibrationVersion`·`progress`가 **AI 호출 없이** 성립한다.

### 이름 (2026-07-30 개명 완료 — 옛 이름을 쓰지 않는다)

```
assessment_problem       구 decision_point       PK problem_id
problem_reference        구 dp_reference         PK reference_id
problem_stage            단계 테이블              PK problem_stage_id, UNIQUE(problem_id, axis_code)
stage_answer_attempt     답변 이력                PK answer_attempt_id, attempt_no 1~3
question_focus_item      구 focus_area_code
project_requirement / project_requirement_assessment   요구사항·판정 결과
curriculum_section / teaches                            교안
code_analysis.analysis_document (JSONB)                 분석 문서 — B-5로 타입 변경 요청 중

폐기: session_turn · dp_question · question_candidate · depth_level
     score_run / axis_score — AI가 값을 만들지 않는다
```

### 축 — L3/L4 순서에 주의

값은 **`"L1"` `"L2"` `"L3"` `"L4"`** (DB `problem_stage.axis_code` CHECK와 동일).

| 단계 | 무엇을 묻나 | 백엔드 `axis_code` 대응 |
|---|---|---|
| L1 | 무엇을 하는 코드인가 | `CODE_UNDERSTANDING` |
| L2 | 왜 그렇게 했는가 | `DESIGN_LOGIC` |
| L3 | **다른 방법과 비교 (대안)** | `ALTERNATIVE_COMPARISON` |
| L4 | **언제 깨지는가 (반례·한계)** | `COUNTEREXAMPLE_RESPONSE` |

⚠️ **L3=반례 / L4=대안으로 적힌 문서·댓글은 낡은 것이다.** PoC 축 순서는 정정 완료(`fc80044`·`15b02fb`).

### 문제 선정 — 없으면 없다 (2026-08-03 PM 확정)

```
teaches 는 오퍼레이터가 고정한다 — 모든 학생이 같은 개념을 시험 본다
개념이 코드에 없으면
  ① 최대한 찾는다     p04-3 + 실패한 teach 만 모아 재시도 1회
  ② 그래도 없으면      문항 없음. unmatchedTeaches 로 명시적으로 보고한다
지어내지 않는다        대체 개념·일반 문제·합성 코드 전부 폐기
```

**비교 가능성의 근거가 여기다.** MG-06(교육생 상세)이 회차별로 같은 개념 3개를 가로로 놓고
비교한다 — 학생마다 개념이 다르면 그 격자가 성립하지 않는다.

🔴 **`―`(문항 없음)과 `0단`(L1 미달)은 다른 것이다.**

```
―     문항 없음   출제 안 됨      → 도달 단계 NULL
0단   L1 미달     출제됐고 못 풀었다 → 도달 단계 0
```

⚠️ **`isGeneral`은 삭제됐다**(필드째). `_general_topics()` 폴백도 없앴다 —
`tests/test_topics.py`가 `assert not hasattr(topics, "_general_topics")`로 되살아나는 것을 막는다.

### 채점

```
단계당 0~5점, 통과선 3점
🔴 점수 상한 없다  {0회:5, 1회:4, 2회:3} 폐기 (2026-08-03, §T15-1 완료)
🔴 총점 없다       문제당 만점 20도 폐기
도달 단계   0~4. 앞에서부터 연속 통과한 개수 = reachedStage
힌트         단계당 2개. 소진 후에도 미달이면 그 문제는 거기서 끝
자력도       파생값이다. 응답에 싣지 않는다 — 어느 슬롯이 통과했는지로 계산된다
             (질문 통과=SELF / 힌트1 통과=SELF_MAINTAINED / 힌트2 통과=PARTIAL)
실패 시      그 문제 종료, 다음 문제의 L1로
가중치       쓰지 않는다 (PM 설계 v2 — "어떤 결정도 임의 숫자의 합산으로 나지 않는다")
재시험       문제 단위. L1·L2 둘 다 통과해야 재시험 아님
```

🔴 **점수 상한을 뺀 이유** (2026-08-03): 새 `problem_stage`가 **질문·힌트1·힌트2 각각의
점수를 따로 저장**한다. 어느 답변이 몇 점이었는지가 DB에 그대로 남으므로, AI가 미리 눌러서
보낼 이유가 없어졌다. **AI는 채점만 하고 가공하지 않는다** — 상한이 필요하면 백엔드·화면이
정한다. 비교 가능성은 "사다리 강도·횟수가 같다"로 이미 보장된다(상한은 그 근거가 아니었다).

**힌트 = 재진술이다** (PM 설계 v2 §4-2).

```
1차  "다른 표현으로"    긴 문장 → 짧은 문장 여러 개, 추상 표현 → 일상어
2차  "더 쉽게 풀어서"   여러 가지를 묻고 있으면 순서대로 답하게 나눠 묻는다
                        ⚠️ 분해이지 축소가 아니다 — 범위를 좁히면 측정 대상이 바뀐다
공통  정답 집합 유지 · 코드 위치 금지 · 선택지 금지 · 답 방향 암시 금지 · 식별자 그대로
```

⚠️ 옛 사다리(`관점 되짚기` / **`범위 좁힘`**)는 폐기했다. 2차의 범위 축소가 정면 위반이었다.

**축별 도달 기준** (`scoring.REACH_CRITERIA`. 3점의 행동 정의이고 `rubric_block`으로 프롬프트에 들어간다)

```
L1  요소들이 어떻게 이어지는지 말했는가
L2  의도와 제약 하나를 연결했는가
L3  대안 하나를 구체적으로 말했는가
L4  언제 문제가 되는지 조건을 특정했는가
```

**채점 프롬프트에 들어가는 것** (좁게 유지한다 — 성공 시 5.8~11.7초가 나오는 이유다)

```
rubric_block    축별 루브릭
question        동결된 질문 1개
hints_block     학생이 받은 힌트 0~2개
code_block      그 문제의 코드 파편 하나
answer          학생 답변
analysisContext overview + structure      ← §T12-6에서 추가
```

🔴 **분석 문서를 통째로(20KB) 넣지 않는다.** 부피의 대부분인 `decisionPoints`는 문제가 이미 정해진 뒤라 불필요하고, 36회 × 5,000~7,000토큰이면 비용·지연이 감당 안 된다. `overview`+`structure`만 뽑으면 1~2KB(500~800토큰)이고, 매 호출 같은 블록이라 프롬프트 캐싱 대상이다(`aiUsage.cachedTokenCount`).

### `reachedStage` — 정의 (2026-08-03 확정)

**앞에서부터 연속으로 통과한 축의 개수. 0~4.** "도달"이 아니라 **"통과"** 기준이다.

```
L1 실패                    0        L1·L2 통과 · L3 실패        2
L1 통과 · L2 실패           1        L1·L2·L3 통과 · L4 실패     3
                                    L1~L4 전부 통과            4
```

`passed`를 앞에서부터 세다가 처음 `false`에서 멈춘다. **AI가 보고서 요청에서 검증하고 어긋나면 422로 막는다** — 파생값이라 어긋나면 화면 판정과 근거가 다른 말을 한다.

**DB 매핑 — 컬럼 둘이 서로 다른 것을 담는다.**

```
highest_reached_level   마지막으로 "통과한" 축     L3 실패 → 'L2'    ← reachedStage
ended_level             문제가 "끝난" 축           L3 실패 → 'L3'

reachedStage 0 → NULL · 1 → 'L1' · 2 → 'L2' · 3 → 'L3' · 4 → 'L4'
```

🔴 **백엔드가 반대로 채우기 쉬운 자리다.** 컬럼 논리명이 *"최고 도달 단계"*라 "L3를 풀었으니 L3 도달"로 읽힌다.

```
termination_reason   COMPLETED_L4 · TERMINATED_AT_L3 · TERMINATED_AT_L2
                     · TERMINATED_AT_L1  ← 코드값 추가 요청 중(B-4)
```

### 시간 초과 — AI 호출이 없다

타이머는 프론트·백엔드 소유다. **AI는 시간을 모르고, 종료를 통보받을 엔드포인트도 없다.** 무상태라 필요가 없다.

```
1. 진행 중이던 단계를 사실대로 남긴다
     시도 0회      status NOT_REACHED · attemptCount 0 · 점수 null
     시도 1회 이상  status FAILED · attemptCount n · 점수 그대로
2. highest_reached_level = 마지막으로 통과한 축
3. 그때까지의 transcript로 POST /reports 를 부른다
4. AI 호출은 그것뿐
```

**"시간 때문인가 실력 때문인가"는 단계가 아니라 세션이 갖는다** — `assessment_session.status = EXPIRED`. `problem_stage.status` CHECK에 `EXPIRED`가 없기도 하다.

⚠️ **구멍 하나**: 힌트를 열고 답변을 쓰다 시간이 끝나면 그 힌트가 기록되지 않는다(`stage_answer_attempt`는 `answer_text` NOT NULL). 점수엔 영향이 없지만 **자력도가 실제보다 좋게 나온다.** 감수한다.

### 재시험 (2026-08-03 확정)

**같은 시험지를 재사용한다. 새 API 없다.** 백엔드가 `problems[]`에 재시험 대상만 실어 보내면 AI는 그것만 순회한다.

```
정규 시험   problems: [문제1, 문제2, 문제3]   →  progress 1/3 · 2/3 · 3/3
재시험      problems: [문제1, 문제3]         →  progress 1/2 · 2/2
```

**백엔드가 지킬 것**

```
1. 재시험은 새 세션이다. sessionId 를 새로 발급한다
2. problemNo 는 원래 번호를 유지한다 (1과 3). 1,2로 다시 매기면 보고서·화면이 못 가리킨다
3. 첫 커서의 hintsUsed 는 0. 점수 상한이 5부터 다시 시작한다
4. 보고서는 재시험 문제 수만큼
```

**대가**: 학생이 질문·힌트를 이미 봤다. 통과해도 이해인지 기억인지 구분이 안 된다. **감수하고 간다** — 재시험용 별도 시험지는 후속(분석 때 병렬로 한 벌 더 만든다. 힌트 24콜이 이미 8병렬이라 배치 시간이 크게 안 는다). **지금 컬럼을 미리 만들지는 않는다.**

### `problem_type` 5종

"왜 이 지점을 골랐나" — `questionFocusItem`의 "무엇을 묻나"와 다른 축이다.

```
DESIGN_CHOICE · RISK_POINT · COMPLEXITY_HOTSPOT · REQUIREMENT_IMPL · EXTERNAL_INTEGRATION
```

### `reference_type` 6종

```
CALLER · CALLEE · DEFINITION · TEST · CONFIG · SIMILAR
```

**`PRIMARY`는 안 쓴다** — 주 코드 지점이 `assessment_problem`으로 옮겨졌다.

🔴 **`references[]`는 현재 항상 빈 배열이다**(`engine.py`). 스키마·DB 테이블은 다 있는데 엔진이 안 채운다. §T13에서 채운다.

### DB CHECK 제약 (새 MEAS 기준, 2026-08-03)

이 값을 어기면 Spring INSERT가 깨진다. **AI가 실제로 채우는 것만 남긴다.**

⚠️ **`테이블정의서_v06`은 낡았다.** 새 MEAS(v03 동기화, 기준일 2026-08-03)에서 구조가 크게
바뀌었다 — 아래는 그 기준이다. 옛 v06 기준 표를 근거로 쓰지 않는다.

```
analysis_job.status          QUEUED, RUNNING, SUCCEEDED, PARTIAL, FAILED
assessment_problem.problem_no        BETWEEN 1 AND 3   ← questionBudget 상한이 3이다
assessment_problem.problem_scope     TEAM_SHARED_PROBLEM, INDIVIDUAL_OWN_COMMIT
                                     ← 2026-08-04 개명. 옛 이름 TEAM_COMMON
assessment_problem.generation_status NOT_GENERATED     ← unmatchedTeaches 가 이 값이 된다
assessment_session.status    READY, IN_PROGRESS, PAUSED, COMPLETED,
                             INTERRUPTED, INVALID, FAILED, SUPERSEDED
problem_stage.axis_code      L1, L2, L3, L4
problem_stage.status         PREPARED, IN_PROGRESS, PASSED, NOT_PASSED,
                             NOT_REACHED, NOT_ANSWERED
problem_stage.*_score        NUMERIC(18,6), NULL 또는 0~5
problem_stage.*_passed       score>=3 이면 TRUE, <3 이면 FALSE (CHECK가 강제한다)
assessment_problem_reference.reference_type
                             PRIMARY_BLOCK, QUESTION_HIGHLIGHT, CALLER,
                             RELATED_CONTEXT, CURRICULUM_EVIDENCE
submission.method            GITHUB_URL, ZIP_WITH_GITLOG
*_scope_code                 TOTAL, OWN_COMMIT
```

🔴 **사라진 것** — 옛 계약이 여기에 기대고 있었다.

```
stage_answer_attempt 테이블      통째로 사라짐 (답변 3슬롯이 problem_stage 한 행에 들어감)
problem_stage.attempt_count      사라짐  → 우리 응답에서도 뺀다 (§T15-3)
problem_stage.termination_reason 사라짐  → 응답에는 남기되 저장 요청 안 함 (§T15)
code_analysis.analysis_document_markdown  사라짐  → JSONB 신설 요청 (#42 B-13)
assessment_problem 의 코드 스니펫 자리     아예 없음 → code_text 신설 요청 (#42 B-12)
```

**AI만 아는 NOT NULL 값** — 응답에 없으면 Spring이 행을 만들 수 없다.

| 테이블 | 컬럼 |
|---|---|
| `code_snapshot` | `content_hash`, `file_count` |
| `assessment_problem` | `problem_no`, `title` |
| `assessment_problem_reference` | `reference_type`, `display_order`, `evidence_hash`, (코드 유형이면) `path`·`line_start`·`line_end` |
| `problem_stage` | `question_text`, `first_hint_text`, `second_hint_text` |

### 제출 방식

**둘 다 동작한다** (2026-08-03, `materialize.py`).

```
GITHUB_URL        source.repoUrl (+branch).  AI가 서버에서 git clone --depth 1
                  **public 레포만.** 인증이 없어서 비공개면 즉시 실패한다
                  commitSha 를 여기서만 실제 값으로 채운다 (ZIP은 요청 값 그대로)
ZIP_WITH_GITLOG   multipart/form-data 로 payload(JSON 문자열) + file
extractionScope   TOTAL 고정.  OWN_COMMIT 은 미구현 — 요청 시 TOTAL 로 물러나고
                  scopeFallback=true 로 알린다
```

🔴 **`--depth 1` 얕은 클론이라 `.git`은 남지만 커밋이 tip 하나뿐이다.** `OWN_COMMIT`(작성자별
필터)은 이걸로 못 한다. **`.git`을 지우지는 않는다** — 지우면 그 시점에 복구가 불가능해진다.
필요해지면 depth를 푸는 것이 그때의 변경 지점이다.

**GitHub API로 파일을 긁지 않는다.** 팀 PoC가 그 방식이었는데 실사고가 났다 — 비인증 한도가
IP당 60회/시간이라 **같은 망의 다른 교육생까지 막혔고**(2026-07-16, 8분간 87회 연속 403),
큰 repo는 tree API가 목록을 잘라 소스가 조용히 빠진다. 서버 클론은 두 한도에 안 걸린다.

### `ai_usage` 매핑

| AI가 준다 | Spring이 채운다 |
|---|---|
| `idempotencyKey` · `contextType` · `contextId` | `usage_id` · `org_id` · `actor_user_id` |
| `featureCode` · `modelCode` | `model_id` (조회) |
| `inputTokenCount` · `outputTokenCount` · `cachedTokenCount` | `request_id` · `trace_id` |
| `status` · `failureCode` | `input_unit_price` · `output_unit_price` · `currency_code` |
| `latencyMs` · `occurredAt` | `estimated_cost` · `actual_cost` · `created_at` |

✅ **단가는 Spring이 갖고 있다 (C-3 닫힘).** 백엔드에 `PUT /platform/operations/models/{modelId}/pricing`과 `GET /organizations/{orgId}/operations/usage`가 이미 있다. 우리 설계가 맞았다 — AI는 단가를 모르고, 단가표를 AI에 두면 바뀔 때마다 재배포다.

```
contextType    ANALYSIS · GRADING · REPORT · CURRICULUM        ← 필드명 변경 완료(2026-08-03)
               ⚠️ 값 집합은 미확정 — 새 MEAS 비고는 ANALYSIS_JOB·PROBLEM_STAGE 처럼 테이블명을 쓴다
failureCode    TIMEOUT · RATE_LIMITED · PROVIDER_ERROR · INVALID_JSON · CONTEXT_OVERFLOW
idempotencyKey {요청키|contextId}:{contextType}:{순번}
```

🔴 **`idempotencyKey` 형식이 2026-08-03에 바뀌었다.** 예전엔 한 작업의 모든 행에 요청 헤더 키를 그대로 박아 **행마다 키가 같았다.** 그대로 두면 Spring이 여러 콜을 한 행으로 합쳐 토큰이 사라진다.

**`featureCode`**

| AI 단계 | 값 |
|---|---|
| 코드 분석 문서 (p04-1) · 요구사항 P/F (p04-2) | `CODE_ANALYSIS` |
| 문제 선정 (p04-3) · 질문·힌트 동결 (p04-4·p04-7) | `QUESTION_GENERATION` |
| 답변 채점 (p04-5) | `GRADING` |
| 보고서 (p04-6) | `SUMMARY_DRAFT` |
| 교안 분석 (p01) | `CURRICULUM_ANALYSIS` |

`SESSION_DIALOG`는 DB CHECK에 남아 있지만 **쓰지 않는다.**

### 속도 제한 — 지금 무료 티어 사정. 설계 근거로 쓰지 않는다

```
NVIDIA 무료 티어   (키, 모델) 쌍당 분당 40회      ← 키당이 아니다
키 8개 풀링        모델당 320 RPM
유료 전환          사라지거나 크게 오름
```

**"RPM 때문에 X를 못 한다"는 문장을 쓰지 않는다** — 임시 조건으로 아키텍처를 못 박으면 유료 전환 때 근거가 통째로 무효가 된다.

**진짜 병목은 컨텍스트 길이다.** 코드를 12,000자로 잘라 프롬프트에 넣으므로 큰 레포는 잘린 코드로 요구사항이 판정된다.

---

## 할 일

### T13 — 백엔드 연동 ← **다음**

**백엔드가 AI 연동 도메인을 아직 안 만들었다.** 교안·제출·분석·세션·보고서 전부 미착수라
연동 테스트는 그쪽 준비 뒤에 시작한다. 우리 쪽 준비는 끝났다.

```
주소        https://cpiysizen3.ap-northeast-1.awsapprunner.com  (고정)
계약        openapi.json  ·  test_folder/ 에 실제 주고받을 데이터가 형식 그대로 있다
```

⚠️ **채점 타임아웃을 30초 이상으로 잡아 달라고 해야 한다.** 무료 티어에서 턴 왕복 중앙값이
21.3초, 최대 141.9초다. `retryable: true`면 같은 `clientRequestId`로 재전송해야 세션이 안 끊긴다.

### 후속 (순서 미정)

```
· 유료 전환            529의 근본 해결. 팀 논의 (../output_docs/미결_논의사항.md P-3)
· §7-8 선별 로직 교체   아래
· RELATED_CONTEXT 참조  심볼 테이블이 없어 아직 못 만든다
· 교안 대형 PDF 상한    34쪽 310초. 200쪽을 외삽할 수 없다
· 폴링 응답에 진행률     operator가 언제 끝나는지 모른다
· 재시험용 별도 시험지   분석 때 병렬로 한 벌 더
```

#### 🔴 미결 — §7-8 선별 로직 교체

PM 설계서(`Frontend/docs/plan/v2/14-verification-design.md`)가 **룰 스캐너 자산 대부분을
폐기**하라고 한다 — `fan_in` 중요도 · 중복 정의 · `idiom_filter`. 대체재는 **교안 사전 기반
선별**이고, 그러면 **교안 분석이 문제 선정의 상류**가 된다.

근거는 실측이다. 도서관 6파일·petclinic 49파일 둘 다 **룰 후보가 1개**였다. 물어볼 개념은
이미 정해져 있고 남는 질문은 "그 개념이 코드 어디 있나" 하나다.

**재료는 확보했다**(`teaches[].kind`·`evidence`·`siblingNames`, 2026-08-04). **로직 교체는
아직 안 한다** — 선별 방식은 문제의 *질*이지 파이프라인 *동작*이 아니다.

지금은 최소 버전만 들어가 있다: **점 있는 API 식별자가 코드에 없으면 사전 제외**
(`topics._missing_api_token`, LLM 0회). `Tool` 같은 단어형 SDK 개념은 아직 안 막는다 —
교안 어휘로 넓히는 자리가 이 항목이다.

#### 확인 필요 (PM)

**L3·L4가 "선택 구간"인데 재시험 기준은 3단 이상이다.** §4-1은 L2까지 필수라 하고 §9-1은
2단 이하면 재시험이라 한다 — 그러면 L3 도달이 사실상 필수가 된다. 우리는 **L1·L2 기준으로
진행 중**이다. 설계서 내부 모순으로 보인다.

---

## 완료 이력

| # | 내용 | 날짜 |
|---|---|---|
| T1~T11 | 스캐폴드 → 엔진 이식 → 세션 무상태 전환(11 → 8 엔드포인트) | ~08-03 |
| T12 | 계약 변경 6건 (`providerModelCode` · `extractorVersion` int · `teachId` · `terminationReason` · `analysisContext`) | 08-03 |
| T12b | 백엔드 이슈 `#42` 게시 | 08-03 |
| T14b | 실호출 안정화. **모델 12종 실측** — 채점은 `deepseek-v4-flash` 하나뿐 | 08-03 |
| T15 | 새 MEAS 정의서 대응. `problem_stage` 1:1, 점수 상한 폐기 | 08-03 |
| T17 | 백엔드 1차 회신 반영 (`contextType` 개명, 통과 기준 3점 공통) | 08-03 |
| T18 | 교안 사전 재료 확보 (`kind`·`evidence`·`siblingNames`) | 08-04 |
| T19 | `references[]` 채우기 + 교안 섹션 병합 수정 | 08-04 |
| T16 | **백엔드 회신 전부 도착 — 우리 쪽 코드 변경 0** | 08-04 |
| T20 | **전 구간 실호출 완주 + 결함 9건 수정** (아래) | 08-04 |

### T20 — 실호출로 드러난 결함 9건 (2026-08-04, 254 tests)

`test_folder/`에서 교안 → 분석 → 문답 → 보고서를 실제 데이터로 통과시키며 잡았다.
**단위 테스트로는 하나도 안 잡혔다** — 전부 모델 출력의 실제 모양에서 나왔다.

| # | 결함 | 고친 곳 |
|---|---|---|
| 1 | nemotron이 추론 토큰 배수표에 없어 p04-3이 JSON을 시작도 못 함 | `client.REASONING_TOKEN_MULTIPLIER` |
| 2 | 배치 재시도 2회 — 529 한 번에 소진 | `stages.BATCH_MAX_ATTEMPTS = 4` |
| 3 | symbol 인용 꼬리 오타로 통째로 버림 | `fragments._prefix_match` |
| 4 | "정확히 N개" vs "없으면 빼라" 프롬프트 모순 → 억지 앵커 | `topics._NO_PADDING` |
| 5 | **`teach_id` echo — 4곳에서 재발** | `stages.resolve_choice` (공용) |
| 6 | 실패 시 `aiUsage` 소실 → 백엔드 비용 집계 누락 | `engine.AnalysisFailed` |
| 7 | 질문·힌트의 깨진 백틱 인용이 학생 화면에 노출 | `fragments.repair_code_quotes` |
| 8 | 있는 파일을 "미제공"이라 적어 허위 위험 생성 | `fragments.build_code_block` 고지문 |
| 9 | 마크다운 교안 위치 ≠ `curriculumRefs` (검증 안 된 페이지 지목) | `report._render_markdown` |

**5번이 가장 비쌌다.** 모델이 준 id를 그대로 안 돌려주는 것은 예외가 아니라 상시 동작이다.
p04-1·p04-3·p04-6 세 스테이지에서 같은 실패가 났고, 공용 함수로 빼고서야 멈췄다.

**교육생 화면에 나가는 문장은 따로 관리한다.** 백엔드가 `unmatchedTeaches[].reason`을
`assessment_problem`에 저장해 화면에 띄우기로 하면서(2026-08-04), 내부 진단(`Selection.dropped`)이
그 필드로 새는 것을 막았다 — 모델이 잘못 인용한 코드 원문이 학생에게 보이던 자리다.

---

## vendor 정책 (2026-08-02 변경)

**"무수정"을 폐기했다.** 우리가 PM 요청·백엔드 요청·실측 성능 사이를 조정하는 자리라, 프롬프트·규칙 수준 수정이 필요할 때 팀원 회신을 기다리면 아무것도 못 한다.

**대가**: 갱신이 덮어쓰기 복사라 **복사하면 우리 수정이 사라진다.** 그래서 규칙 셋 —

```
1. 수정하면 vendor/PATCHES.md 에 항목 추가
     (무엇을 · 왜 · before→after · 동작 변화 · 재적용 방법 · 닫는 조건)
2. 갱신 후 모든 항목 재적용
3. pytest tests/test_vendor_patches.py     ← 패치 소실을 잡는 유일한 장치
```

**우리 소유 코드(`rules.py`·`scoring.py` 등)로 우회할 수 있으면 그쪽이 먼저다** — 갱신 때 안 사라지고 재적용도 필요 없다. 기준 커밋·갱신 절차는 `vendor/SOURCE.md`.

**적용 중인 패치**

| # | 대상 | 내용 | 상류 |
|---|---|---|---|
| P-1 | `prompt_manifest.json` p04-5 | 채점 응답에 `reached` 추가 | 팀원 요청 예정(M-1) |

**P-1을 우리 쪽에서 못 한 이유**: 도달 기준 *문장*은 `rubric_block()`으로 넣을 수 있지만(우리가 만드는 문자열), **모델이 응답 필드를 하나 더 담게 하려면 `user_template` 안의 JSON 스키마를 고쳐야 한다.**

**동작 변화**: 모델이 `score`·`reached`를 따로 내고 우리가 교차 검증한다. 어긋나면 **점수를 따르고** `Grade.reach_conflict=True`로 남긴다. `reached`가 없어도 채점은 계속 돈다 — 교차 검증만 꺼진다.

---

## 설계 결정 (뒤집지 말 것)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 층은 `api/` `schemas/` `engines/` 셋만 | 층마다 존재 이유를 한 문장으로 못 대면 만들지 않는다 |
| D2 | 엔진은 FastAPI를 모른다 (`dict` in/out) | 팀원이 FastAPI 몰라도 기여 가능. CLI 단독 실행으로 디버깅 가능 |
| D3 | 스텁이 1급 시민 (`engine_mode`) | 엔진 없이도 계약이 살아 있어야 백엔드가 대기하지 않는다. `real` 모드에 엔진이 없으면 **시끄럽게 실패**한다 — 조용한 폴백은 가짜 데이터를 운영까지 흘려보낸다 |
| D4 | JS 엔진은 Python으로 포팅. Node 안 띄운다 | JS는 브라우저 제약의 산물이지 설계 선택이 아니다 |
| D5 | 이 브랜치에서 PoC를 만들지 않는다 | 역할 분담. 검증은 Swagger·Postman으로만 |
| D6 | 기존 구현은 `_legacy/`로 물리되 삭제하지 않는다 | 모듈화 참고용. `.gitignore` 대상 |
| D7 | 워커 1개 전제 | 인메모리 job 저장소. 워커를 늘리면 레이트리밋 카운터가 갈라진다 |
| D8 | 턴 점수를 wire에 노출한다 | Spring이 매 턴 저장한다. 점수·시도 횟수가 없으면 복구된 세션이 "힌트를 몇 번 썼는지" 모른 채 재개된다 |
| D9 | `bestScore`·`confirmedScore`를 둘 다 보낸다 | 힌트가 점수 상한을 깎는다. 캡 적용 결과만 남기면 상한 정책 변경 시 재계산 불가, 감사도 불가 |
| D10 | 재시험·문제배정 판정은 Spring이 한다 | AI는 점수와 문제별 `retest`만 낸다. 커트라인·배정은 조직 정책이라 DB 주인이 갖는다 |
| D11 | 질문·힌트는 분석 배치에서 전부 동결 | 위 §기능 동결 스펙 ① |
| D12 | 세션 총점·축 평균을 AI가 만들지 않는다 | 점수는 매 턴 `problem_stage`에 저장돼 있다. 집계는 Spring이 SQL로 하면 되고, LLM이 아니면 못 만드는 것만 `/reports`에 담는다 |
| D13 | `codeSnippet`을 AI가 보낸다 | `evidence_hash`가 그 문자열의 해시다. Spring이 따로 잘라내면 줄바꿈·BOM 차이로 해시가 안 맞는다 |
| D14 | 값 이름은 DB 컬럼명을 그대로 쓴다 | 새 어휘를 만들면 백엔드가 "정의서에 없는 컬럼"으로 읽는다. **단 컬럼명이 사실과 다르면 컬럼명을 고친다** — `analysis_document_markdown`(B-5)이 그 사례 |
| D15 | 분석 문서의 원본은 JSON이고 Markdown은 렌더 결과다 | 다운스트림(문제 선정·보고서)이 JSON을 그대로 프롬프트에 넣는다. Markdown으로 저장하면 되파싱해야 한다 |
| D16 | LLM에게 줄 번호를 세게 하지 않는다 | LLM은 `symbol`(소스에서 복사한 코드 한 줄)만 주고 줄 번호는 우리가 그 문자열을 찾아 산정한다. 못 찾으면 `evidenceValid=false`로 남기고 근거로 쓰지 않는다 |
| D17 | 세션은 무상태다 | 위 §기능 동결 스펙 ④ |
| D18 | AI→백엔드 방향 통신은 0이다 | 폴링은 연결 방향이 한쪽뿐이라 `X-Internal-Key` 하나로 끝난다. 콜백은 역방향 인증을 새로 정해야 하고, 10분짜리 작업이라 유실 시 재전송 정책도 필요하다. 폴링은 유실 개념이 없다 |

---

## 함정

이전에 실제로 물렸거나 물릴 뻔한 것들.

| 함정 | 대응 |
|---|---|
| **가짜 LLM 테스트는 전부 초록인데 실호출이 깨진다** | 2026-08-02에 이렇게 잡힌 버그가 5개. 계약 변경 후에는 실호출 1회를 반드시 돌린다 |
| `.env`가 `os.environ`을 안 채운다 | vendor 키 풀은 `os.environ`만 본다. **AWS는 진짜 환경변수라 안 터지고 로컬만 죽는다** — `config.load_api_keys_into_env()` |
| 교안 청크를 쪽 수로만 자른다 | PoC 기본값 10쪽은 슬라이드 기준. 실제 PDF는 쪽당 1,600자 → 응답 JSON이 잘린다. `CHARS_PER_CHUNK` 필수 |
| 원시 `list[dict]` 필드는 camelCase가 변환 없이 통과한다 | 엔진은 `axis_code`를 찾는데 `axisCode`가 온다 → 도달 0단·재시험 True. `_to_snake()` 필요 |
| multipart 엔드포인트는 요청 스키마가 `openapi.json`에 안 나온다 | 백엔드는 이 파일로 구현하는데 필드를 하나도 못 본다. `contentSchema`로 실었다(`api/multipart_docs.py`) |
| `pytest`가 `_legacy/tests/`를 수집해 깨진다 | `pytest.ini`에 `norecursedirs = _legacy .venv` 필수 |
| DB CHECK에 없는 상태값을 보낸다 | 위 §DB CHECK 목록만 쓴다. `OPEN`·`CANDIDATE`·`TIMEOUT`은 전부 폐기값 |
| L3/L4를 뒤집어 쓴다 | **L3=대안, L4=반례.** 옛 문서·이슈 옛 댓글이 반대로 적고 있다 |
| `cachedTokenCount > inputTokenCount` | DB CHECK가 막는다. `model_validator`로 먼저 잡는다 |
| `requirementResults` 길이가 요청과 다르다 | 조용히 채우지 말고 에러. 빠뜨린 항목은 `verdict="F"` + `note` |
| 워크트리 경로에 백슬래시 | Bash에서 `..\ai_poc\x`는 이스케이프로 먹혀 엉뚱한 폴더가 생긴다. `/`를 쓴다 |
| 분석 CPU가 이벤트 루프를 막는다 | `def` 또는 `run_in_executor` |

---

## 브랜치·커밋

```
feature/*  개발은 여기서만 한다
   ↓  동작·테스트 전부 통과하면
develop    통합 브랜치. GitHub 기본 브랜치
   ↓  검증 끝난 것만
main       배포 브랜치. EC2가 이 브랜치를 체크아웃해 둔다
```

**배포는 자동이 아니라 수동이다** — EC2에서 `git pull && systemctl restart iz-get-ai`. `main` 머지 자체는 안전하지만 **재시작은 진행 중 job을 끊으므로 시점을 백엔드와 맞춘다.** (세션은 무상태라 안 깨진다.)

⚠️ **정정 (2026-08-03): App Runner를 쓰고 있다.** 신규 고객 차단은 맞지만 **팀원 계정이 기존
고객이라 열려 있었고**, 팀원이 배포해 줬다. 아래 T9 기록(못 쓴다는 결론)은 우리 계정 기준의
경위로만 남긴다 — **결론은 뒤집혔다.** 현황은 §T14를 본다.

**커밋**: `type: short description (#issue)` — `feat` `fix` `refactor` `style` `docs` `chore` `remove`. 동사원형 소문자, 마침표 없음, 50자 이내, 이슈 있으면 번호 필수. **T 하나당 1커밋**을 권장한다.

**주의**: 이 저장소는 사용자가 직접 git 명령을 실행한다. 에이전트는 명령을 만들어 전달만 한다.

---

## 완료 이력

| 날짜 | 내용 |
|---|---|
| 2026-08-03 | **§T11 안정화** — 세션 무상태 전환(엔드포인트 3개 삭제) · `aiUsage` 전 경로 배선(분석 외 전부 빈 배열이던 계약 위반) · `sourceType` 4종 · `/reports` 헤더·멱등 · `callbackUrl` 삭제 · `idempotencyKey` 형식 정정. **198 tests** |
| 2026-08-03 | **§T14b 실호출 안정화**(죽은 채점 모델 교체 · GITHUB_URL 이식 · codeSnippet 파일 전체 · 리포트 problemNo·narrativeFailed) · **§T15 새 MEAS 대응**(점수 상한 폐기 · StageScore 1:1 · 어휘 정렬 · 잡 PARTIAL) · **§T17 백엔드 1차 회신 반영**(contextType 개명 · isGeneral 삭제 · 재시도 1회 · unmatchedTeaches · courseLabel 필수 · 교안 한글 고정). **232 tests** |
| 2026-08-02 | **T9 배포** — 서울 EC2 + cloudflared. **T10 전면 동결 전환** · **T10-B PM 설계 v2 대조 10건 판정** · **T10-C vendor 정책 변경 + P-1** · p04-1 `analysis_doc.py` · p04-2 `requirements.py` · 보고서 문제 단위 · 총점 폐기 + `reachedStage` · 힌트 사다리 재진술로 교체 · `Problem.is_general`. **192 tests** |
| 2026-08-01 | **T7d 정체의 정체** — 스트리밍 TTFT 측정. 정체 draw는 30초를 기다려도 **첫 토큰이 0개**다(느린 게 아니라 영영 안 온다). 성공 draw TTFT 중앙값 0.62초. 세션 타임아웃 8초 × 10회. **112 tests** |
| 2026-07-31 | **T6 룰 규칙부 이식**(vendor 방식, ZIP 안전 해제) · **T7a LLM 클라이언트**(실패해도 `usage`를 들고 던진다) · **T7b p04 전 구간 실동작** · **T7c 체감 지연 실측**(결론: 지연이 아니라 **실패율 32%**가 문제) · **T2c 분석 문서 JSON 전환**(B-5 발생) |
| 2026-07-30 | T2·T2b 스키마 정렬(`AxisCode` L1~L4, **L3=대안/L4=반례 교정**) · T1b 이름 통일 · T5 `openapi.json` |
| 2026-07-22 | 백엔드 계약 C1~C6 합의 |

빈 FastAPI 골격부터 다시 쌓아 **엔드포인트 + 인증 + 에러 형식 + camelCase 직렬화 + Swagger + `openapi.json`**을 만들었다. 기존 구현(`app/` 1,659줄 + 목업 2,550줄 + vendored pipeline 4,815줄)은 브라우저 PoC와 얽혀 있어 `_legacy/`로 물러났다(`.gitignore` 대상).
