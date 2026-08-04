# AI 파트 작업 계획

> 갱신: **2026-08-03** · 작업 브랜치 `feature/stabilize` (`develop`에서 분기)
> **이 문서는 실행용이다.** 무엇을 어떤 순서로, 어떤 방법으로 할지만 적는다.
> 구조·계약의 설명은 `README.md`(팀원용). 기계용 계약은 `openapi.json`.
> **백엔드 전달본은 `../qna/2026-08-03/issue-body-v2.md` = GitHub 이슈 `Team-IZ/Backend#42`.**
> ⚠️ 옛 `issue-body.md`와 이슈 `#31`은 폐기다 — `테이블정의서_v06` 기준이라 낡았다.

---

## 현재 위치

| | |
|---|---|
| 갱신 | **2026-08-03** · 브랜치 `feature/stabilize` |
| 엔드포인트 | **8개**. 세션이 무상태로 바뀌며 11 → 8 |
| 기능 | **6/6 완성 + 전부 실호출 검증.** 교안 · 코드 분석 · 문제 생성 · 힌트 · 채점 · 보고서 |
| 제출 | **ZIP · GitHub 링크 둘 다 동작.** 링크는 서버에서 `git clone --depth 1` |
| 배포 | **App Runner 자동 배포**(`main` 푸시 = 배포). 주소 고정. 팀원 소유 — 우리 작업 아님 |
| 계약 | `openapi.json`. `tests/test_openapi.py`가 드리프트를 막는다 |
| 다음 | **§T13 백엔드 연동**. §T16 회신은 2026-08-04 전부 도착 — 우리 쪽 변경 0 |
| 기준 | **§기능 동결 스펙** · **§계약 기준값**. 앞선 절과 충돌하면 이 둘이 이긴다 |
| 막힌 것 | **DDL 2건**(`code_text`·`analysis_document`). 이슈 `#42`로 요청함 |
| 🔴 위험 | **무료 티어 529 실패율 64%.** 유료 전환이 근본 해결(`../output_docs/미결_논의사항.md` P-3) |

### 실측값 (2026-08-03, 무료 티어 · 전 구간 실호출)

```
/analyses    ZIP 6파일     271초  문제 3 · 질문 12 · 힌트 24
             GitHub 링크   270초  clone 1.0초 + 스캔 0.6초 포함 (petclinic 49파일)
채점         성공 시 5.8~11.7초 · 실제 중앙값 17.2초(529 재시도 포함) · 최대 50.1초
/reports     20~45초 (문제 1건당)
/curricula   6쪽 · 3청크 병렬 · 464초 · 모듈 10개 (2026-08-02 측정, 갱신 안 함)
```

🔴 **LLM 호출 39회 중 25회(64%)가 `HTTP 529 Overloaded`였다.** 0.3초에 즉답하는 실패라
재시도로 넘기지만, 채점 목표(15초)를 못 지키는 원인이 이것이다. **모델 교체로 안 풀린다**
— 12종 실측(§T14b-1).

**소요 특성이 서로 다르다 — 한 덩어리로 말하면 안 된다.**

```
코드분석   LLM 콜 수가 30으로 고정. 코드 크기에 거의 안 비례한다
           (프롬프트를 12,000자로 자르므로) → 626초는 대체로 상수
교안분석   청크 수 = PDF 글자 수에 비례 (4,000자 또는 10쪽당 1콜, 8병렬)
           → ceil(청크/8) 라운드. **실측이 6쪽 1건뿐이라 100쪽을 외삽할 수 없다**
```

⚠️ **교안 소요 상한은 아직 못 준다.** 폴링이라 타임아웃 문제는 아니지만 ① operator가 언제 끝나는지 모르고 ② 청크가 많아지면 529 실패율 64%와 겹쳐 `PARTIAL`이 정상 상태가 된다.

⚠️ **p04-1 지연 편차가 크다: 36초 → 115초 → 195초.** 같은 프롬프트인데 5배 넘게 흔들린다. **배치 소요시간을 예측값으로 쓰지 않는다.**

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

### T12 — 계약 변경 6건 (2026-08-03, 브랜치 `feature/stabilize`)

전부 사용자와의 설계 대조에서 확정됐다. **이슈 전달을 막는 것들이다** — `openapi.json`을 재생성해야 이슈에서 가리킬 수 있다.

| # | 내용 | 근거 |
|---|---|---|
| 1 | `AnswerSubmit`에 모델 필드 추가 | 다른 3개 요청엔 있는데 **채점만 없었다.** operator가 채점 모델을 고르는 구조(백엔드 `GradingPolicy`)인데 실을 자리가 없다 |
| 2 | 요청 `modelCode` → `providerModelCode` | 방향마다 다른 컬럼이다(위 §모델 코드). 이름을 갈라야 백엔드가 어느 값을 실을지 안다 |
| 3 | `extractorVersion` `str` → `int` | 정의서가 INTEGER + CHECK > 0. **문자열이면 Spring INSERT가 깨진다** |
| 4 | `Problem.teachId` 추가 | 엔진은 이미 teach 기반으로 고르는데(`topics.select`) 조립할 때만 버린다. **없으면 "클래스는 L3까지 도달" 화면을 못 만든다** |
| 5 | `AnswerResult`에 `terminationReason`·`endedLevel` | 종료 판정은 AI가 하는데 응답에 없어 백엔드가 역추론해야 했다. DB 컬럼은 이미 있다 |
| 6 | 채점에 `analysisContext`(`overview`+`structure`) 주입 | 코드 파편 하나로는 MVC 같은 **파일 간 흐름**을 못 본다. 문서 전체는 과하고 이 둘만 1~2KB |

✅ **완료 (2026-08-03, 211 tests).** `openapi.json` 재생성(경로 8·스키마 36) · `README.md` 갱신.

**vendor는 안 건드렸다.** 6번을 매니페스트 슬롯 추가 대신 우리 소유 `stages.call(extra_user=...)`로 프롬프트 끝에 블록을 덧붙이는 방식으로 했다 — vendor를 고치면 팀원 갱신(덮어쓰기 복사)마다 재적용 부담이 생기는데 우리 코드로 같은 결과가 나온다. 대가는 블록이 `"규칙:"` 뒤에 붙는 것이라, 블록 안에 *"맥락은 사실 확인용이고 채점 기준은 값 단계 서술뿐"*을 명시해 루브릭을 다시 앵커했다. 맥락은 2,000자 상한.

**T12에서 갈린 판단 3개**

| 판단 | 내용 |
|---|---|
| `extractorVersion` 정수화 | `rules.extractor_version()`이 내던 값이 `"rules-<hex12>"`라 **어떤 파싱도 항상 실패한다**(= `/analyses` 영구 FAILED). 함수 자체를 `int` 반환으로 바꿨다. hex12를 그대로 int로 하면 2^48이라 **PostgreSQL INTEGER(2^31-1)를 넘어** `% 2_147_483_647 + 1`로 접었다. **대가: 값이 사람에게 안 읽히고 순서가 없다**(버전이 오르지 않는다). "같은 룰이면 같은 값"만 필요한 자리라 충분하다 |
| 🆕 `TERMINATED_AT_L4` | L3까지 통과하고 L4에서 힌트를 소진하면 실제로 발생하는데 **정의서 코드값 3종 어디에도 없다.** `COMPLETED_L4`(전부 통과)와 다른 결과다. B-4에 함께 요청한다 |
| `ReportVersions.modelCode` | `AiUsage.modelCode`와 같은 혼동을 낳는 응답 필드. 이름은 두고 "provider 문자열을 에코한다"를 주석으로 명시 |

⚠️ **`README.md`의 `/reports` 절이 낡아 있었다** — `problems[]`·`totalScore`·`retestTargets`는 T10에서 폐기된 것들이다. 현재 스키마(`problem`·`reachedStage`·`retest`)로 함께 고쳤다.

### T12b — 백엔드 이슈 전달 ✅ 완료 (2026-08-03)

**`Team-IZ/Backend#42`로 게시했다.** 본문은 `../qna/2026-08-03/issue-body-v2.md`.
새 이슈로 열었다 — 새 MEAS 정의서 기준으로 전면 재작성이라 `#31` 본문 교체로는 이력이 섞인다.

⚠️ **`#31`과 `issue-body.md`는 폐기다.** `테이블정의서_v06` 기준이고, DDL 요청 4건이 이미
해결됐다(백엔드가 `problem_stage`를 새로 짜면서). 옛 문서를 근거로 쓰지 않는다.

`extractorVersion` 순서 문제는 **미결로 남긴다** — 지금 값은 vendor 해시를
`% 2_147_483_647 + 1`로 접은 것이라 동일성만 보장되고 순서가 없다. "같은 룰이면 같은 값"만
필요한 자리라 충분하고, 순서가 필요해지면 손으로 올리는 정수 카운터로 바꾼다.

### T14b — 실호출 안정화 ✅ 완료 (2026-08-03, 226 tests)

전 구간 실호출에서 나온 버그와 계약 구멍을 고쳤다. **§T15와 달리 이것들은 이미 반영돼 있다.**

| # | 고친 것 | 왜 |
|---|---|---|
| 1 | **세션 모델 교체** `mistral-medium-3.5` → `deepseek-v4-flash` | 옛 모델이 **"1+1은?" 최소 프롬프트도 3/3 타임아웃**한다. 죽은 모델이었다 |
| 2 | `SESSION_TIMEOUT_S` 8 → 20초, 재시도 10 → 6 | 8초는 죽은 모델의 TTFT 분포에서 나온 값이라 근거가 사라졌다. 지금 모델 정상 지연이 6.9~11.7초라 8초면 성공할 호출을 죽인다 |
| 3 | **529를 `RATE_LIMITED`로 분류 + 지수 백오프** | 529는 0.3초 즉답이라 백오프 없이 재시도하면 6회가 2초에 소진된다. `PROVIDER_ERROR`에 섞이면 진짜 장애와 통계가 안 갈린다 |
| 4 | **`GITHUB_URL` 지원** (`materialize.py` 이식) | 스키마는 받는데 엔진이 `NotImplementedError`였다. 팀원 브랜치 `feature/code-importance-map`(`f2db763`)에서 이식 |
| 5 | **`codeSnippet` = 파일 전체** | 파편이 1줄(29~51자)로 나오는 경우가 있어 학생이 판단할 재료가 없었다. `evidenceHash`는 파편 기준 유지 |
| 6 | ZIP 최상위 소스 폴더 오벗김 | `src/` 하나만 있는 ZIP에서 `src/`를 GitHub 래퍼로 착각해 벗겼다. 백엔드가 그 경로로 파일을 못 찾는데 에러도 안 난다 |
| 7 | 리포트 `problemNo` 반향 · `narrativeFailed` · `narrative` 구조화 · 재시도 2 → 6 | 보고서 3건이 전부 `problemNo=1`이었고, 서술 실패를 백엔드가 알 방법이 없었다 |

**갈린 판단 2개**

| 판단 | 내용 |
|---|---|
| 채점 입력을 파편으로 되자른다 | `codeSnippet`이 파일 전체가 되면서, 그대로 채점에 넣으면 매니페스트 `code_block` 상한 4,000자에 **앞에서부터 잘려** 문제 구간이 파일 뒤쪽일 때 근거가 사라진 채 채점된다. `sessions._grading_code()`가 `lineStart`/`lineEnd`로 ±8줄을 되잘라 쓴다 |
| `_repo_root` 판정 기준 | "안에 마커 파일이 있나"로 하면 README 없는 레포에서 오작동한다. **폴더 이름이 소스 폴더 이름(`src`·`app`·`main`…)이면 안 벗긴다**로 갔다 — 모르는 이름은 예전처럼 벗기므로 실패해도 옛 동작이다 |

### T15 — 새 MEAS 정의서 대응 ✅ 완료 (2026-08-03, 227 tests + 실호출 검증)

백엔드가 `problem_stage`를 새로 짜면서 **한 행에 질문 1 + 힌트 2 + 답변 3 + 점수 3 + 통과 3**을
담는 구조가 됐다. `stage_answer_attempt` 테이블이 사라졌고 `attempt_count`·`termination_reason`
컬럼도 없다. 우리 응답을 그 모양에 맞췄다. **이슈 `#42` §4에 통보한 내용이다.**

| # | 한 것 | 내용 |
|---|---|---|
| 1 | ✅ **점수 상한 제거** | `scoring.HINT_CAPS`·`cap_for()` 삭제. `bestScore`/`confirmedScore` → `score` 하나. **AI는 채점만 하고 가공하지 않는다** — 새 구조가 답변 3개 점수를 따로 저장하므로 상한은 백엔드·화면이 정한다 |
| 2 | ✅ **`StageScore`를 `problem_stage`와 1:1** | `axisCode` · `question`/`firstHint`/`secondHint` × (`Score`, `Passed`) · `status`. 백엔드가 변환 없이 INSERT할 수 있다 |
| 3 | ✅ `attemptCount`·`hintsUsed`·`autonomy` 제거 | 전부 위 6필드에서 파생된다. **그래서 컬럼 추가 요청도 하지 않았다**(옛 B-14 철회) |
| 4 | ✅ 세션 상태 어휘 8종 | `EXPIRED` 삭제(정의서에 없다 → INSERT가 깨진다), `INTERRUPTED`·`INVALID`·`SUPERSEDED` 추가 |
| 5 | ✅ 잡 상태 `PARTIAL` | 요구사항 판정만 실패하면 `PARTIAL` + 사유. 예전엔 `SUCCEEDED`라 **화면에 "요구사항 전부 미충족"이 사실처럼 떴다** |
| 6 | ⏸ `ai_usage` → `context_type`/`context_id` | **`#42` §3-1 회신(시트 갱신본) 대기.** 새 MEAS 비고가 두 곳에서 그렇게 쓰는데 시트를 못 봤다 |

**갈린 판단 1개**

| 판단 | 내용 |
|---|---|
| `summarize_stages`가 마지막 턴만 남기지 않는다 | 옛 구조는 축당 1행이라 "마지막 시도가 그 축의 결과"였다. 새 구조는 답변 3개가 **같은 행의 다른 슬롯**이라, 마지막만 남기면 **"힌트 없이 몇 점이었나"가 사라져 자력 판정이 불가능해진다.** 턴의 `hints_used`로 슬롯을 골라 흩뿌린다 |

**`TranscriptTurn.hintsUsed`는 남겼다.** `StageScore`에서는 뺐지만 턴에는 필요하다 —
**어느 슬롯에 저장할지를 정하는 값**이라서다(0=질문 · 1=firstHint · 2=secondHint).

`terminationReason`은 **응답에 계속 실어 보내되 저장 요청은 하지 않는다.**
`problem_stage.status` 조합으로 완전히 파생되기 때문이다(마지막 `NOT_PASSED` 축 = 종료 축).

**실호출 검증** (2026-08-03)

```
L1  questionScore 5 · passed true                              status PASSED
L2  questionScore 1 · false  →  firstHintScore 4 · true        status PASSED
L3  전부 null                                                   status NOT_REACHED
L4  전부 null                                                   status NOT_REACHED
reachedStage 2 · retest false
```

DB CHECK 두 개를 자연히 만족한다 — `firstHint`에 값이 있으려면 `questionPassed=false`여야
하고, 미도달 축은 점수가 전부 `null`이다. 힌트 1개를 쓰고 4점을 받은 턴이 **4점 그대로**
나갔다(옛 규칙이면 상한 4에 걸려 우연히 같았겠지만, 이제 상한 자체가 없다).

### T17 — 백엔드 1차 회신 반영 ✅ 완료 (2026-08-03, 232 tests)

`#42` 회신이 왔고 확정분을 코드에 반영했다.

| # | 한 것 | 근거 |
|---|---|---|
| 1 | ✅ `aiUsage` → `contextType`/`contextId` | 백엔드가 필드명 변경을 확인해줬다(01_SYS) |
| 2 | ✅ 통과 기준 3점 **고정** | 공통 정책으로 확정. 요청에 `passScore`를 받지 않는다 |
| 3 | ✅ **`isGeneral` 필드째 삭제** + `_general_topics()` 폴백 제거 | PM 결정 — 아래 참조 |
| 4 | ✅ 못 찾은 teach **재시도 1회** | PM: "최대한 찾아보고 그래도 없으면 없다고 박아라" |
| 5 | ✅ `unmatchedTeaches` 신설 | `―`(문항 없음)을 백엔드가 역산하지 않게 |
| 6 | ✅ `courseLabel` 필수화 · 교안 결과 한글 고정 | 아래 참조 |

**🔴 개념이 코드에 없을 때 — "없으면 없다" (2026-08-03 PM 확정)**

```
오퍼레이터가 고른 teaches 는 고정이다 — 모든 학생이 같은 개념을 시험 본다
  ① 최대한 찾는다     p04-3 + 실패한 teach 만 모아 재시도 1회
  ② 그래도 없으면      그 문항은 없다. unmatchedTeaches 로 보고한다
```

**폐기된 대안 3개와 이유**

| 폐기 | 왜 |
|---|---|
| 다른 teach로 대체 선정 | 학생마다 다른 개념을 시험 보게 되어 **개념별 도달 비교가 깨진다.** MG-06이 회차별로 같은 개념 3개를 가로로 놓는 구조다 |
| 앵커 없는 일반 문제(`isGeneral`) | 위와 같다. 오퍼레이터가 고른 개념을 조용히 갈아치우는 자리였다 |
| 합성 코드 생성 + 3인칭 루브릭 | 루브릭 L2·L4가 **"네가 쓴 코드"** 전제다. 합성 코드에 "왜 이렇게 했나"를 물으면 학생이 할 답이 "제가 안 썼는데요"뿐이라 전원 저점으로 수렴한다 |

🔴 **`―`(문항 없음)과 `0단`(L1 미달)은 다른 것이다.** 도달 단계에 0을 박으면 "안 물어봤다"가
"틀렸다"로 바뀐다 — 문항 없음은 NULL이어야 한다. 백엔드에 이 구분을 요청했다.

**교안 분석 2건**

```
courseLabel 필수화   생략 시 매니페스트 기본값 'Java' 로 프레이밍된다.
                    영어 교안 실측에서 발견 — 업로드 화면이 과정을 이미 안다
한글 출력 고정        매니페스트(vendor)에 언어 지시가 없다.
                    stages.call(extra_user=...) 로 붙였다 — vendor 안 건드림
                    번역 금지 항목 명시: JSON 키 · unit_id · kind · 기술 용어
                    실측: guardrail(안전장치) · deterministic · Agents SDK 원문 유지
```

**곁가지로 잡은 버그 1개**: 검증 에러 메시지가 `": Field required"`로 나갔다 —
`format_validation_message`가 `loc[1:]`로 무조건 잘라서, multipart payload를 손으로 파싱하는
경로(`/analyses`·`/curricula`)는 `loc`에 `"body"`가 없어 **필드명이 통째로 날아갔다.**
백엔드가 뭘 빠뜨렸는지 알 수 없는 메시지였다.

### T18 — 교안 사전 재료 확보 ✅ 완료 (2026-08-04, 234 tests)

**PM 설계 v2 §7(소재 선별 교체)의 준비 단계다.** 로직은 안 바꾸고 **계약만 미리 맞췄다** —
나중에 하면 백엔드를 두 번 고치게 된다.

`teaches[]`에 3개 추가. **LLM 호출·토큰이 늘지 않는다.**

| 필드 | 어디서 왔나 | 쓰임 |
|---|---|---|
| `kind` (`CONCEPT`/`CODE_EXAMPLE`/`CAUTION`) | **p01-2가 이미 답에 담아 보냈는데 `_merge()`가 버렸다** | `CODE_EXAMPLE`이 식별자 추출원 · `CAUTION`이 L4 재료 |
| `evidence` | 같음 — 받고도 버렸다 | 추가 식별자 추출원(정의 문장보다 코드 이름이 많다) |
| `siblingNames` | **계산.** 같은 unit의 다른 개념 | 교안이 대안을 가르쳤다는 신호 → PM §7-3의 두 필터 중 하나 |

**왜 이 방향인가** — 룰 스캐너의 "중요도" 알고리즘은 **교안 연결이 없던 시절의 답**이다.
PoC 팀원이 LLM 없이 "중요한 코드가 뭔가"를 정하려고 `fan_in`·중복 정의·`idiom_filter`를
쌓았는데, **교안이 생기면서 그 질문 자체가 사라졌다.** 물어볼 개념은 이미 정해져 있고
남는 질문은 "그 개념이 코드 어디 있나" 하나 — 그건 grep이다.

실측이 뒷받침한다: 도서관 6파일·petclinic 49파일 둘 다 **룰 후보가 1개**밖에 안 나왔다.
폐기 대상 자산이 이미 실질적으로 안 쓰이고 있다.

**식별자 추출률 실측** (34쪽 영어 교안, teaches 70개)

```
지금        canonicalName + canonicalDescription 만 긁어서   24% (17건) · 고유 32개
            노이즈: 'content를' 'new_messages를'  ← 한국어 조사가 붙는다
기대        CODE_EXAMPLE · evidence 가 살아나면 오른다 — 아직 측정 안 함
```

⏳ **`kind` 분포는 아직 모른다.** 지금까지 버려서 데이터가 없다. 다음 교안 실행에서
`CODE_EXAMPLE`이 70개 중 몇 개인지 보면 사전 품질을 가늠할 수 있다.

### T19 — references 채우기 + 섹션 병합 수정 ✅ 완료 (2026-08-04, 240 tests)

**🔴 `ReferenceType`이 새 MEAS와 안 맞았다 — 조용한 지뢰였다.**

```
우리      CALLER · CALLEE · DEFINITION · TEST · CONFIG · SIMILAR
새 MEAS   PRIMARY_BLOCK · QUESTION_HIGHLIGHT · CALLER · RELATED_CONTEXT · CURRICULUM_EVIDENCE
겹침      CALLER 하나뿐
```

`references[]`가 **항상 빈 배열이라 안 터졌을 뿐**이고, 채우는 순간 전부 CHECK 위반이었다.
어휘부터 맞추고 채웠다.

**채운 것** (LLM 0회 — 이미 산정된 사실만 조립)

| 유형 | 개수 | 근거 |
|---|---|---|
| `PRIMARY_BLOCK` | 1 | 문제를 낸 그 지점 |
| `QUESTION_HIGHLIGHT` | 4 | 축별 강조 구간. `axisCode` 필수 |
| `CURRICULUM_EVIDENCE` | 0~1 | teach 연결. **코드 라인이 없다** |
| `CALLER` | 0~3 | import 그래프. 상한 3 |

⚠️ **`RELATED_CONTEXT`는 안 만든다.** 심볼 테이블이 없어 "같이 봐야 하는 자리"를 특정할
근거가 없다 — 지어내면 학생이 무관한 코드를 읽는다.

⚠️ **`QUESTION_HIGHLIGHT` 4개가 전부 같은 구간을 가리킨다.** 축마다 다른 곳을 짚으려면
LLM이 필요하고 지금은 근거가 없다. 그래도 넣는 이유는 DB가 축별 행을 기대하고, 화면이
"L3에서는 여기를 보세요"를 그리려면 자리가 있어야 해서다.

**`imports.py` 이식** — 팀원 브랜치 `feature/code-importance-map`의 `graph.py`(`f2db763`)에서
다국어 import 정규식(JS·PY·JAVA·C)과 Java 주석·문자열 제거를 가져왔다. **`fan_in` 점수는
안 가져왔다** — 공용 모듈일수록 판단이 빠져 있어 중요도로 쓰면 안 되는 값이다(PM §7-1).
역방향 색인("누가 나를 import 하나")만 남겼다.

실측(spring-petclinic 49파일): importer가 있는 파일 11개. `Person.java ← Owner·Vet`처럼
상속 관계가 잡힌다.

**🔴 `_merge()` 재작성 — `unit_id`로 합치면 안 된다**

모델은 `unit_id`를 **청크마다 독립적으로** `"01"`·`"02"`로 매긴다. 청크 1의 `"02"`와 청크 5의
`"02"`가 같은 단원으로 합쳐졌다.

```
전   섹션 3개   p.4-6(7) · p.5-31(48) · p.8-34(15)   범위 2쌍 겹침
                siblingNames 평균 31.2  ← "교안이 대안을 가르쳤다" 신호로 못 쓴다
후   섹션 15개  p.4-4 … p.32-34 순서대로              범위 1쌍(쪽 경계라 정상)
                siblingNames 평균 4.0 · 최대 7
```

**제목이 같고 페이지가 이어질 때만 합친다**(`_find_continuation`, 허용 간격 2쪽). 청크 경계에
걸친 단원은 이어지고, 멀리 떨어진 동명 단원은 따로 남는다. 회귀 테스트가 양쪽을 고정한다.

**교안 사전 실측** (34쪽 영어 교안)

```
kind          CONCEPT 43 · CODE_EXAMPLE 12 · CAUTION 3   (58개)
evidence      전부 채워짐
식별자 추출    teach 수 25% (변화 없음) · 고유 22 → 37 (68% 증가)
              새로 얻은 것: GuardrailFunctionOutput · WebSearchTool() · tripwire_triggered …
```

`evidence`가 사전 크기를 키웠다. **커버리지(teach 수)는 안 늘었다** — `evidence`에 식별자가
있는 teach는 이미 `canonicalDescription`에도 있었다.

### T16 — 백엔드 회신 ✅ **전부 회신됨** (2026-08-04)

**우리 쪽 코드 변경은 0이었다.** 응답이 이미 그 값을 들고 있었고 저장 자리만 없었다.

| 항목 | 회신 | 우리가 한 일 |
|---|---|---|
| **B-12** `code_text` 컬럼 | 반영 | 없음(이미 `codeSnippet`으로 보낸다) |
| **B-13** `analysis_document JSONB` | 반영 | 없음(이미 보낸다) |
| **되물음 1** 도달 단계 컬럼 위치 | `best_success_stage`는 **팀원 개별 속성**이다. 문제 행이 팀원마다 복사된다 | 없음 |
| **되물음 2** `ai_model` 등록 | 3건 등록 완료 | 없음. 이제 기본값 대신 등록된 코드가 온다 |
| **되물음 3** 문항 없음 | `generation_status='NOT_GENERATED'`로 저장 | 없음. `unmatchedTeaches[]`가 그 재료다 |
| **고지** `contextType` 값 집합 | ⓑ 채택 — `GRADING` / `sessionId` | 없음(이미 그 동작). 주석의 "미확정" 경고만 삭제 |
| **요청** `teaches` 3컬럼 | `kind`·`evidence`·`sibling_names` 추가 완료 | 없음(이미 보낸다) |

**개명**: `TEAM_COMMON` → `TEAM_SHARED_PROBLEM`. 뜻이 "팀원이 problemId를 공유한다"가 아니라
**"팀 코드 분석에서 한 번 생성되어 팀원 개인 세션이 개별로 갖는 문제 원본"**이었다.

#### 🔴 세션 생성 모델이 드러났다 — 문제 행은 팀원마다 복사된다

```
팀원 1명이 제출        submission (+ submission_artifact)
분석 1회               code_analysis          ← 다른 팀원 제출은 재분석 방지
팀원 수만큼 세션 생성   assessment_session × N
세션마다 문제·단계      assessment_problem 3 × N,  problem_stage 4 × 3 × N
```

팀원 8명이면 **문제 행이 24개**다. AI는 `problemId`를 3개만 발급한다 —
**그 3개는 원본이고 팀원별 사본은 백엔드가 PK를 새로 판다.**

✅ **세션·보고서 요청의 `problemId`는 사본이다** (2026-08-04 백엔드 확정).
`assessment_problem`이 팀원별 문제 현황 테이블이라 그 PK가 곧 사본이다. AI는 무상태라
받은 id를 그대로 되돌려주므로 코드 변경은 없다 — **분석 응답의 `problemId`(원본)와
세션 요청의 `problemId`(사본)는 서로 다른 값이고, 그 매핑은 백엔드가 든다.**

✅ **문항 없음 사유는 문장으로 저장된다** (2026-08-04). `not_generated_reason_code`를
코드가 아니라 설명 텍스트를 담도록 바꾸기로 했다 — `unmatchedTeaches[].reason`이
**교육생 화면에 그대로 뜬다.** 그래서 내부 진단(`Selection.dropped`)을 그 필드로 흘리지
않는다(`topics.NOT_FOUND_REASON`).

✅ **재시험은 새 `assessment_session`을 만든다** (2026-08-04). AI 쪽 변경 없음 —
다시 볼 문제만 `problems[]`에 실어 기존 엔드포인트를 그대로 부르면 된다.

### T13 — 백엔드 연동 + `references[]` 채우기

**연동이 먼저다.** `references`는 백엔드 작업이 0이라(테이블·스키마가 이미 있고 빈 배열이 안 빈 배열이 될 뿐) 언제 해도 된다.

```
2단계-a   DEFINITION + TEST + CONFIG      싸다. 파일명 규칙 + 식별자 인덱스
2단계-b   CALLER/CALLEE + SIMILAR         언어별 파서 필요. 별도 판단
```

스캐너가 **정규식·패턴 기반이라 심볼 테이블도 import 그래프도 없다.** `DEFINITION`("클래스 정의가 다른 파일에 있다")이 제일 값싸고 효과가 크다. `SIMILAR`은 L3(대안 비교)의 재료라 값어치는 있지만 제일 비싸다.

### T14 — 고정 HTTPS 주소 ✅ 해소 (2026-08-03) — **우리 작업이 아니다**

```
https://cpiysizen3.ap-northeast-1.awsapprunner.com
```

팀원이 **AWS App Runner에 `main` 브랜치 자동 배포**를 걸어 줬다. 계정·설정 소유자도 팀원이다.
**배포는 이제 우리 할 일 목록에서 빠진다** — `main`에 올리면 끝이고, 명령도 서버 접속도 없다.
터널 URL이 바뀌던 문제도 같이 사라졌다(주소가 고정이라 백엔드가 한 번만 넣으면 된다).

⚠️ **남는 함의 둘**

```
1  main 머지 = 즉시 재배포 = 진행 중 job 소멸
   세션은 무상태라 안 깨지지만(매 요청이 restore) 폴링 중인 /analyses·/reports job 은 사라진다
   → 머지 시점은 백엔드와 맞춘다

2  인스턴스 1개 고정
   job 저장소가 인메모리 dict 라 2개면 만든 프로세스와 조회 프로세스가 달라져 폴링이 404
```

🔴 **`main`이 낡았다.** 최근 커밋이 `#31` 시절이라 오늘 작업(모델 교체·GITHUB_URL·점수 상한
제거·StageScore 1:1)이 하나도 안 들어가 있다. **지금 배포된 것은 옛 계약이다.**
`feature/stabilize` → `develop` → `main` 머지가 되어야 반영된다.

### 후속 (순서 미정)

```
· 유료 전환         529 실패율 64%의 근본 해결. 팀 논의 필요
· 교안 소요 실측     100쪽 상한을 아직 못 준다
· 폴링 응답에 진행률  operator가 언제 끝나는지 모른다 (교안 대형 PDF)
· 재시험용 별도 시험지 분석 때 병렬로 한 벌 더
· §7-8 선별 로직 교체  아래 참조
```

#### 🔴 미결 — §7-8 선별 로직 교체

PM 설계서(`Frontend/docs/plan/v2/14-verification-design.md`)가 **룰 스캐너 자산 대부분을 폐기**하라고 한다.

| 자산 | 처분 | 우리 상태 |
|---|---|---|
| `find_hub` / fan_in 중요도 | 폐기 | `rules.py`가 `hub` 반환 중 |
| `find_duplicate_definitions` | 폐기 | vendor에 있음 |
| `idiom_filter.py` | 폐기 | vendor에 있음 |
| `tier_b_risk_triggered_scan` | 이해도 경로 제외 | 살아 있음 |
| `tier_a_structural_scan` | 재활용 | 유지 |
| `find_architecture_diffusion_point` | 폴백에서만 | 상시 사용 중 |

대체재는 **교안 사전 기반 선별** — `concept`/`siblings`/`warns`/`gap`을 교안에서 뽑아 코드와 매칭한다. **교안 분석이 문답 소재 선별의 상류가 된다.** 우리는 교안을 "완전 별개 흐름"으로 잡았는데 실제로는 아니다.

`feat/poc_full`의 Tier B 제거(`756c4cb`)가 이 설계를 따라간 것으로 보인다. 그러면 vendor 동기화가 "나중 일"이 아니라 선별 교체의 일부다.

**로직 교체는 아직 안 한다** — 선별 방식은 문제의 *질*이지 파이프라인 *동작*이 아니다.
다만 **재료(교안 사전)는 §T18에서 확보했다.** 계약이 먼저인 이유는 백엔드를 두 번 안
고치기 위해서다.

⚠️ **`references[]` 채우기와 이 항목은 같은 뿌리다.** 둘 다 "코드 사이의 관계를 안다"가
필요하고, 팀원 브랜치 `feature/code-importance-map`의 `graph.py`(다국어 import 그래프)가
양쪽 재료다. 순서를 정할 때 같이 본다.

#### 확인 필요 (PM)

**L3·L4가 "선택 구간"인데 재시험 기준은 3단 이상이다.** §4-1은 L2까지 필수라 하고 §9-1은 2단 이하면 재시험이라 한다 — 그러면 L3 도달이 사실상 필수가 된다. 우리는 **L1·L2 기준으로 진행 중**이고 사용자도 그렇게 판정했다. 설계서 내부 모순으로 보인다.

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
