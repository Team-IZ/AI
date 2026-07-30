# P04 통합 검증 PoC (`feat/poc_full`)

교안 teaches → 코드 제출 → 코드 분석 → 문답(L1~L4) → 보고서를 한 줄기로 잇는 통합 PoC.
`feat/pdf_analysis`(P01 교안분석)와 `feat/code_Q&A`(P02 코드분석/P03 검증세션) 둘 다와
관계있지만, 문답 루프의 채점 시점·힌트 규칙이 P03과 근본적으로 달라(아래 참고) 별도
브랜치/도구로 분리했다.

**배포**: `https://team-iz.github.io/AI/lab/poc/` (`.github/workflows/pages.yml`이 이
브랜치를 세 번째 소스로 조립한다 — 세부는 워크플로 파일의 D-poc1 주석 참고)

## 실행 방법

빌드 스텝 없는 바닐라 HTML/CSS/JS. 이 저장소 루트에서:

```bash
python3 -m http.server 8813
# http://localhost:8813/app/index.html
```

**시작하려면**: `app/index.html`의 "⚙ 연결 설정"에 NVIDIA API 키 + 프록시 URL 입력
(code-qna와 동일한 `worker/nvidia-proxy.js` 재사용, 이 브랜치도 vendored). 교안 목록은
로그인해야 보인다(팀 DB) — 아직 로그인 전이거나 DB에 분석된 교안이 없으면 "DB 없이 직접
입력" 경로로 P01의 unit_map JSON을 붙여넣어도 된다.

## 페이지 구조

```
index.html      -- 0+1단계: 교안 teaches 선택(정확히 3개) · 요구사항 P/F 입력 · 코드 제출
app/
  index.html      실제 1단계 페이지(vendored 파일의 "../" 상대경로 때문에 한 단계 깊이 필요,
                   아래 "왜 app/ 아래인가" 참고)
  analysis.html   2단계: 코드 분석 문서 · 요구사항 P/F 판정 · 문제 3개 선정 · L1~L4 질문 동결
  session.html    3단계: 문답 (문제당 최대 4레벨, 레벨당 힌트 최대 2회)
  report.html     4단계: 보고서 (문제×레벨 매트릭스 · 재시험 대상 · 교안 참조)

  scoring-config.js    ★ 축(L1~L4)×값(0~5) 루브릭 + 임계값(pass=3) + 힌트 상한(5/4/3) + 힌트
                         사다리 강도 정의(hintLadder) + 재시험 규칙
  prompt_manifest.json ★ p04 7개 스테이지 프롬프트/파라미터 (단일 소스)
  llm-stage.js         매니페스트 스테이지 1개 호출(fillTemplate -> chatJSON -> JSON 파싱) 공용 경로
  teaches-source.js    P01이 DB에 남긴 unit_map을 teach 목록으로 읽어옴(교안 분석 자체는 재구현 안 함)
  code-fragment.js     LLM이 지목한 {file,symbol}에서 실제 줄 번호를 우리가 찾아 산정(D-poc10)
  question-guard.js    질문·힌트에 선택지가 섞이는 걸 정규식으로 탐지(실측 사고 재발 방지)
  hint-ladder.js       문제 1개의 L1~L4 질문을 답변 전에 동결(freezeQuestionSet, D4) +
                         힌트 1개 생성(generateHint) -- hintMode에 따라 질문 직후 답변 없이
                         (frozen, D7) 또는 오답 확정 직후 답변 기반(adaptive, D4 개정)으로 호출
  requirements.js      요구사항 P/F 판정
  poc-engine.js         전체 오케스트레이션(2/3/4단계) -- 페이지는 DOM을 안 만지고 이 파일의 hooks만 받음
  poc-state.js          페이지 간 sessionStorage 핸드오프(teamiz_p04_* 키, code-qna와 분리)
  p04_schema.sql        ★ public.runs/presets의 pipeline CHECK 제약에 'p04' 추가(사람이 한 번 실행)
  p04_timing_schema.sql ★ public.runs에 hint_mode/timing_ms 컬럼 + p04_timing_view 추가(D8)

shared/ cognition/ judgment/ feedback/  feat/code_Q&A에서 무수정 이식(vendored, 드리프트 검사 대상)
worker/                동일 프록시 설정 재사용(재배포 불필요)
```

## 왜 이 구조인가 (결정 기록)

**D-poc1** (pages.yml): 세 번째 소스 브랜치로 조립. P03(문답)의 턴 루프는 "방어 성공 시
조기 종료 + 끝에서 한 번 5축 채점"인데, 명세는 "레벨마다 즉시 0~5점 채점 + 3점 미만이면
힌트 2회 재질의 + 실패 시 그 문제 종료"로 근본적으로 다르다. P03을 이 모양으로 고치면 팀이
쓰는 도구가 깨지므로 별도 도구로 분리했다. 세 브랜치 모두 워크플로 파일을 동일하게 유지해야
하고(push 이벤트는 push된 브랜치의 워크플로로 실행됨), 한 브랜치가 깨지면 세 도구 배포가
전부 막힌다.

**D2**: `trainee/`, `reference/`, `shared/p03-engine.js`를 이 브랜치에서 삭제. 안 지우면
`/lab/poc/`에 code-qna 도구의 두 번째 사본이 배포되는데, 이건 D-split2가 이미 겪고 없앤
문제(`docs/lab/code-qna/` 중복본 드리프트)의 재발이다.

**D-poc2**: `shared/`·`cognition/`·`judgment/`·`feedback/`은 feat/code_Q&A에서 무수정
이식 — P02 스캐너를 다시 구현하지 않기 위해서다. 두 브랜치의 사본이 어긋나지 않도록
`.github/workflows/pages.yml`에 드리프트 검사를 넣었다(byte-diff, 어긋나면 빌드 실패).

**D3**: 채점 임계값·루브릭을 `app/scoring-config.js` 한 파일로 외화(축×값 표). 사용자
요구("채점 임계값 하이퍼파라미터·채점 로직은 모듈화로 빼놓고 설정 가능하게") 반영.

**D4 (2026-07-28, 최초)**: 힌트는 문제 시작 시 4개 레벨 질문과 함께 한 번에 생성해
동결한다(답변을 본 뒤 생성하지 않음). graduated prompting(Campione & Brown, 1987) — 답변을
보고 힌트를 만들면 같은 실력의 두 학생이 다른 힌트를 받게 되어 "몇 번째 힌트에서
통과했는가"가 학생 차이가 아니라 생성 차이를 재게 된다.

**D4 개정 (2026-07-30, 실사용 피드백)**: 사용자가 이걸 뒤집었다 — 힌트는 그 레벨에서 학생이
실제로 낸 답변(질문+답변 전문+채점의 missing/evidence)을 본 뒤에 생성해야 그 학생이 실제로
놓친 지점을 겨냥할 수 있다. **질문 동결은 그대로다(안 바뀜)** — 바뀐 건 힌트뿐이다.
비교 가능성의 근거가 "힌트 텍스트가 동일함"에서 "사다리 단계 수(레벨당 2회)·강도 정의(아래
표, `app/scoring-config.js`의 `hintLadder`)·점수 상한(5/4/3)이 동일함"으로 이동했다 — 텍스트가
학생마다 달라지는 건 이제 의도된 설계다("왜 힌트가 다르냐"는 더 이상 버그가 아니다). 이게
오히려 표준 graduated prompting 구현(힌트가 학생의 실제 오류에 반응)에 더 가깝다.
COST: 오답마다 LLM 호출이 1회 늘어난다 — `session.html`의 타이핑 인디케이터(D-poc11)가
그 대기를 가시화한다. 실패/빈 응답/선택지 위반이 재시도 후에도 계속되면
`HintLadder.fallbackHint()`의 결정론적 문장으로 대체돼 힌트 미생성이 구조적으로 불가능하다.
프롬프트만 고치면 힌트 문구·강도가 바뀌도록 `app/prompt_manifest.json`의 `p04-7` 스테이지
하나로 모듈화했다(사용자 요구).

**D7 (2026-07-30, 팀 기획서 충돌 절충)**: D4 개정 직후 팀원 기획서(`poc-axis-order-fix.md`)가
"지금 계약은 동결 기준, 세션 런타임은 채점만(턴당 LLM 호출 1개), 적응형 힌트는 나중에 별도
반영"이라고 명시하며 정면 충돌했다. 사용자 지시(D4 개정)와 팀 계약(동결) 중 하나를 코드로
확정하지 않고, **`app/index.html`에 버튼 토글을 둬 언제든 전환 가능**하게 했다 —
`POCScoring.hintMode`(기본값 `"frozen"`, 팀 계약과 일치). 두 모드가 `HintLadder.generateHint()`
**같은 함수/프롬프트**를 공유한다: frozen은 질문 생성 직후 `attempts:[]`로(답변 없이) 호출해
`lvl.hints`에 동결, adaptive는 오답 확정 직후 실제 attempts로 호출. `app/prompt_manifest.json`의
p04-7 문구를 "답변이 있다면 참고, 없다면(동결) 질문 자체로 판단"으로 일반화해 두 컨텍스트
모두에서 말이 되게 했다. `analysis.html`에 현재 모드 배지를 표시한다.

**D6 → D6-fix (2026-07-30, 팀 기획서 대조)**: 세부질문 순서 확정 — `L1 코드기술 → L2
설계논리 → L3 대안 → L4 반례한계`(팀 기획서가 명시한 정확한 축 id/label). 최초 구현은
`L3=반례한계, L4=대안`이었고, 사용자 지시로 순서(order)만 먼저 맞바꿨는데 축 id를
`L3_대안비교`/`L4_반례대응`으로 새로 지었더니 팀이 이식할 백엔드(07_ENG의
`axis_score.axis_code`: `ALTERNATIVE_COMPARISON`/`COUNTEREXAMPLE_RESPONSE`)가 참조하는
정확한 문자열(`L3_대안`/`L4_반례한계`, label "반례 대응·한계")과 어긋났다 — 팀 문서 대조 후
키 이름을 다시 고쳤다. 값 단계 서술 내용은 처음부터 안 바뀌었다(각 축이 재는 능력은 동일).
재시험 판정(`retest.triggerAxis`)은 L1 고정이라 이 변경과 무관.

**D-poc6/D-poc7**: 질문·힌트에 선택지가 섞이는 사고(사용자 실측: 보기 준 학생이 대안비교
5점 받음 — 대안을 제시한 게 아니라 고른 것)를 `question-guard.js`가 정규식으로 잡아
재생성시킨다.

**D-poc10 (2026-07-30, 실사용 재현)**: "코드 조각 조회 문제" 보고 — 처음엔 LLM에게 줄
번호(`[시작,끝]`)까지 직접 세게 했는데, 실사용에서 특히 긴 파일에서 번호가 자주 틀렸고
`code-fragment.js`가 그걸 "무효"로 버려 "코드 조각을 확인할 수 없음"만 계속 떴다. LLM은
코드를 그대로 인용하는 건 잘하고 세는 것만 못한다는 게 원인이었다 — 그래서 LLM에게는
실제 코드 한 줄(`symbol`, 예: `"def pay(order, method):"`)만 그대로 옮겨 적게 하고, 그
문자열이 파일의 몇 번째 줄에 있는지는 `CodeFragment.locateSymbol()`이 직접 찾는다("산정된
사실"과 "LLM의 주장"을 분리 — D-poc6가 finding 검증에 이미 쓰던 원칙을 위치 탐색에도 적용).
블록 끝은 들여쓰기 기반 휴리스틱으로 추정(완벽하진 않지만 시작 줄은 항상 정확함이 보장됨).
`session.html`의 코드 패널도 조각(앞뒤 2줄)이 아니라 **해당 파일 전체 + 구간 하이라이트**로
바꿨다 — 조각만 보여줄 땐 L2~L4(설계논리·반례·대안)를 묻는데 주변 맥락(다른 함수, import,
호출부)이 안 보이는 문제가 있었다. 파일 본문은 `POCEngine.resolveFileContents()`로 세션
시작 시 재확보한다(D-poc5 유지 — sessionStorage에 코드 본문을 넣지 않는다).

**D-poc11 (2026-07-30, 실사용 재현)**: "질문 생성 시 … 애니메이션 없음" 보고 — `analysis.html`의
진행 아이콘이 정적 `◔` 글리프였고(회전 없음), `session.html`은 `onProgress: () => {}`로
채점·힌트 생성 중 진행 신호를 전부 버리고 있었다. `poc.css`에 회전 스피너 + 타이핑 점
`@keyframes`를 추가하고, `session.html`은 답변 제출 직후 타이핑 인디케이터 버블을 띄워
채점 결과 또는 다음 질문(힌트 생성 완료 후)이 그 자리를 대체하게 했다.

**D-poc12 (2026-07-30, 실사용 재현)**: "key 미입력 시 default 모델 리스트 존재" 보고 —
vendored `shared/lab-core.js:200`이 페이지 로드 즉시 `MODEL_CHOICES`를 `CURATED_MODELS`(13개,
이미 단종 확인된 모델 포함)로 채워서, 키를 넣기 전에도 정상 목록처럼 보였다. p01 브랜치는
이 문제를 `b878bd1`/`71d48a0`으로 이미 고쳤지만 vendored 파일 자체를 고쳤다 — 이 브랜치는
드리프트 검사 때문에 그럴 수 없어, `app/index.html`의 **렌더링 쪽에서만** 게이팅한다(배열은
여전히 13개로 차 있지만 이 페이지가 렌더를 안 함). 키 입력 전엔 칩 0개+안내문, 키
입력(포커스 아웃 시점, p01과 동일하게 "change" 이벤트) 후 실시간 카탈로그로 교체, 조회
실패 시에만 CURATED로 폴백하고 그 사실을 note에 표시.

**힌트 사다리** (레벨당 최대 2회, 소진 후 미달이면 그 문제 종료 → 다음 문제 L1. 강도 정의는
`app/scoring-config.js`의 `hintLadder`, 실제 문구는 `app/prompt_manifest.json`의 `p04-7`):

| 단계 | 종류 | 강도 정의 | 자력 판정 | 점수 상한 |
|---|---|---|---|---|
| 0 | — | — | 자력 | 5 |
| 1 | 관점 되짚기 | 답변에서 말하지 않은 관점을 짚어 재질의. 새 사실·정답 없음 | 자력 유지 | 4 |
| 2 | 범위 좁힘 | 질문 범위를 한 단계 좁혀 더 작은 하위질문으로 | 부분 자력 | 3 |

**재시험 판정**: L1(코드 기술)에서 힌트 소진 후 미달로 끝난 문제만 재시험 대상이다.
사용자가 준 예시(`1번문제(4,0/4,1/3,0/2,2)` `2번문제(3,0/2,2/X/X)` `3번문제(2,2/X/X/X)` →
`3번 재시험`)를 역산한 규칙 — L4 실패(1번, 마지막 레벨이라 자연 종료)와 L2 실패(2번)는
재시험 대상이 아니고, L1 실패(3번, 가장 기초 단계에서도 못 넘음)만 재시험이다.
**★ 이건 단일 예시에서 역산한 가설이다** — 실제 세션이 쌓이면 재검증 대상
(`app/scoring-config.js`의 `retest` 주석 참고).

**D-poc8** (`app/llm-stage.js`): `LabApp`은 매니페스트를 하나만 들고 있는데
`LabApp.loadManifest()`는 항상 저장소 루트의 `prompt_manifest.json`(p02용)을 불러온다.
이 PoC의 p04 스테이지는 별도 파일(`app/prompt_manifest.json`)에 있어서, 페이지 로드 시
`POCStage.ensureManifestLoaded()`가 두 파일을 fetch해 `manifest.pipelines.p04`를 합쳐
넣는다. 또한 `LabApp.resolveParam()`은 vendored `shared/lab-core.js`의 `overrides =
{p02:{}, p03:{}}`를 읽는데 `p04` 키가 없어 그대로 쓰면 죽는다 — p04에는 애초에 프롬프트
오버라이드 UI가 없으므로(P02/P03의 스테이지카드 에디터는 이 포트들에도 없음), `POCStage`는
그 함수를 거치지 않고 스테이지 파라미터 기본값을 직접 읽는다.

**D8 (2026-07-30, frozen/adaptive 소요시간 비교)**: "적응형과 동결 버전의 질문과 힌트 각각
소요 시간을 체크해야" 하는 요구 -- `HintLadder.generateHint()`/`freezeQuestionSet()`이 벽시계
시간(`Date.now()` 전후차, 재시도 포함 총 시간)을 재서 반환하고, `poc-engine.js`가 이걸 모아
`analysis.timing`(질문 생성 + frozen이면 힌트 사전생성 8건)과 `session.timing`(adaptive면
힌트 실시간 생성)으로 각 단계 저장 페이로드에 얹는다. `analysis.html`(질문·힌트 사전생성
소요시간 요약+상세), `session.html`(힌트 버블마다 "(N.N초)" 인라인 표시 -- frozen은 사전생성
시점 값, adaptive는 방금 생성한 값), `report.html`(세션 종합, frozen이면 "사전생성 8건 중
실제 세션에서 재사용된 건수"까지 attempts에서 역산)에 표시.
  - **DB 컬럼**: `app/p04_timing_schema.sql`이 `public.runs`에 `hint_mode text`(CHECK로
    `'frozen'`/`'adaptive'`만 허용) + `timing_ms jsonb` **실컬럼**을 추가한다(JSONB
    `input_meta` 필드가 아니라 "DB에 칼럼 구별 지어서"라는 요구를 문자 그대로 만족).
    vendored `shared/db.js`의 `startRun()`/`saveRun()`은 이 두 필드를 모르므로(고정된
    필드 집합만 INSERT/UPDATE, 드리프트 검사 대상이라 시그니처를 못 늘림),
    `poc-engine.js`의 `patchTimingColumns()`가 같은 run 행에 **직접 REST PATCH**를
    보내 채운다 -- 로그인한 사용자의 실제 session access_token을 써서 RLS
    `update own`(`member_id = auth.uid()`)을 통과한다(미로그인이면 0행 매칭으로
    조용히 실패, 기존 best-effort 관용과 동일). 조회 편의를 위해 `p04_timing_view`도
    같이 만든다(이 저장소의 `p01_questions_view`/`p03_progress_view`와 같은 관례).
  - COST: 미로그인 세션은 hint_mode/timing_ms가 DB에 안 남는다(화면 표시는 로그인
    무관하게 항상 됨). REST PATCH 실패는 onProgress로만 알리고 메인 저장 흐름을
    막지 않는다.

## DB 마이그레이션 상태

- **`app/p04_schema.sql`**: **적용 완료** (2026-07-29, Management API PAT으로 직접 실행).
  `public.runs`의 `pipeline` CHECK 제약이 `('p01','p02','p03')`로만 한정돼 있던 걸
  `'p04'`까지 허용하도록 변경. 적용 후 `insert ... pipeline='p04' ... rollback`으로
  실제 통과함을 확인(데이터는 남기지 않음).
  `public.presets`는 원본 스키마 파일에는 있지만 이 라이브 프로젝트(ref
  `oziaeqcvrkrqkhwrybfj`)에는 애초에 생성돼 있지 않음을 실측 확인 — 존재 여부를 먼저
  검사해 없으면 건너뛰도록 파일을 고친 뒤 재적용(1차 시도는 존재하지 않는 presets를
  참조하다 트랜잭션 전체가 롤백돼 runs 쪽도 같이 실패했었음, 파일 상단 주석에 기록).
- **`app/p04_timing_schema.sql`**: **적용 완료** (2026-07-30, Management API PAT).
  `public.runs`에 `hint_mode text`(CHECK 제약 포함) + `timing_ms jsonb` 컬럼과
  `public.p04_timing_view`를 추가. 적용 후 `information_schema.columns` 조회로 두
  컬럼이 실제로 생겼음을 확인.

## 검증

Playwright로 mocked LLM(system 프롬프트 문자열로 스테이지 식별) + 실제 Pyodide 스캔을 태워
전 과정을 실행 확인:
- 1단계: 교안 수동 입력(3개 제한) · 요구사항 행 추가/삭제 · GitHub/ZIP 토글 · 모델 카탈로그.
- 2단계: 실제 ZIP → 실제 `two_tier_scan.py`/`score_findings.py` 스캔 → 분석 문서(존재하지
  않는 파일을 지목한 decision_point가 실제로 "근거 무효"로 걸러짐 확인) → 요구사항 P/F →
  문제 3개(teach 중복 검증) → 질문 생성. 질문 가드가 실제로 선택지 포함 응답을 잡아
  재생성시키는 것(재시도 성공)과, 계속 위반 시 `flagged`로 종료하는 것(3회 시도 후 정지)
  둘 다 확인.
- 3단계: 사용자가 원 스펙에 준 예시(`4,0/4,1/3,0/2,2` `3,0/2,2/X/X` `2,2/X/X/X`)를 그대로
  재현하는 raw score 시퀀스를 주입해, 저장된 세션 결과가 그 예시와 **정확히 일치**함을 확인.
- 4단계: 매트릭스·재시험 배지(3번만 표시)·잘한점/부족한점/교안참조·요구사항 P/F 렌더 확인.
- 드리프트 검사: vendored 파일을 일부러 1줄 고쳐 `diff -r`가 실제로 잡는 것 확인 후 되돌림.
- **힌트 답변 기반 생성(D4 개정)**: `HintLadder.generateHint()`를 단위로 직접 호출해,
  프롬프트에 **학생 답변 전문이 실제로 포함**됨을 확인(D4 개정의 직접 증거). 선택지 포함
  응답 → 재생성 → 정상 힌트로 교체되는 경로와, 계속 위반 → `generated:false`로 결정론적
  폴백 문장이 나오는 경로 둘 다 확인.
- **심볼 기반 코드 위치**: `CodeFragment.locateSymbol()`을 실제 Python 함수 정의로 테스트 —
  정확일치/공백정규화매치/미매치 3가지, 그리고 들여쓰기 기반 블록 끝 추정이 다음 함수를
  침범하지 않고 정확히 함수 본문 끝에서 멈추는 것 확인.
- **모델 게이팅**: 키 없음(칩 0개+안내문) · 키+카탈로그 조회 성공(실시간 목록으로 교체) ·
  키+조회 실패(CURATED 11개 폴백+실패 note) 3가지 경로 모두 확인.
