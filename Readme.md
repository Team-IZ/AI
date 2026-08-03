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
>
> **D208 (2026-07-21, 이 브랜치 자체 변경)**: P02/P03가 매 실행마다
> `raw.githubusercontent.com/popixoxipop-collab/Code_reviewer_with_feedback`에서 파이썬
> 원본(`cognition/`, `judgment/`, `feedback/`)을 실시간으로 가져오던 구조를 제거했습니다.
> 이제 그 31개 파일을 이 저장소(`feat/code_Q&A`)에 그대로 복사해두고,
> `shared/p02-engine.js`/`shared/p03-engine.js`의 `REPO_RAW_BASE`를 절대 URL 대신
> 상대경로(`"../"`)로 바꿔 이 저장소 자신에서 로드합니다 — 다른 팀 저장소가 사라지거나
> private로 바뀌거나 브랜치가 정리돼도 이 브랜치는 영향받지 않습니다. 대신 원본이
> `cognition/`/`judgment`/`feedback`을 고치면 이 사본은 자동으로 안 따라가므로, 필요하면
> 아래 "이식 방법론"과 같은 방식으로 수동으로 다시 가져와야 합니다. Playwright로 실제
> ZIP 제출 → Pyodide 스캔 전 과정을 구동하면서 `raw.githubusercontent.com`/
> `api.github.com`으로 나가는 요청이 0건임을 직접 확인했습니다(요청이 발생하면 실패하도록
> 차단 설정한 상태로 테스트).
>
> **D208 후속 (같은 날, 원본 팀 인프라 의존성도 제거)**: 코드뿐 아니라 이 branch가 기대는
> 백엔드 인프라(NVIDIA 프록시 Worker, Supabase DB)도 원본 팀(popixoxipop-collab) 소유였던
> 걸 확인하고 Team-IZ 자체 자원으로 옮겼습니다.
> - **Cloudflare Worker**: `worker/nvidia-proxy.js`(무수정)를 `team-iz-code-qna-proxy`라는
>   새 이름으로, 전용 KV 네임스페이스(`team_iz_code_qna_nvidia_jobs`)+전용 큐
>   (`team-iz-code-qna-jobs-queue`)로 독립 배포했습니다(`worker/wrangler.toml` 참고) —
>   원본 `nvidia-proxy`나 커리큘럼 매니저 작업의 `team-iz-nvidia-proxy`와 자원을 전혀
>   공유하지 않아, 한쪽 장애/설정 변경이 이쪽에 영향을 주지 않습니다. 배포 직후
>   `x-nvidia-api-key` 헤더 없는 요청이 정상적으로 401을 반환하는 것으로 라이브 확인.
> - **Supabase**: 커리큘럼 매니저 작업 때 이미 만들어둔 `team-iz-curriculum-manager`
>   프로젝트(ref `tjmviobhxplucuwoibaj`)를 재사용하기로 결정(사용자 선택) — 이 프로젝트에
>   이미 있던 `members` 테이블/정책/트리거는 그대로 두고, 이 branch가 필요로 하는
>   `runs`/`stage_events`/`artifacts`/`presets` 테이블 + RLS 정책만 추가 적용(멱등,
>   `supabase_schema.sql`과 동일 스키마). 적용 후 Management API로 5개 테이블 전부와
>   정책 개수(runs 3, stage_events 2, artifacts 2, presets 3, members 2)를 직접 조회해
>   확인.
> - `shared/config.js`의 `TEAM_SUPABASE_URL`/`TEAM_SUPABASE_ANON_KEY`/`DEFAULT_PROXY_URL`
>   세 값 모두 위 새 자원을 가리키도록 교체.
>
> **D213 (2026-07-22, Supabase 다시 교체 + 알려진 리스크 수용)**: D208에서 고른
> `team-iz-curriculum-manager`는 갓 만든 사실상 빈 프로젝트(멤버 2명)였음이 드러났습니다.
> `code-reviewer-pipeline-lab`(원본 popixoxipop-collab repo 자신의 Supabase 프로젝트)에
> 이 팀의 **실제** 사용 이력이 이미 있었습니다 — 실제 팀원 7명(사용자 확인,
> 개발/테스트 계정 아님), p02 176건, p03 30건, 그리고 `team-iz-curriculum-manager`엔
> 없던 편의 뷰(`p03_progress_view`, `p03_turns_view`, `runs_with_email` 등)까지. 두
> 프로젝트 다 같은 Supabase organization(같은 계정) 소속이라 D208의 "다른 팀 소유
> 인프라" 문제와는 결이 다릅니다 — `shared/config.js`의 `TEAM_SUPABASE_URL`/
> `TEAM_SUPABASE_ANON_KEY`만 재교체.
>
> **알려진 리스크 (수용, 사용자 확정)**: 백그라운드 보안 리뷰가 두 가지를 지적했고 둘 다
> 실측으로 확인된 진짜 리스크입니다 —
>   1. **cross-tenant-signup**: `code-reviewer-pipeline-lab`의 `disable_signup=False`,
>      도메인/allowlist 제한 없음 — code-qna URL을 아는 누구든 구글 로그인만으로
>      `members` row가 자동 생성되고(`on_auth_user_created` 트리거) RLS `read all`
>      정책으로 전체 이력을 읽을 수 있습니다. "우리 팀만 안다"는 사회적 합의일 뿐 실제
>      접근 제어가 아닙니다.
>   2. **trust-boundary/data-cotenancy**: 원본 Pipeline Lab 사용자와 code-qna 트레이니가
>      이제 완전히 같은 테이블·같은 `read all` 정책을 공유 — 서로 다른 두 앱의 사용자
>      데이터가 한 신뢰 경계 안에 섞입니다.
>   사용자가 명시적으로 "감수" 결정(우선순위: 로그인 정상 작동 > 리스크 제거) — 이후
>   더 강화하고 싶으면 Supabase Auth 설정에서 `disable_signup=true` + 이메일 도메인
>   allowlist 추가를 고려(단, 이건 원본 Pipeline Lab의 가입 흐름도 함께 바뀌므로 그쪽과
>   조율 필요).
>   **로그인 리디렉트**: `uri_allow_list`에 `team-iz.github.io` 관련 항목이 원래
>   없었는데, 확인 시점에 이미 `https://team-iz.github.io/AI/lab/**`가 추가돼 있었음
>   (동시 진행 중인 curriculum-manager 작업이 넣어둔 것으로 보이며, code-qna 경로도
>   함께 커버함) — 추가 조치 불필요, 라이브 값으로 직접 확인함.
>   **EXIT (2026-07-22 수정)**: 원래는 "`team-iz-curriculum-manager`(ref
>   `tjmviobhxplucuwoibaj`)로 `TEAM_SUPABASE_URL`/`TEAM_SUPABASE_ANON_KEY`만 되돌리면
>   된다"고 적었으나, 그 프로젝트는 사용자가 **의도적으로 삭제**했습니다(Management API
>   확인 — 프로젝트 목록에서 사라짐). 이 되돌리기 경로는 더 이상 존재하지 않습니다 —
>   코드/설정 어디에도 그 프로젝트를 참조하는 부분은 없었음을 확인했으니(grep 검증)
>   기능적으로 끊어진 건 없지만, 정말 되돌려야 한다면 `code-reviewer-pipeline-lab` 안에
>   code-qna 전용 스키마를 새로 파거나 완전히 새 프로젝트를 만들어야 함.

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
  code-locate.js      -- 근거 심볼 -> 파일/줄 위치 확정(D-ground1). MIN_SYMBOL_LEN = 9는 손으로
                         고른 값이 아니라 실측값입니다(D-ground1m, 파일 상단 주석에 코퍼스와 분포)
  p02-engine.js       -- p02-runner.js를 복사 후 DOM 접점만 훅으로 치환
  p03-engine.js       -- p03-runner.js를 복사 후 DOM 접점만 훅으로 치환(가장 정교한 이식 대상)
  session-state.js    -- 페이지 간 sessionStorage 핸드오프(신규 코드, 원본엔 없음)
prompt_manifest.json / webtool_driver.py  -- 원본에서 무수정 이식
  (참고: webtool_driver.py 헤더 주석의 "fetched at runtime from raw.githubusercontent.com"은
   원본 기준 서술입니다 -- 이 저장소에서는 D208 이후 shared/p02-engine.js·p03-engine.js의
   REPO_RAW_BASE = "../" 로 같은 저장소에서 상대경로로 읽습니다. 무수정 이식 원칙 + pages.yml의
   바이트 동일성 드리프트 검사 때문에 그 파일 자체는 고치지 않습니다.)
cognition/ judgment/ feedback/  -- D208: P02/P03가 Pyodide로 실행하는 실제 파이썬 원본
  cognition/two_tier_scan.py    -- 구조/판단 스캔
  judgment/*.py                 -- 5축 채점(score_findings), 격리 판정기(isolation_*),
                                   관용구 필터(idiom_filter), 중요도 랭킹(importance_rank), subrubric
  feedback/*.py                 -- 자기수정(Reflection) 턴의 신호 추출(reflection_signal/reflection_hook)
  각 .py 옆의 JSON이 실제 규칙 데이터입니다(코드가 아니라 여기를 고쳐야 판정이 바뀝니다):
    judgment/idioms/{c,cpp,java,javascript,python,swift}/idiom_patterns.json
    judgment/isolation_categories/*/patterns.json,  judgment/subrubric_weights/*/weights.json
    judgment/rank_weights/rank_weights.json,        feedback/reflection_patterns/*/patterns.json
  셋 다 원본에서 무수정 이식이며, shared/p02-engine.js·p03-engine.js가 raw.githubusercontent.com
  대신 이 저장소 자신에서(상대경로로) 읽어들입니다 -- 다른 팀 저장소에 대한 런타임 의존성 제거.
worker/               -- NVIDIA API용 Cloudflare Worker 프록시(API 키를 브라우저에 두지 않기 위함)
  nvidia-proxy.js / nvidia-proxy.test.js / wrangler.toml / package.json
  (wrangler.toml과 shared/config.js는 feat/poc_full과 **의도적으로** 갈라져 있습니다 --
   Worker 이름/KV namespace/큐/LANGSMITH_PROJECT 같은 배포 정체성을 담고 있어서. D-poc-worker)
tests/                -- 런타임 산출물이 아니므로 drift 검사 대상 밖에 둡니다
  js/     code-locate.test.js, p03-code-context.test.js  -- node --test tests/js/*.test.js
  python/ test_rank_after_tier_b_drop.py                 -- python3 -m pytest tests/python/ -q
docs/
  port-reference/     -- 이식 대조용 원본 p02-runner.js/p03-runner.js/app.js 스냅샷(2026-07-28, 동결).
                         왜 지우면 안 되는지는 그 디렉터리의 README.md 참조
.github/workflows/pages.yml
  -- 4개 브랜치를 하나의 사이트로 조립하는 배포 워크플로. feat/poc_full과의 바이트 동일성
     드리프트 검사도 여기 있습니다(cognition/ judgment/ shared/ worker/ +
     prompt_manifest.json/webtool_driver.py -- 이 경로들은 두 브랜치를 함께 고쳐야 합니다).
```

## 이식 방법론

원본 `p02-runner.js`/`p03-runner.js`는 로직과 DOM 렌더링이 한 파일에 섞여 있습니다. 이번 포팅은 "이해한 내용을 바탕으로 재작성"이 아니라 **원본을 통째로 복사한 뒤, DOM 접점만 훅 호출로 기계적으로 치환**하는 방식으로 진행했습니다(`p02-engine.js`/`p03-engine.js` 상단 주석에 각 파일의 정확한 변경 목록이 있습니다). `docs/port-reference/`의 원본과 diff하면 훅 치환으로 명시한 줄 외에는 100% 동일합니다.

`run()`은 이제 훅 객체를 받는 고차 함수입니다:
```js
P03Engine.run({ finding, codeContexts, model }, {
  onStatus, onProgress, onRunStart, onRunEnd,
  onQuestion, getAnswer, onAnswerRecorded, countdown,
});
```
이건 새 설계가 아니라, 원본 Python `feedback/turn_engine.py`의 `run_decision_point(..., answer_fn)` 시임을 그대로 복원한 것입니다(JS 포팅 과정에서 DOM Promise로 변형됐던 걸 원래 모양으로 되돌림).

## 검증

- **소스 diff**: `p02-engine.js`/`p03-engine.js`를 `docs/port-reference/`의 원본과 비교, 문서화된 변경 외 로직 차이 없음을 확인.
- **실제 E2E 실행**(Playwright + 실제 NVIDIA API 호출): ZIP 제출 → 실제 Pyodide 스캔 → finding 2건(direct-match 1건 + text-mention 1건, D179/D180 두 커넥터 경로 모두) → 검증 세션 자동 시작 → 4턴 전부 실제 질문 생성+답변 제출+실제 Pyodide 분류 → 5축 채점 → 결과 페이지 렌더링까지 전 과정 실제로 통과. GitHub URL 제출 경로도 별도로 확인(성공/실패 케이스 둘 다).
- 이 과정에서 실제 버그 3건을 발견·수정: session.html에 Pyodide 스크립트 태그 누락(분류기가 Pyodide를 쓰는데 "재스캔 없음"이라는 이유로 빠뜨렸음), `.hidden` 유틸리티 클래스가 어느 CSS에도 정의되지 않아 시각적으로 안 숨겨짐, 진행 체크리스트 아이콘이 상태 전환 시 색상만 바뀌고 글리프(✓/◔/•)는 안 바뀜.
- direct-navigation 폴백(세션 데이터 없이 session.html/result.html 직접 접근) 확인 완료.
- **D207 최신화 검증**: 모든 `shared/*.js`/`docs/port-reference/*.js` syntax check 통과. `trainee/session.html`+`trainee/result.html`을 대상으로 Playwright E2E 재실행(모킹된 NVIDIA 프록시+GitHub API, 실제 Pyodide 분류기) — 근거 코드 패널 좌우 스크롤(D198), Enter 제출 시 확인 팝업(D201), L2 질문 생성 시 실시간 `⚙ list_files 호출 중...` 버블 노출 및 근거 파일명 인용(D204/D205), 마지막 턴 이후 채점 스피너 오버레이(D203), 결과 리포트의 채팅 버블 문답 원문+턴수 배지(D202) 8개 항목 전부 확인.

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
- **Google 로그인 리디렉트**: D213 시점에 `code-reviewer-pipeline-lab`(현재 Supabase 대상)의 `uri_allow_list`에 `https://team-iz.github.io/AI/lab/**`가 이미 포함돼 있는 것을 확인했습니다 — 로그인은 정상 작동합니다. NVIDIA 키 기반 P02/P03 실행 자체는 로그인과 무관하게 동작하며, 로그인이 안 되면 DB 저장만 건너뜁니다(화면 표시는 정상).
- **Supabase cross-tenant 리스크 (D213, 의도적으로 수용됨)**: `code-reviewer-pipeline-lab`은 가입 제한이 없고(`disable_signup=False`) RLS가 `read all`이라, code-qna URL을 아는 누구나 로그인 한 번으로 원본 Pipeline Lab 사용자 이력을 포함한 전체 데이터를 읽을 수 있습니다. 위 D213 노트 참고 — 강화하려면 Supabase Auth의 가입 제한 설정이 별도로 필요합니다.

## 원본과의 관계

원본 Pipeline Lab(`docs/lab/`)은 계속 `popixoxipop-collab/Code_reviewer_with_feedback`에서 유지보수됩니다. 이 브랜치는 원본의 D206 시점(commit `10246f3`) 스냅샷을 Team-IZ 스타일로 재구성한 것으로, 원본이 이후 업데이트되어도 자동으로 동기화되지 않습니다 — 다시 최신화하려면 이 문서의 "이식 방법론"과 동일하게, 원본 저장소의 새 커밋 diff를 이 브랜치의 대응 파일에 재적용하면 됩니다.
