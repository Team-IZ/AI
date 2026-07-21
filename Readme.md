# Team-IZ 스타일 검증 파이프라인 (`feat/code_Q&A`)

> D207 (2026-07-21): `feature/verification-ui`(D176~D184 스냅샷)를 원본 D206 상태로 최신화한
> 브랜치입니다. `feature/verification-ui`는 그 시점 그대로 별도 유지되며 이 브랜치가 대체하지
> 않습니다 — 최신 상태가 필요하면 이 브랜치(`feat/code_Q&A`)를 보세요.
>
> D185~D206 사이 반영된 주요 변경(전부 아래 "이식 방법론"과 동일한 diff-기반 재적용으로 반영됨):
> - **D197**: P03 채점이 "루브릭 전체를 마지막 한 턴만 보고 채점"하던 버그 수정 — 실제
>   진행된 레벨(`transcript`)에 매핑된 축만 채점하고, 도달하지 못한 축은 "미검증"으로 표시.
> - **D198**: 근거 코드 패널 좌우 스크롤, 답변 Enter 제출, 결과 리포트 "문답 원문 보기".
> - **D199**: 중복 질문 탐지에 overlap coefficient 보강(짧은 질문이 긴 질문의 완전
>   부분집합인 경우까지 탐지) + 턴별 누적 판정(verdict_note)을 매 질문 생성에 반영.
> - **D200**: L2/L3/Reflection 후속 질문 생성에 실시간 GitHub `list_files`/`read_file` 도구
>   접근 추가 — 학생 답변을 저장된 스니펫이 아니라 실제 최신 코드로 재확인 후 질문.
> - **D201/D202**: 답변 제출 확인 팝업(취소 시 이어서 수정 가능), 결과 리포트의 문답
>   원문을 실시간 채팅 버블 스타일로 재사용 + 토글 강조.
> - **D203**: 마지막 턴 제출 후 채점/리포트 생성 중임을 보여주는 스피너 오버레이.
> - **D204~D206**: D200의 실시간 재확인이 확률적으로 스킵되던 문제를 구조적으로 강제(첫
>   시도는 GitHub 도구 호출 없이는 질문 생성 불가), 중복 질문 재생성 시에도 이미 확보한
>   근거를 재사용(재조회 없이), 중복 판정 시 "겹치는 이전 질문"을 직접 인용해 모델이
>   비교하도록 개선, 저장소 정보 없음/실시간 확인 중 상태를 채팅 버블로 가시화.

이 브랜치는 [`popixoxipop-collab/Code_reviewer_with_feedback`](https://github.com/popixoxipop-collab/Code_reviewer_with_feedback)의 Pipeline Lab(`docs/lab/`)에서 실제로 동작 중인 **P02(코드 분석) → P03(소크라틱 검증 세션) → 결과 리포트** 기능을, Team-IZ/Frontend의 실제 화면정의(`team-iz.github.io/Frontend/`, `gh-pages` 브랜치)와 동일한 UI/UX로 다시 입힌 것입니다.

**기능은 원본 그대로, 스타일/페이지 구조만 다릅니다.** 새 백엔드 기능은 추가하지 않았습니다 — 아래 "범위" 참고.

## 실행 방법

빌드 스텝 없는 바닐라 HTML/CSS/JS입니다. 정적 서버로 `trainee/` 아래 3개 페이지를 띄우면 됩니다:

```bash
python3 -m http.server 8813   # 이 저장소 루트에서
# http://localhost:8813/trainee/submission.html 접속
```

GitHub Pages에 그대로 배포해도 동일하게 동작합니다(상대 경로만 사용, 빌드 불필요).

**시작하려면**: `submission.html`의 "⚙ 연결 설정"에 NVIDIA API 키를 입력하세요(팀원 각자 자기 키 사용, D137). 프록시 URL은 팀 공용 배포본이 기본값으로 채워집니다. P02(코드 분석) 자체는 LLM 호출이 없어 키가 없어도 동작하지만, P03(검증 세션)에는 필수입니다.

## 페이지 구조

```
trainee/
  submission.html   -- 코드 제출(GitHub URL/ZIP) + 연결 설정 패널 + finding 목록
  session.html       -- 소크라틱 검증 세션(4턴: L1/L2/L3/Reflection)
  result.html         -- 5축 채점 결과 리포트
shared/
  iz-tokens.css       -- Team-IZ 디자인 토큰(:root 변수) + 페이지 공통 컴포넌트(.viewport/.ttop/.wrap)
  config.js/db.js/llm.js/pyodide-shared.js  -- 원본 Pipeline Lab에서 무수정 이식
  lab-core.js         -- app.js에서 순수 매니페스트/템플릿 계층만 축출(탭 전환 UI 등은 제외)
  traffic-rate.js     -- debug-traffic.js에서 순수 rate-check 로직만 축출(SVG 차트 UI는 제외)
  p02-engine.js       -- p02-runner.js를 복사 후 DOM 접점만 훅으로 치환
  p03-engine.js       -- p03-runner.js를 복사 후 DOM 접점만 훅으로 치환(가장 정교한 이식 대상)
  session-state.js    -- 페이지 간 sessionStorage 핸드오프(신규 코드, 원본엔 없음)
prompt_manifest.json / webtool_driver.py  -- 원본에서 무수정 이식
reference/            -- 이식 작업 중 대조용으로 둔 원본 p02-runner.js/p03-runner.js/app.js 사본
```

## 이식 방법론

원본 `p02-runner.js`/`p03-runner.js`는 로직과 DOM 렌더링이 한 파일에 섞여 있습니다. 이번 포팅은 "이해한 내용을 바탕으로 재작성"이 아니라 **원본을 통째로 복사한 뒤, DOM 접점만 훅 호출로 기계적으로 치환**하는 방식으로 진행했습니다(`p02-engine.js`/`p03-engine.js` 상단 주석에 각 파일의 정확한 변경 목록이 있습니다). `reference/`의 원본과 diff하면 훅 치환으로 명시한 줄 외에는 100% 동일합니다.

`run()`은 이제 훅 객체를 받는 고차 함수입니다:
```js
P03Engine.run({ finding, codeContexts, model }, {
  onStatus, onProgress, onRunStart, onRunEnd,
  onQuestion, getAnswer, onAnswerRecorded, countdown,
});
```
이건 새 설계가 아니라, 원본 Python `feedback/turn_engine.py`의 `run_decision_point(..., answer_fn)` 시임을 그대로 복원한 것입니다(JS 포팅 과정에서 DOM Promise로 변형됐던 걸 원래 모양으로 되돌림).

## 검증

- **소스 diff**: `p02-engine.js`/`p03-engine.js`를 `reference/`의 원본과 비교, 문서화된 변경 외 로직 차이 없음을 확인.
- **실제 E2E 실행**(Playwright + 실제 NVIDIA API 호출): ZIP 제출 → 실제 Pyodide 스캔 → finding 2건(direct-match 1건 + text-mention 1건, D179/D180 두 커넥터 경로 모두) → 검증 세션 자동 시작 → 4턴 전부 실제 질문 생성+답변 제출+실제 Pyodide 분류 → 5축 채점 → 결과 페이지 렌더링까지 전 과정 실제로 통과. GitHub URL 제출 경로도 별도로 확인(성공/실패 케이스 둘 다).
- 이 과정에서 실제 버그 3건을 발견·수정: session.html에 Pyodide 스크립트 태그 누락(분류기가 Pyodide를 쓰는데 "재스캔 없음"이라는 이유로 빠뜨렸음), `.hidden` 유틸리티 클래스가 어느 CSS에도 정의되지 않아 시각적으로 안 숨겨짐, 진행 체크리스트 아이콘이 상태 전환 시 색상만 바뀌고 글리프(✓/◔/•)는 안 바뀜.
- direct-navigation 폴백(세션 데이터 없이 session.html/result.html 직접 접근) 확인 완료.
- **D207 최신화 검증**: 모든 `shared/*.js`/`reference/*.js` syntax check 통과. `trainee/session.html`+`trainee/result.html`을 대상으로 Playwright E2E 재실행(모킹된 NVIDIA 프록시+GitHub API, 실제 Pyodide 분류기) — 근거 코드 패널 좌우 스크롤(D198), Enter 제출 시 확인 팝업(D201), L2 질문 생성 시 실시간 `⚙ list_files 호출 중...` 버블 노출 및 근거 파일명 인용(D204/D205), 마지막 턴 이후 채점 스피너 오버레이(D203), 결과 리포트의 채팅 버블 문답 원문+턴수 배지(D202) 8개 항목 전부 확인.

## 범위 밖 (Team-IZ 원본엔 있지만 이 포트엔 없는 것)

원본 Pipeline Lab에 없던 기능은 새로 만들지 않았습니다:
- 커밋 이메일 검증(귀속 분석 자체가 없음)
- 이의제기 워크플로(매니저 검토 백엔드 없음)
- 다회차 성장추이 차트(다회차 집계 없음)
- 교안 위치 안내(매핑 데이터 없음)
- 비공개/공개범위 잠금 변형(공개범위 개념 없음)
- 코드 줄 단위 하이라이트(`.hl`) — evidence 줄 번호 추출 로직이 아직 없어 1차는 하이라이트 없이 전체 코드만 표시

반대로, 원본 Pipeline Lab에는 있지만 Team-IZ 원본엔 없는 것(그대로 유지):
- **채점 결과 숫자 점수 즉시 노출**: Team-IZ 원본은 점수를 절대 노출하지 않지만, 이 도구의 실제 사용자(파이프라인을 직접 테스트하는 팀원)는 채점이 맞게 됐는지 바로 확인해야 해서 유지(팀 프로젝트 컨텍스트에서의 명시적 요구사항).

## 알려진 동작 차이 (의도적, 문서화됨)

- **모델 실시간 전환 불가**: 원본은 인터뷰 도중에도 모델 선택을 바꾸면 다음 턴부터 적용됐지만, 이 포트는 세션 시작 시점에 모델이 고정됩니다(세션 시작 후 모델 선택기는 잠김). 드물게 쓰이는 동작이라 판단해 단순화했습니다 — `p03-engine.js`의 change #9 참고.
- **Google 로그인 리디렉트**: Supabase 프로젝트의 OAuth allow-list가 원래 배포 도메인(`popixoxipop-collab.github.io/.../docs/lab/`) 기준으로 설정되어 있습니다. 이 브랜치를 새 도메인(예: `team-iz.github.io/AI/...`)에 배포하면, allow-list에 새 경로가 추가되기 전까지 Google 로그인이 실패할 수 있습니다(Supabase 대시보드 설정 필요 — 코드로 고칠 수 있는 부분이 아닙니다). NVIDIA 키 기반 P02/P03 실행 자체는 로그인과 무관하게 동작하며, 로그인이 안 되면 DB 저장만 건너뜁니다(화면 표시는 정상).

## 원본과의 관계

원본 Pipeline Lab(`docs/lab/`)은 계속 `popixoxipop-collab/Code_reviewer_with_feedback`에서 유지보수됩니다. 이 브랜치는 원본의 D206 시점(commit `10246f3`) 스냅샷을 Team-IZ 스타일로 재구성한 것으로, 원본이 이후 업데이트되어도 자동으로 동기화되지 않습니다 — 다시 최신화하려면 이 문서의 "이식 방법론"과 동일하게, 원본 저장소의 새 커밋 diff를 이 브랜치의 대응 파일에 재적용하면 됩니다.
