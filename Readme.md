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
  analysis.html   2단계: 코드 분석 문서 · 요구사항 P/F 판정 · 문제 3개 선정 · L1~L4+힌트 동결
  session.html    3단계: 문답 (문제당 최대 4레벨, 레벨당 힌트 최대 2회)
  report.html     4단계: 보고서 (문제×레벨 매트릭스 · 재시험 대상 · 교안 참조)

  scoring-config.js    ★ 축(L1~L4)×값(0~5) 루브릭 + 임계값(pass=3) + 힌트 상한(5/4/3) + 재시험 규칙
  prompt_manifest.json ★ p04 6개 스테이지 프롬프트/파라미터 (단일 소스)
  llm-stage.js         매니페스트 스테이지 1개 호출(fillTemplate -> chatJSON -> JSON 파싱) 공용 경로
  teaches-source.js    P01이 DB에 남긴 unit_map을 teach 목록으로 읽어옴(교안 분석 자체는 재구현 안 함)
  code-fragment.js     LLM이 지목한 {file,lines}를 실제 파일과 대조해 코드 파편 추출/검증
  question-guard.js    질문·힌트에 선택지가 섞이는 걸 정규식으로 탐지(실측 사고 재발 방지)
  hint-ladder.js       문제 1개의 L1~L4 질문+힌트 2단을 한 번에 생성해 동결(D4)
  requirements.js      요구사항 P/F 판정
  poc-engine.js         전체 오케스트레이션(2/3/4단계) -- 페이지는 DOM을 안 만지고 이 파일의 hooks만 받음
  poc-state.js          페이지 간 sessionStorage 핸드오프(teamiz_p04_* 키, code-qna와 분리)
  p04_schema.sql        ★ public.runs/presets의 pipeline CHECK 제약에 'p04' 추가(사람이 한 번 실행)

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

**D4**: 힌트는 문제 시작 시 4개 레벨 질문과 함께 한 번에 생성해 동결한다(답변을 본 뒤
생성하지 않음). graduated prompting(Campione & Brown, 1987) — 답변을 보고 힌트를 만들면
같은 실력의 두 학생이 다른 힌트를 받게 되어 "몇 번째 힌트에서 통과했는가"가 학생 차이가
아니라 생성 차이를 재게 된다.

**D-poc6/D-poc7**: 기존 P02 finding에는 라인 번호가 없어 코드 파편(파일+라인)을 만들 수
없다 — LLM이 분석 문서에서 스스로 `{file,lines}`를 지목하게 하고, `code-fragment.js`가
실제 파일과 대조해 무효면 버린다. 질문·힌트에 선택지가 섞이는 사고(사용자 실측: 보기 준
학생이 대안비교 5점 받음 — 대안을 제시한 게 아니라 고른 것)를 `question-guard.js`가
정규식으로 잡아 재생성시킨다.

**힌트 사다리** (레벨당 최대 2회, 소진 후 미달이면 그 문제 종료 → 다음 문제 L1):

| 단계 | 종류 | 자력 판정 | 점수 상한 |
|---|---|---|---|
| 0 | — | 자력 | 5 |
| 1 | 재진술(정보 추가 없음) | 자력 유지 | 4 |
| 2 | 재진술·난이도 하향 | 부분 자력 | 3 |

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

## 미적용 사항 (사람이 한 번 해야 함)

- **`app/p04_schema.sql`**: `public.runs`/`public.presets`의 `pipeline` CHECK 제약이
  `('p01','p02','p03')`로만 한정돼 있어(원본 `experiments/web_lab/supabase_schema.sql`),
  `'p04'`를 추가해야 이 PoC의 실행 기록이 DB에 남는다. 적용 전에도 화면 동작·채점·보고서는
  전부 정상 동작한다 — 저장만 실패하고 "DB 저장 실패(결과는 화면에 남아있음)"로 로그된다
  (이 저장소의 기존 best-effort 저장 관용과 동일).

## 검증

Playwright로 mocked LLM(system 프롬프트 문자열로 스테이지 식별) + 실제 Pyodide 스캔을 태워
전 과정을 실행 확인:
- 1단계: 교안 수동 입력(3개 제한) · 요구사항 행 추가/삭제 · GitHub/ZIP 토글 · 모델 카탈로그.
- 2단계: 실제 ZIP → 실제 `two_tier_scan.py`/`score_findings.py` 스캔 → 분석 문서(존재하지
  않는 파일을 지목한 decision_point가 실제로 "근거 무효"로 걸러짐 확인) → 요구사항 P/F →
  문제 3개(teach 중복 검증) → 질문·힌트 생성. 힌트 가드가 실제로 선택지 포함 응답을 잡아
  재생성시키는 것(재시도 성공)과, 계속 위반 시 `flagged`로 종료하는 것(3회 시도 후 정지)
  둘 다 확인.
- 3단계: 사용자가 원 스펙에 준 예시(`4,0/4,1/3,0/2,2` `3,0/2,2/X/X` `2,2/X/X/X`)를 그대로
  재현하는 raw score 시퀀스를 주입해, 저장된 세션 결과가 그 예시와 **정확히 일치**함을 확인.
- 4단계: 매트릭스·재시험 배지(3번만 표시)·잘한점/부족한점/교안참조·요구사항 P/F 렌더 확인.
- 드리프트 검사: vendored 파일을 일부러 1줄 고쳐 `diff -r`가 실제로 잡는 것 확인 후 되돌림.
