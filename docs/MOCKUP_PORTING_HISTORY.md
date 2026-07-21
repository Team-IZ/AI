# 목업(Pyodide) 시절 이식 기록 — 이력 보존용

> 이 문서는 FastAPI 전환 **이전**의 상태를 기록한 것이다. 지금의 실행 방법·구조는
> `../README.md`를 봐라. 여기 적힌 실행 방법(`python3 -m http.server`)은 **더 이상 유효하지 않다.**
>
> 그럼에도 이 문서를 남기는 이유: 현재 `pipeline/`·`shared/`에 있는 코드가 "어디서 왔고,
> 어떤 방식으로 이식됐고, 무엇이 실제로 검증됐는지"가 전환 작업의 판단 근거이기 때문이다.
> 특히 §"검증"의 E2E 통과 내역은, FastAPI 전환 후 동작이 달라졌을 때 "원래는 이랬다"의
> 기준점이 된다.
>
> 원문 출처: 구 `Readme.md` (커밋 `7b23213` 시점), 브랜치 `feature/verification-ui`.

## 원본 레포와의 관계

이 코드는 [`popixoxipop-collab/Code_reviewer_with_feedback`](https://github.com/popixoxipop-collab/Code_reviewer_with_feedback)의
Pipeline Lab(`docs/lab/`)에서 동작 중이던 **P02(코드 분석) → P03(소크라틱 검증 세션) → 결과 리포트**
기능을, Team-IZ/Frontend의 화면정의(`team-iz.github.io/Frontend/`, `gh-pages` 브랜치)와
동일한 UI/UX로 다시 입힌 것이다. **기능은 원본 그대로, 스타일/페이지 구조만 달랐다.**

원본 Pipeline Lab은 계속 원본 레포에서 유지보수된다. 이 코드는 그 시점(D176~D184)의
스냅샷이며, 원본이 이후 업데이트되어도 자동 동기화되지 않는다.

> 파이프라인 소스(`pipeline/`)의 vendoring 출처·커밋 SHA는 별도로 `../pipeline/VENDORED.md`에
> 기록돼 있다(커밋 `9bea5fc` 기준). 이 문서와 그 문서는 서로 다른 이식 작업을 다룬다 —
> 여기는 **브라우저 목업 UI 이식**, 저기는 **분석 파이프라인 Python 소스 내재화**.

## 이식 방법론 (그대로 유효한 부분)

원본 `p02-runner.js`/`p03-runner.js`는 로직과 DOM 렌더링이 한 파일에 섞여 있었다.
이 포팅은 "이해한 내용을 바탕으로 재작성"이 아니라 **원본을 통째로 복사한 뒤 DOM 접점만
훅 호출로 기계적으로 치환**하는 방식으로 진행했다. `shared/p02-engine.js`·`shared/p03-engine.js`
상단 주석에 각 파일의 정확한 변경 목록이 있고, `reference/`의 원본과 diff하면 훅 치환으로
명시한 줄 외에는 100% 동일하다.

`run()`은 훅 객체를 받는 고차 함수다:

```js
P03Engine.run({ finding, codeContexts, model }, {
  onStatus, onProgress, onRunStart, onRunEnd,
  onQuestion, getAnswer, onAnswerRecorded, countdown,
});
```

이건 새 설계가 아니라 원본 Python `feedback/turn_engine.py`의
`run_decision_point(..., answer_fn)` 시그니처를 그대로 복원한 것이다(JS 포팅 과정에서
DOM Promise로 변형됐던 걸 원래 모양으로 되돌림).

**FastAPI 전환에서의 함의**: `turn_engine.py`가 Python 원형이므로, Phase 3의 세션 API는
이 JS 훅 구조를 다시 Python으로 번역할 필요 없이 원본 Python을 base로 수정하면 된다.

## 당시 페이지 구조

```
trainee/
  submission.html  -- 코드 제출(GitHub URL/ZIP) + 연결 설정 패널 + finding 목록
  session.html     -- 소크라틱 검증 세션(4턴: L1/L2/L3/Reflection)
  result.html      -- 5축 채점 결과 리포트
shared/
  iz-tokens.css      -- Team-IZ 디자인 토큰(:root 변수) + 공통 컴포넌트(.viewport/.ttop/.wrap)
  config.js/db.js/llm.js/pyodide-shared.js  -- 원본 Pipeline Lab에서 무수정 이식
  lab-core.js        -- app.js에서 순수 매니페스트/템플릿 계층만 축출
  traffic-rate.js    -- debug-traffic.js에서 순수 rate-check 로직만 축출
  p02-engine.js      -- p02-runner.js 복사 후 DOM 접점만 훅으로 치환
  p03-engine.js      -- p03-runner.js 복사 후 DOM 접점만 훅으로 치환
  session-state.js   -- 페이지 간 sessionStorage 핸드오프(신규 코드, 원본엔 없음)
prompt_manifest.json / webtool_driver.py  -- 원본에서 무수정 이식
reference/           -- 이식 중 대조용 원본 p02-runner.js/p03-runner.js/app.js 사본
```

> ⚠️ 세션 턴 구조가 **바뀌었다**: 목업은 L1/L2/L3/**Reflection** 4턴이었지만,
> 전환 후 확정 구조는 **L1 → L2 → L3 3단(Reflection 턴 제거)**이다.
> 채점도 per-답변 채점에서 **세션 종료 후 transcript 전체 5축 후채점**으로 바뀐다.
> 계획서 §0 참고.

## 검증 내역 (목업 시점 실측 — 전환 후 회귀 판단의 기준선)

- **소스 diff**: `p02-engine.js`/`p03-engine.js`를 `reference/`의 원본과 비교, 문서화된
  변경 외 로직 차이 없음을 확인.
- **실제 E2E 실행**(Playwright + 실제 NVIDIA API 호출): ZIP 제출 → 실제 Pyodide 스캔 →
  finding 2건(direct-match 1건 + text-mention 1건, D179/D180 두 커넥터 경로 모두) →
  검증 세션 자동 시작 → 4턴 전부 실제 질문 생성+답변 제출+실제 Pyodide 분류 → 5축 채점 →
  결과 페이지 렌더링까지 전 과정 통과. GitHub URL 제출 경로도 별도 확인(성공/실패 둘 다).
- 이 과정에서 실제 버그 3건 발견·수정: session.html에 Pyodide 스크립트 태그 누락,
  `.hidden` 유틸리티 클래스가 어느 CSS에도 정의되지 않아 시각적으로 안 숨겨짐,
  진행 체크리스트 아이콘이 상태 전환 시 색상만 바뀌고 글리프(✓/◔/•)는 안 바뀜.
- direct-navigation 폴백(세션 데이터 없이 session.html/result.html 직접 접근) 확인 완료.

## 범위 밖이었던 항목 (Team-IZ 원본엔 있지만 이 포트엔 없음)

원본 Pipeline Lab에 없던 기능은 새로 만들지 않았다:

- 커밋 이메일 검증(귀속 분석 자체가 없음) — **FastAPI 전환에서 구현 예정**(명세서 §3.2 `attribution`)
- 이의제기 워크플로(매니저 검토 백엔드 없음) — Spring 소관
- 다회차 성장추이 차트(다회차 집계 없음) — Spring/RPT 소관
- 교안 위치 안내(매핑 데이터 없음)
- 비공개/공개범위 잠금 변형(공개범위 개념 없음)
- 코드 줄 단위 하이라이트(`.hl`) — evidence 줄 번호 추출 로직 부재로 전체 코드만 표시

반대로 원본 Pipeline Lab에는 있지만 Team-IZ 원본엔 없던 것(그대로 유지):

- **채점 결과 숫자 점수 즉시 노출**: Team-IZ 원본은 점수를 절대 노출하지 않지만, 이 도구의
  실제 사용자(파이프라인을 직접 테스트하는 팀원)는 채점이 맞게 됐는지 바로 확인해야 해서 유지.

## 알려진 동작 차이 (목업 시점, 의도적)

- **모델 실시간 전환 불가**: 원본은 인터뷰 도중 모델 선택을 바꾸면 다음 턴부터 적용됐지만,
  이 포트는 세션 시작 시점에 모델이 고정된다(시작 후 모델 선택기 잠김).
  `p03-engine.js`의 change #9 참고.
- **Google 로그인 리디렉트**: Supabase 프로젝트의 OAuth allow-list가 원래 배포 도메인
  (`popixoxipop-collab.github.io/.../docs/lab/`) 기준이라, 새 도메인에 배포하면 allow-list에
  경로가 추가되기 전까지 Google 로그인이 실패할 수 있다(Supabase 대시보드 설정 사항).
  NVIDIA 키 기반 P02/P03 실행은 로그인과 무관하게 동작하고, 로그인 실패 시 DB 저장만 건너뛴다.

> standalone 모드에서는 Supabase가 Spring 대역으로 확정 데이터를 저장한다(계획서 §1.5).
> 위 OAuth allow-list 이슈는 Phase 5에서 `supabase_store.py`를 구현할 때 다시 검토 대상이 된다.
</content>
</invoke>
