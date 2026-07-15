# Team-IZ 스타일 검증 파이프라인 (`feature/verification-ui`)

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

원본 Pipeline Lab(`docs/lab/`)은 계속 `popixoxipop-collab/Code_reviewer_with_feedback`에서 유지보수됩니다. 이 브랜치는 그 시점(D176~D184)의 스냅샷을 Team-IZ 스타일로 재구성한 것으로, 원본이 이후 업데이트되어도 자동으로 동기화되지 않습니다.
