# code-importance-map (D1~D12 요약)

학생 제출 저장소에서 "어떤 파일이 실제로 중요한가"를 고르는 스테이지.
기존 알파벳순 트렁케이션이 78개 중 1개만 살아남기던 문제(원인: 첫 큰 파일이
문자 예산을 다 먹음)를 대체한다. `app/engines/codemap/` 아래 전부 있다.

## 구조 (Phase 0~6)

| 파일 | 역할 | 순수/불순 |
|---|---|---|
| `models.py` | 값 객체 전부(`@dataclass(frozen=True)`) | 순수 |
| `collect.py` | 저장소 걷기, 소스 파일 수집(.git/생성물/바이너리 제외, 심볼릭링크 탈출 거부) | **불순** -- 유일한 파일시스템 읽기 지점 |
| `graph.py` | 다국어 import 그래프(fan-in dedup) | 순수 |
| `rank.py` | Tier 1 결정론적 랭커 | 순수 |
| `weights.py` | `weights/codemap_weights.json` 파싱 | 순수(파일 읽기는 `__init__.py`가 함) |
| `shortlist.py` | 예산 안에서 rank 순서로 채우기 | 순수 |
| `ground.py` | Tier 2 원시 응답의 closed-vocabulary 검증 | 순수 |
| `crew.py` | Tier 2 재랭킹(2026-07-31부터 crewai 없이 `shared.llm.chat()` 직접 호출, D1) | **불순** -- 유일한 네트워크 지점 |
| `analysis_doc.py` | 코드 분석 문서 생성(p05-3) | **불순** -- `chat()` 경유 네트워크 |
| `diagram.py` | 구조도 생성(p05-4, 2026-07-31 신규, 데모) -- `agent_loop.run_tool_loop()`의 첫 실사용처, `mermaid_syntax_lookup` 도구 하나로 시연 | **불순** -- `chat()` 경유 네트워크 |
| `materialize.py` | GITHUB_URL clone / ZIP 해제 | **불순** -- 유일한 git/zipfile 지점 |
| `engine.py` | `AnalysisEngine` 프로토콜 구현체(조립) | 조립부 |
| `__init__.py` | composition root -- Tier1+Tier2 전체 조립 | 조립부 |
| `prompts/*.yaml` | 프롬프트 원본(스테이지 1개=파일 1개) | 데이터 |

## 프롬프트 고치는 법

1. `app/engines/codemap/prompts/*.yaml` 중 해당 스테이지 파일을 고친다
   (파일명·함수 위치는 각 YAML의 `function:` 필드에 있음 -- 예:
   `run_rerank_crew() -- app/engines/codemap/crew.py`).
2. `./tools/rebuild_codemap_manifest.sh`로 `app/prompt_manifest.json`을
   다시 생성하고 같이 커밋한다. 안 하면 CI의 `prompt-manifest` job이 막는다.
3. 프롬프트 문자열을 YAML 밖(코드 안)에 직접 쓰면 `tools/lint_llm_calls.py`의
   PROMPT001 규칙이 막는다 -- `app.engines.shared.prompts.load_stage/render`를
   거치지 않은 프롬프트는 커밋할 수 없다.

## 로컬에서 돌려보기 (네트워크 없이, Tier 1만)

```bash
python -m app.engines.codemap --repo <분석할 저장소 경로> --top 20
python -m app.engines.codemap --repo <경로> --json > result.json  # 전체 결과
```

## 가드 켜기

```bash
git config core.hooksPath .githooks   # 선택. 커밋 전 린터+시크릿가드 자동 실행
```

CI(`.github/workflows/ci.yml`)는 훅 설정과 무관하게 항상 강제한다:
`test`, `lint`(D3/D4 AST 검사 + D9 시크릿 가드), `prompt-manifest`(D3-2 드리프트).

## Tier 2(재랭킹)를 실제로 쓰려면

추가 설치가 필요 없다 -- `pip install -r requirements.txt`만으로 충분하다
(2026-07-31부터 crewai 의존성 없음, `crew.py` 모듈 docstring D1 참고).
`NVIDIA_API_KEY`는 `.env`에만 두고(`tools/check_no_secrets.py`가 커밋을 막는다),
`engine_mode=codemap`으로 설정하면 `get_analysis_engine()`이 이 엔진을 반환한다.

멀티라운드 도구 사용이 실제로 필요한 스테이지가 생기면
`app/engines/shared/agent_loop.py::run_tool_loop()`을 쓴다(모듈 docstring 참고).

## 더 읽을 것

- [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md) -- D8(호출 회계 단위), D10(README "6콜" 계약) 미결 상태
- [PARALLEL_RUN_CHECKLIST.md](./PARALLEL_RUN_CHECKLIST.md) -- 기존 Worker 은퇴 조건(D5)
