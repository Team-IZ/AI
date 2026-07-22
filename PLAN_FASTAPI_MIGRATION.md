# AI 파트 FastAPI 전환 계획서 (ai-dev 서브 에이전트용)

> 작성일: 2026-07-20 (v2 — 세션 구조 확정 반영). 이 문서는 ai-dev 서브 에이전트가 작업 시 기준으로 삼는 계획서다.
> 작업 브랜치: `feature/fastapi-migration` (`origin/feature/fastapi-migration` 추적, 로컬이 2커밋 앞섬)
> 전제 지식: `AI/README.md`(구조·실행법), `../docs/AI-Backend_API_명세서_v0.1.md`(내용은 v0.2, API 계약), `../docs/docs_for_read/P02_코드분석_서비스플로우.md`, `P03_소크라틱_검증세션_서비스플로우.md`, `기능명세서.md`.

---

## 현재 상태 (마지막 갱신: 2026-07-22)

**이 문서가 AI 파트 진행 상황의 단일 기준이다.** 새 세션을 시작하면 여기부터 읽어라.
**마지막 확인 테스트: 47 passed · 1 skipped** (`./.venv/Scripts/python.exe -m pytest tests/ -q`). skip 1건은 네트워크에 의존하는 GitHub clone E2E다.

> PC 이전(노트북 `C:\KT_aivle\big-project`, Python 3.12 → 현재 `D:\KT_AIVLE\BigProject\big-project`, Python 3.13)으로 `.venv`가 옛 경로를 가리켜 깨졌었다. **2026-07-22에 `C:\Python313` 기준으로 재생성 완료**, 47 passed·1 skipped 재확인. 다시 깨지면 `rm -rf .venv && /c/Python313/python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`.

### 완료

| 항목 | 결과 |
|---|---|
| Phase 0 — 파이프라인 내재화 | 팀원 레포(`popixoxipop-collab/Code_reviewer_with_feedback`, main `9bea5fc`)의 분석 소스 41개를 `pipeline/`에 무수정 복사. Pyodide 런타임 fetch 의존 제거. 상세: `pipeline/VENDORED.md` |
| Phase 1 — FastAPI 골격 | `app/`(api·core·storage 레이어), 듀얼 모드(APP_MODE), 공유 API 키 인증(B1), B6 운영 파라미터, 테스트 15건(14 passed·1 xfailed) |
| API 명세 1차 확정 | `../docs/AI-Backend_API_명세서_v0.1.md` — B1~B7 결정 반영, 엔드포인트 9개 정의 |
| 커밋·push | `feature/fastapi-migration` 브랜치에 3커밋(`dbb45c4` vendor / `899f9c9` skeleton / `c27eb90` docs) → `Team-IZ/AI` 원격 |
| Phase 2a — P02 분석 API | `POST /api/v1/analyses`(JSON/multipart 분기) + `GET /api/v1/analyses/{job_id}`, `app/core/`(collect·attribution·findings·analysis_job), 명세 §3.2 응답 구조 |
| Phase 2b — submission.html 연결 | 목업 제출 페이지가 FastAPI 분석 API를 호출. 브라우저 Pyodide 경로(`shared/p02-engine.js`·`webtool_driver.py`) **삭제**. `session.html`·`result.html`에 미연결 배너 추가(당시 테스트 36 passed·1 skipped) |
| S1 — 코드 업로드 마무리 (`9c14049`) | 분석 결과에 `snapshot_id`(UUID, `job_id`와 별개)와 `snapshot_meta`(`content_hash` sha256 64자 / `file_count` / `byte_count`) 반환. 코드 원문 무저장 원칙(API 명세 §3.3)은 유지하고 메타만 제공해 Spring이 `code_snapshot` 행을 만들 수 있게 하는 설계. 메타는 **스코프 필터 적용 후 실제 분석된 파일 집합** 기준으로 산출 |
| `run_integrated.ps1` (`58d3e18`) | integrated 모드(백엔드 연동) 실행 스크립트 추가. 로컬 `feat/fastapi-migration`에만 있고 원격 미push |
| standalone 루트 리다이렉트 (`e18b6b3`) | `GET /`가 `/submission.html`로 307 임시 리다이렉트(`trainee/`에 `index.html`이 없어 404 나던 문제). OpenAPI 스키마 미노출, integrated 모드에는 미등록 |

현재 구현된 엔드포인트는 `GET /api/health`, `POST /api/v1/analyses`, `GET /api/v1/analyses/{job_id}` 3개(+ 위 standalone 루트 리다이렉트)다.

### 작업 단위 재분할 (S1~S4)

기존 Phase 3/4/5 구분 위에 **서비스 흐름 단위(S1~S4) 재분할**이 도입됐다.

```
S1 코드 업로드(완료) → S2 중요도 분석 → S3 문답 → S4 결과보고서
```

| 단위 | 대응 | 상태 |
|---|---|---|
| S1 코드 업로드 | Phase 2 + `snapshot_id`/`snapshot_meta` 보강 | ✅ 완료 |
| S2 중요도 분석 | (신규) `dp_id` 발급, `commit_attribution` 결손 필드(git log 확장), 중요도 산출부 격리 어댑터, `focus_areas` 실전달 | ← **다음 작업** |
| S3 문답 | 기존 Phase 3(세션 API) — §3 | 대기 |
| S4 결과보고서 | 기존 Phase 4(5축 후채점) — §3 | 대기 |
| — | Phase 5(저장 계층·전체 E2E·계약 확정)는 그대로 유지 | 대기 |

**실행 계획(단계별 DoD·엔드포인트·DB 컬럼 대응·테스트 계획)은 `../output_docs/FastAPI화_상세계획.md`가 기준이다.** 이 문서에 그 상세를 복제하지 않는다 — 이 문서는 Phase 단위 진행 상황과 AI 저장소 내부 맥락에 집중한다.

### 진행 중 / 다음 할 일

**S2 — 중요도 분석 보강** ← 다음 작업. 상세는 `../output_docs/FastAPI화_상세계획.md` §4.

### 알려진 문제

1. ~~standalone 목업의 제출 흐름 비동작~~ ✅ **해소(Phase 2b)**. Pyodide 경로를 삭제하고 FastAPI API 호출로 교체. `tests/test_app_modes.py::test_standalone_mockup_submission_flow_works`가 동작을 고정한다(기존 strict xfail은 삭제).
2. ~~"미연결 페이지 안내 문구" 미이행~~ ✅ **해소(Phase 2b)**. `session.html`·`result.html` 상단에 `.phase-notice` 배너 추가(`shared/iz-tokens.css`).
3. **`storage/base.py`가 명세와 3곳 불일치** — `save_findings` 시그니처가 §3.2 응답 구조와 다름, `save_grades`가 `session_id` 기준(§5.1은 `score_run_id`), `ai_usage` 저장 경로 없음. Supabase 스키마가 미결이라 Phase 5까지 보류.
4. **분석 결과가 인메모리 job에만 있다.** standalone도 NullStore 폴백이라 서버 재시작 시 유실된다(Phase 5에서 supabase_store). 목업은 `sessionStorage`(`SessionState`)에 finding basket을 따로 들고 있어 뒤로가기 복원만 가능하다.
5. **PARTIAL 상태의 정의가 백엔드 미확인.** 현재는 "OWN_COMMIT→TOTAL 폴백"에만 부여한다(§3.2에 조건이 열거돼 있지 않음).
6. **목업에 `extraction_scope`/`commit_email` 입력 UI가 없다.** 페이지는 항상 `TOTAL`·`question_budget:4`로 보낸다. OWN_COMMIT 경로는 API·테스트로만 검증돼 있다.

### 브랜치 전략 (팀 결정)

팀 합의 순서는 `feature/*` → (동작·테스트 완료 후) `main` → `main` 기준으로 `develop` 생성 → 이후 수정은 `develop`에서 테스트 → `main` 병합이다.

실제 경위는 이와 순서가 다르다: `feature/fastapi-migration`이 먼저 만들어졌고, `develop`은 2026-07-21에 뒤늦게 생성됐다(`e121ce7`). `develop`에는 임시 README와 `.gitignore`만 들어 있는 사실상 빈 브랜치다.

**현재는 `feature/fastapi-migration`을 `develop`에 병합하려는 시점**이며, 이때 `develop`의 임시 README·`.gitignore` 대신 feature 쪽 버전을 채택한다는 방침이다.

**로컬 브랜치 현황(2026-07-22, PC 이전 후 정리)**
- 규칙과 어긋난 로컬 이름 `feat/fastapi-migration`을 협업 규칙(`../rule/개발/이슈 O/협업 규칙 요약.docx`, 네이밍 `feature/기능명`)에 맞춰 **`feature/fastapi-migration`으로 rename 완료**. 중복이던 구 로컬 `feature/fastapi-migration`(`578ef3f`)은 rename 대상의 조상이라 커밋 손실 없이 삭제.
- 현재 `origin/feature/fastapi-migration` 추적, **로컬이 2커밋 앞섬**(`cc45f37` PR#1 머지, `58d3e18` run_integrated.ps1) — 아직 push 안 됨.
- 원격(`Team-IZ/AI`)에 `feat/pdf_analysis`가 별도로 존재하나 이 작업과 무관.

---

## 0. 확정된 설계 결정 (2026-07-20)

- **세션 턴 구조: L1 → L2 → L3 (Reflection 턴 제거).** Decision Point(DP)별로 depth 3단 대화를 진행한다.
- **5축은 문항 구조가 아니라 후채점 루브릭이다.** 문항을 5축으로 쪼개지 않는다. 5축(코드이해·설계논리·대안비교·반례대응·자기수정, 25점 만점·축당 동일가중 5점)은 **세션 종료 후 전사(transcript) 전체를 대상으로 채점**한다.
- 목업의 per-턴 Reflection 분류(`reflection_signal.py` 등)는 세션 진행 게이트에서 빠진다. 자기수정 축 채점에 재활용할지는 미결(§5).
- 코드 접근 방식: 처음부터 새로 작성하지 않고 **팀원의 분석 레포를 받아와 그 코드를 기반으로 수정**한다.

## 1. 목표

현재 목업은 브라우저 안에서 모든 것을 처리한다(JS 오케스트레이션 + Pyodide로 Python 스캔 실행 + 브라우저→프록시→NVIDIA LLM 호출 + sessionStorage 상태 관리). 이를 다음과 같이 재편한다:

- **JS(HTML 페이지)는 UI 뼈대만 남긴다.** 화면 골격은 추후 Frontend(React) 파트가 가져간다 — 팀 확인 완료.
- **데이터 처리·통신은 전부 Python(FastAPI) 서버로 이동한다**: 코드 제출/분석(P02), AI 문답 세션(P03), 결과 보고서 생성.
- FastAPI 서비스는 Spring Boot 백엔드와 통신하는 것을 전제로 설계한다.

## 1.5 운영 모드 설계 (2026-07-20 확정)

FastAPI 서비스는 **동일한 API 코어**를 두 가지 모드로 구동한다. 모드는 환경변수(`APP_MODE=standalone | integrated`)로 전환하며, **엔드포인트·요청/응답 스키마는 두 모드에서 완전히 동일**해야 한다 — 달라지는 건 "누가 호출하는가"와 "확정 데이터를 어디에 영속화하는가" 뿐이다.

### 모드 A — standalone (AI 파트 단독 테스트)

```
팀원 뼈대 프론트(trainee/*.html, 정적 서빙) ←→ FastAPI ←→ Supabase (백엔드 대체)
                                              └→ NVIDIA LLM
```

- 목적: Spring Boot 없이 AI 레포만으로 제출→분석→문답→후채점→리포트 전체 흐름을 명령어 하나로 테스트.
- 프론트: 기존 목업 페이지(`trainee/`)를 FastAPI가 정적 파일로 함께 서빙. 페이지의 데이터 호출부만 FastAPI API로 교체(Pyodide·프록시 경로 제거).
- 저장: **Supabase가 Spring Boot의 대역**. 확정 데이터(finding, transcript, 채점)를 Supabase adapter가 저장. 기존 `db.js`의 테이블 구조·`supabase_schema.sql`(원본 레포)을 참고.
- 실행 명령(목표): `uvicorn app.main:app` + `APP_MODE=standalone` (또는 `run_standalone.ps1`/`Makefile` 타깃 하나로 래핑).

### 모드 B — integrated (전체 팀 통합)

```
React (Frontend/) ←→ Spring Boot (Backend/) ←→ FastAPI
                                                └→ NVIDIA LLM
```

- FastAPI는 **저장하지 않는다**. 모든 확정 데이터는 응답(또는 완료 콜백)으로 Spring Boot에 반환하고, 영속화·테넌트 격리·감사 로그는 Spring이 전담 (2026-07-20 결정 — DB 단일 소유자는 Spring).
- Supabase adapter는 비활성화. React가 FastAPI를 직접 호출하는 경로는 없다.
- FastAPI 응답에는 모델명·토큰 사용량을 포함해 Spring의 `ai_usage` 원장 기록을 지원한다.

### 구현 원칙 — 저장소 어댑터 분리

```
app/
├── api/          # 라우터 (두 모드 공통, 스키마 동일)
├── core/         # pipeline/ 호출 서비스 레이어 (두 모드 공통)
├── storage/      # ResultStore 인터페이스
│   ├── supabase_store.py   # standalone 전용
│   └── null_store.py       # integrated: 저장 안 함(응답으로만 반환)
└── main.py       # APP_MODE에 따라 adapter 주입 + standalone이면 trainee/ 정적 서빙
```

- 진행 중 세션의 턴 상태 같은 휘발성 작업 메모리는 모드와 무관하게 FastAPI 로컬(인메모리)에 둔다. 영속 기록만 adapter를 거친다.
- 통합 시점에 코드 변경 없이 `APP_MODE=integrated`로만 전환되는 것이 이 설계의 합격 기준이다.

## 2. 전환 대상 매핑 (현재 → 목표)

| 현재 (브라우저) | 목표 (FastAPI 서버) |
|---|---|
| `p02-engine.js`: GitHub/ZIP 수집, Pyodide 부트스트랩, 외부 레포에서 파이프라인 소스 runtime fetch | `POST /api/submissions` — 서버가 ZIP 해제/GitHub clone 후 **로컬에 내재화한** 파이프라인 소스를 직접 import·실행 |
| `webtool_driver.py`: 오버라이드 적용 + `scan()`/`score()` 호출 | 서비스 레이어의 원형. 거의 그대로 재사용 |
| `p03-engine.js`: 턴 루프 오케스트레이션, Pyodide 분류기 | 세션 API(DP별 L1→L2→L3, Reflection 없음). 원본 레포의 `feedback/turn_engine.py`가 Python 원형이므로 그것을 base로 수정 |
| 목업의 per-답변 5축 채점(`grade_interview_answer`) | **세션 종료 후 transcript 전체 대상 5축 후채점**으로 교체 (§0 확정 결정) |
| `llm.js`: 프록시 경유 submit-and-poll NVIDIA 호출 | 서버에서 NVIDIA API 직접 호출. 단 **긴 지연(92s+ 사례) 교훈은 계승** — 동기 응답 대신 비동기 작업(job) 패턴 |
| `session-state.js`: sessionStorage 핸드오프 | 서버 측 세션 상태(초기: 인메모리/SQLite, 추후 Spring Boot와 DB 소유권 협의) |
| `config.js`: 브라우저에 API 키 입력 | 서버 환경변수(`.env`, gitignore 필수). 키를 클라이언트에 절대 노출하지 않음 |
| `db.js`: Supabase 저장 | `storage/supabase_store.py`(standalone 모드 전용 adapter)로 이관. integrated 모드에선 저장 없이 응답으로 Spring에 반환 (§1.5) |

## 3. 작업 단계

### Phase 0 — 팀원 분석 레포 확보·내재화 (선행 필수)
1. **팀원의 분석 레포를 clone해 받아온다** (레포 주소·브랜치는 사용자 확인 후 — §5). 이 코드가 수정의 출발점(base)이다. 새로 작성하지 않는다.
2. 분석 파이프라인 소스를 `AI/pipeline/` 아래로 내재화. 경로 구조 유지 필수 — 모듈들이 `os.path.dirname(__file__)` 기준으로 형제 파일을 찾는다. 참고: 현재 목업이 runtime fetch하는 파일 목록은 `p02-engine.js:43-67`(P02용 23개), `p03-engine.js:71-84`(P03용 12개)에 있으며, 받아온 레포 구조와 대조해 필요분을 확정한다.
   - ✅ **완료(2026-07-20)**: 커밋 `9bea5fc` 기준 40개 파일 vendoring, import 검증 통과. 상세는 `AI/pipeline/VENDORED.md`. 주의: `evidence_bridge.py`는 원본 레포에서 `feedback/`이 아니라 `pipeline/`에 있음(→ `AI/pipeline/pipeline/evidence_bridge.py`). `timeout_config.py`, `nvidia_key_pool.py`, `generate_questions.py`, `interview_rubric.py`가 import 체인상 추가로 필요했음.
   - Python 오케스트레이션 원본(`feedback/turn_engine.py`, `feedback/nvidia_client.py`, `feedback/evidence_bridge.py`, 프롬프트 파일)이 레포에 있으면 그것을 우선 사용.
3. 내재화 후 출처 레포·커밋 SHA를 `AI/pipeline/VENDORED.md`에 기록 (추후 원본과의 diff 추적용).

### Phase 1 — FastAPI 골격 ✅ 완료(2026-07-20)
> `app/`(api/core/storage 레이어), `requirements.txt`, `.env.example`, `run_standalone.ps1`, `tests/test_pipeline_smoke.py` 생성. 양 모드 health 200, pytest 3건 통과, Pyodide 없이 scan→score 완주 확인. standalone의 supabase_store는 Phase 5 범위라 현재 NullStore 폴백.
1. `AI/app/` 구조로 FastAPI 프로젝트 스캐폴드 (`pyproject.toml` 또는 `requirements.txt`, uvicorn). §1.5의 `api/core/storage` 레이어 구조를 처음부터 적용.
2. 설정: `.env` 기반 (`APP_MODE`, NVIDIA API 키, Supabase URL/키(standalone용), 모델명, CORS 허용 오리진). `.env.example` 제공, `.env`는 gitignore.
3. `ResultStore` 인터페이스 + `null_store` 먼저 구현 (supabase_store는 Phase 5에서).
4. 스모크 테스트: 내재화한 `two_tier_scan.scan()` + `score_findings.score()`를 로컬 샘플 레포에 직접 실행해 목업과 동일한 finding이 나오는지 확인 (Pyodide 없이 동작 검증).

> ★ **Phase 2~4 공통 원칙 (2026-07-20 결정)**: 각 Phase는 **API 엔드포인트와 목업 페이지 연결을 함께** 내놓는다. 목업 연결을 Phase 5에 몰면 그동안 standalone이 죽어 있고, 더 나쁘게는 목업이 브라우저 Pyodide로 도는 동안 정작 통합에 쓸 서버 코드가 검증되지 않는다. 목업이 FastAPI를 호출하게 만들어야 **standalone 테스트 = 통합 시 Spring이 호출할 그 코드 경로 검증**이 성립한다.
> - 각 Phase 완료 시점에 `.\run_standalone.ps1` 하나로 그 시점까지의 기능을 실제 사용 가능해야 한다.
> - 연결이 끝난 페이지에서는 해당 Pyodide·프록시 코드를 **삭제**한다(두 구현 병존 금지).
> - 아직 연결 안 된 페이지는 "이 단계는 아직 미연결" 안내를 표시한다 — 반쯤 동작하며 조용히 이상해지는 것보다 낫다.
> - 최종 통합 후에도 목업은 계속 살아 있다: `APP_MODE` 전환만으로 standalone/integrated를 오간다.

### Phase 2 (= S1) — P02 분석 API + submission.html 연결 ✅ 완료(2026-07-20, S1 보강 2026-07-21)
> 2a: API·서비스 레이어. 2b: 목업 연결 + Pyodide 경로 삭제 + 미연결 배너.
> 목업 폴링 주기는 명세 B6의 3초 대신 **1초** — B6의 3초는 "Spring이 네트워크 너머로 폴링"하는
> 값이고, 목업은 같은 오리진·같은 프로세스의 인메모리 job을 폴링하며 실제 분석이 1~2초에
> 끝나기 때문이다. **서버 코드 경로는 동일하다**(바뀌는 건 목업 페이지의 폴링 빈도뿐).
- `POST /api/v1/analyses` (job 패턴, 202 + callback_url) / `GET /api/v1/analyses/{job_id}` — 명세서 §3 기준.
- 파일 수집 규칙은 `p02-engine.js`의 로직(SRC_EXTS, SKIP_DIR_NAMES, .ipynb 셀 추출)을 Python으로 이관.
- **공개 레포만 지원(B5)** — PAT 없음. ZIP은 무저장 multipart 중계(§3.3).
- 응답에 finding별 `code_context` 발췌·`commit_sha` 포함(§3.3 파편 저장 원칙).
- **목업 연결**: `submission.html`이 이 API를 호출하도록 교체. Pyodide 부트스트랩·`webtool_driver.py` fetch 경로 제거(→ 이때 `webtool_driver.py` 파일도 삭제 가능해짐).

### Phase 3 (= S3) — P03 세션 API + session.html 연결 ★확정 구조 반영
- `POST /api/v1/sessions`, `POST /api/v1/sessions/{id}/answers`, `GET /api/v1/sessions/{id}`, `POST /api/v1/sessions/{id}/restore` — 명세서 §4 기준.
- **턴 규칙(확정): DP별 L1 → L2 → L3 depth 3단. Reflection 턴 없음.** 답변이 견고해도 최소 1회 L2 후속 질문(ENG 소크라틱 평가 계약).
- **세션 중에는 채점하지 않는다.** 세션 중 LLM 역할은 질문 생성뿐.
- 매 응답에 확정 턴 기록 포함(§4.5 순서 원칙), `client_request_id` 멱등 처리.
- LLM 호출은 NVIDIA 직접(프록시 제거), 키 로테이션은 `nvidia_key_pool.py` 활용 검토.
- **목업 연결**: `session.html`이 이 API를 호출하도록 교체. 브라우저 Pyodide 분류기·프록시 경로 제거.

### Phase 4 (= S4) — 후채점 API + result.html 연결 ★확정 구조 반영
- `POST /api/v1/gradings` (job) / `GET /api/v1/gradings/{job_id}` — 명세서 §5 기준.
- **5축 채점은 세션 종료 후 transcript 전체 대상 1회.** 축별 1~5점·동일가중, 총 5~25점, 축별 인용 근거 + 모델·프롬프트·루브릭 버전 필수.
- 목업의 per-답변 `grade_interview_answer`를 **transcript 단위 후채점**으로 교체 — 판정 로직이 목업과 달라지는 유일한 지점이므로 프롬프트·스키마 신규 설계 후 사용자 검토.
- 등급(소유/표면/미흡) 매핑은 FastAPI가 하지 않는다(Spring/RPT 소관).
- **목업 연결**: `result.html`이 이 API 결과를 표시하도록 교체.

### Phase 5 — standalone 저장 계층 + 전체 E2E + 계약 확정
1. `supabase_store.py` 구현 — standalone에서 Supabase가 Spring 대역으로 확정 데이터(파편) 저장. 스키마는 미결 #12.
2. **전체 E2E**: 제출→분석→문답→후채점→리포트를 standalone에서 통과.
3. `storage/base.py` 인터페이스를 명세 응답 구조에 맞게 확정(현재 3곳 불일치 — Phase 1 재검토에서 보고됨).
4. API 명세 최종본 확정 + 에러 코드 전체 목록(B7) 작성 → 백엔드와 합의.
5. integrated 모드 전환 검증: 코드 변경 없이 `APP_MODE=integrated`로만 전환되는지(§1.5 합격 기준).

## 4. 제약·주의사항

- **폴더 경계**: 작업은 전부 `AI/` 안에서만. Spring Boot 통신 설계를 위해 `Backend/`를 읽는 건 허용, 수정 금지.
- **비밀키**: NVIDIA 키를 코드·커밋에 절대 포함하지 않는다. `../docs/env.xlsx` 값을 옮겨 쓰지 말 것.
- **판정 로직 변경 범위(1차)**: P02 스캔·판단 필터는 받아온 소스를 수정 없이 그대로 실행한다(목업이 E2E 검증한 기준 동작). **의도된 변경은 P03 세션 구조(Reflection 턴 제거)와 채점 방식(후채점 루브릭 전환) 두 가지뿐** — 그 외 로직은 건드리지 않는다.
- **docs는 유동적**: 명세서와 충돌하면 맹신하지 말고 실제 목업 동작·최근 결정을 우선, 애매하면 사용자에게 확인.

## 5. 미결 사항 (사용자/팀 확인 필요)

착수 차단(Phase 0 전 필요):

| # | 항목 | 내용 |
|---|---|---|
| 1 | 팀원 분석 레포 | 레포 주소·브랜치·clone 받을 위치. 기존에 목업이 참조하던 `popixoxipop-collab/Code_reviewer_with_feedback`과 같은 레포인지, 다른 레포인지 확인 |

세션·채점 구조 확정에 필요(Phase 3~4 전):

| # | 항목 | 내용 |
|---|---|---|
| 2 | verdict/조기 종료 규칙 | 목업의 defended 조기 종료·verdict 4종(defended/surface/partial/exhausted)은 per-턴 분류 기반이었음. Reflection 턴 제거·후채점 전환 후에도 (a) DP별 조기 종료를 유지할지, (b) verdict 개념 자체를 유지할지 아니면 5축 점수·등급으로 대체할지 |
| 3 | per-턴 분류기 활용 | `isolation_classifier` 등 per-턴 분류를 세션 진행(다음 질문 난이도 조절)에 계속 쓸지, 완전히 제거할지 |
| 4 | Reflection 자산 처리 | `reflection_signal.py`·reflection 패턴 JSON을 후채점의 "자기수정" 축 근거 산출에 재활용할지, 폐기할지 |
| 5 | DP 수·세션 종료 조건 | 세션당 DP 개수(목업 기본 finding당 1세션), 전체 세션 종료 조건(모든 DP 소진?) |
| 6 | 등급 매핑 | 후채점 25점의 등급 구간 — 기능명세서(소유 20+/표면 12–19/미흡 ≤11)를 따를지, 재정의할지 |

아키텍처·운영:

| # | 항목 | 내용 |
|---|---|---|
| ~~7~~ | ~~통신 토폴로지~~ | ✅ **확정(2026-07-20)**: React→Spring→FastAPI (§1.5 모드 B). React가 FastAPI를 직접 호출하지 않음. 단 integrated 모드에서 Spring→FastAPI 요청의 인증 방식(내부망/API 키/토큰)은 backend 팀과 협의 필요 |
| ~~8~~ | ~~세션·결과 저장소~~ | ✅ **확정(2026-07-20)**: DB 단일 소유자는 Spring. FastAPI는 integrated 모드에서 저장하지 않고 응답/콜백으로 반환. standalone 테스트 모드에서만 Supabase를 백엔드 대역으로 사용 (§1.5) |
| 9 | GitHub PAT 취급 | 사용자 PAT를 서버로 전달받는 방식의 보안 정책 |
| 10 | 배포 형태 | 로컬 uvicorn / Docker / 클라우드 — CORS·프록시 설정에 영향 |
| 11 | 완료 통지 방식 | 오래 걸리는 작업(분석·후채점)의 결과 전달: Spring 폴링 vs FastAPI→Spring 콜백(멱등성 키 필요). Phase 5 계약 문서에서 확정 |
| 12 | standalone Supabase 스키마 | 원본 레포 `supabase_schema.sql`을 그대로 쓸지, 새 API 스키마에 맞게 재정의할지 |
