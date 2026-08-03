# 병행 운영 체크리스트 (D5)

기존 Worker(알파벳순 트렁케이션, `feat/poc_full`)와 이 브랜치의 FastAPI
codemap 엔진을 동시에 운영하는 동안, **다섯 항목이 전부 Pass여야 기존
Worker를 은퇴시킨다.** 하나라도 Fail로 바뀌면 전부 다시 열린다 — narrative로
"됐다"고 적는 게 아니라, 각 행이 명령/로그로 증명돼야 Pass다.

## Retirement gate

> 🔴 **아래 다섯 행이 같은 커밋에서 동시에 Pass일 때만 기존 Worker를 제거한다.**

| # | 항목 | Pass 조건 | Fail 조건 | 증거 |
|---|---|---|---|---|
| **PR-1** | 핵심 골든 테스트 동일 | `pytest tests/test_codemap_golden.py`가 전체 픽스처에서 green, 각 픽스처의 `must_include` 전부 포함 + `must_exclude` 전부 미포함 | `must_include` 누락 또는 `must_exclude` 등장 | CI 실행 URL + `tests/test_codemap_golden.py` |
| **PR-2** | 분석 결과 구조 계약 동일 | `pytest tests/test_codemap_engine.py`가 green, `AnalysisResult.model_validate()` 통과, `openapi.json` 재생성 시 byte-identical | 스키마 검증 실패 또는 `openapi.json` diff 발생 | `git diff --exit-code openapi.json` |
| **PR-3** | FastAPI 경로가 운영 세션 기준 충족 | 실제 운영 분석 세션 ≥20건에서 p99(`completed_at - started_at`) ≤ **X초**, job `FAILED` 비율 ≤ **Y%** | 둘 중 하나라도 초과, 또는 세션이 20건 미만 | `docs/code-importance-map/measurements/*.jsonl` |
| **PR-4** | 기존 Worker 잔여 트래픽 | 기존 Worker 요청 수가 30일 연속 0(또는 헬스체크 귀속 ≤1건/일) | 어느 하루라도 기준 초과 시 30일 카운터 리셋 | Cloudflare Analytics(`team-iz-poc-proxy`) 날짜별 표 |
| **PR-5** | 두 시스템 사이 프롬프트 드리프트 | `tools/check_prompt_drift.py`로 지정한 공유 파이프라인이 전부 일치 | 드리프트 발견, 또는 검사가 이유 기록 없이 스킵/삭제됨 | CI 실행 URL |

## X/Y는 아직 실측값이 아니다

이 서비스에는 아직 메트릭 저장소가 없다(`jobs.py`가 인메모리, `README.md:88`).
그래서 PR-3의 X(지연 상한)/Y(실패율 상한)를 오늘 정직하게 확정할 수 없다.

**측정 절차 (파일럿 기간에 실행):**
1. `GET /analyses/{jobId}`의 최종(terminal) 폴링 응답마다 `status`,
   `started_at`, `completed_at`, `failure_reason`, 그리고 `ai_usage[]`의
   각 항목에서 `source_type`/`status`/`failure_code`/`latency_ms`를
   `docs/code-importance-map/measurements/<날짜>.jsonl`에 한 줄씩 append.
   (`app/jobs.py::_log_measurement()`가 로컬/컨테이너 stdout에 이미 이 모양으로
   `calls: [{source_type, status, failure_code, latency_ms}, ...]`을 자동 기록한다 --
   D-pr3b, 2026-08-03. 수동 하베스트는 이 자동 기록과 같은 필드셋을 맞추면 된다.)
2. 20건이 쌓이면 p99 지연과 FAILED 비율을 계산해 이 문서의 X/Y를 날짜와 함께 갱신.
   스테이지별(`source_type`) 실패가 특정 단계(예: DIAGRAM)에 쏠리면 Y를 전체
   job 실패율 하나로 뭉개지 말고 스테이지별로도 병기할 것.

**논쟁용 가안(채택 아님, 반증 대상):** X=300초(기존 Worker 자신의 문서화된
600초/attempt 예산의 절반), Y=2%. **이 숫자로 기존 Worker를 은퇴시키지 말 것** —
실측 전까지는 PR-3을 항상 Fail로 취급한다.

## PR-5 상세 — 지금은 비교 대상이 없다

`tools/check_prompt_drift.py`는 두 `prompt_manifest.json`에서 지정한 파이프라인
키의 canonical-JSON 해시를 비교하는 범용 도구로 이미 만들어져 테스트도 통과했다
(`tests/test_check_prompt_drift.py`).

다만 **이 브랜치(`feature/code-importance-map`)와 `feat/poc_full` 사이에는
현재 실제로 공유되는 파이프라인이 없다** — 이 브랜치의 `app/prompt_manifest.json`은
`p05`(codemap 전용, 이 브랜치가 새로 만든 것)만 있고, `feat/poc_full`의
매니페스트는 `p04`(그 브랜치의 PoC 전용)만 있다. 서로 다른 걸 다루므로
"드리프트"라는 개념 자체가 아직 적용되지 않는다 — 그래서 CI에 cross-branch
비교 job을 아직 추가하지 않았다(추가했다면 매번 "한쪽에만 없음"으로 항상
실패하는, 실제 드리프트와 무관한 가짜 실패였을 것).

**언제 이 도구를 CI에 연결하는가:** FastAPI 쪽이 `feat/poc_full`의 P04
스테이지(분석 문서, 질문 생성 등)와 내용상 대응하는 파이프라인을 갖추게 되면,
그 시점에 `.github/workflows/ci.yml`에 `feat/poc_full`을 `src-poc` 경로로
체크아웃하는 job을 추가하고 `check_prompt_drift.py --pipeline <그 이름>`을
호출한다(`pages.yml`의 체크아웃 패턴 그대로 재사용).

**의도적으로 갈라서기로 한 경우(향후):** `--pipeline` 목록에서 빼고, 그
이유를 이 문서에 날짜와 함께 적을 것 — `pages.yml`의 "드리프트 체크 제외"
관행과 동일.

## 지금 실제로 동작하는 드리프트 방어 (in-branch)

PR-5의 cross-branch 절반은 위 이유로 보류지만, **같은 브랜치 안에서의**
드리프트(YAML 프롬프트 원본 vs 커밋된 `app/prompt_manifest.json`)는 이미
실제로 CI가 막고 있다: `.github/workflows/ci.yml`의 `prompt-manifest` job이
`./tools/rebuild_codemap_manifest.sh --check`를 매 push마다 돈다(D3-2).
