# VENDORED — 팀원 분석 파이프라인 내재화 기록

이 디렉터리의 파일은 아래 외부 레포에서 **수정 없이 그대로 복사(vendoring)** 한 것이다.
원본과의 diff 추적을 위해 이 파일을 유지한다. 파이프라인 소스 자체를 수정하지 말 것
(계획서 §4: P02 스캔·판단 로직은 목업이 E2E 검증한 기준 동작을 그대로 실행한다).

## 출처

- 레포: https://github.com/popixoxipop-collab/Code_reviewer_with_feedback
- 브랜치: main
- 커밋 SHA: `9bea5fc5c9a8e6bd530f32dfdae25ea0bc2b15a5`
- Vendoring 날짜: 2026-07-20
- 복사 방식: shallow clone 후 필요 파일만 선별 복사. `.git`, feedback_log.jsonl,
  벤치마크/실험/예제 산출물은 복사하지 않음.

## 디렉터리 구조 주의

원본 레포의 경로 구조를 그대로 유지했다. 모듈들이 `os.path.dirname(__file__)` 기준
상대 경로로 형제 파일·패턴 JSON을 찾기 때문에 구조를 바꾸면 안 된다. 특히:

- `feedback/turn_engine.py`는 `../judgment`, `../pipeline`, `..`(repo root)를
  `sys.path`에 넣는다. 그래서 원본 repo root 파일 `timeout_config.py`는 이 디렉터리
  루트에, 원본의 `pipeline/evidence_bridge.py`는 `pipeline/pipeline/` 이중 경로처럼
  보이는 `./pipeline/evidence_bridge.py`에 있다 (원본 레포에 `feedback/evidence_bridge.py`는
  존재하지 않음 — 계획서의 경로 표기와 다른 지점).
- 각 hook 모듈(`*_hook.py`)은 자기 파일 위치 기준으로 `*_patterns/`, `idioms/`,
  `*_weights/` 등의 JSON을 로드한다.

## 복사한 파일 (40개)

### cognition (P02 스캔, 1)
- `cognition/two_tier_scan.py`

### judgment (P02 판단 + P03 분류기, 24)
- `judgment/score_findings.py`
- `judgment/idiom_filter.py`
- `judgment/tier_b_suppression_filter.py`
- `judgment/subrubric.py`
- `judgment/tier_b_hook.py`
- `judgment/subrubric_hook.py`
- `judgment/importance_rank.py`
- `judgment/isolation_classifier.py`
- `judgment/isolation_hook.py`
- `judgment/rank_weights/rank_weights.json`
- `judgment/isolation_categories/{role_separation,domain_irrelevance,alt_storage_or_scope,perf_optimization}/patterns.json` (4)
- `judgment/tier_b_suppressions/suppressions.json`
- `judgment/subrubric_weights/{question_value,design_intent,risk}/weights.json` (3)
- `judgment/idioms/{python,java,cpp,swift,javascript,c}/idiom_patterns.json` (6)

### feedback (P03 오케스트레이션·분류기·프롬프트 자산, 13)
- `feedback/turn_engine.py` — 턴 루프 Python 원형 (Phase 3 base)
- `feedback/nvidia_client.py`
- `feedback/nvidia_key_pool.py` — nvidia_client의 import 의존
- `feedback/generate_questions.py` — turn_engine의 import 의존 (`_as_openai_tool`)
- `feedback/interview_rubric.py` — per-답변 5축 루브릭 (후채점 전환 시 참고용)
- `feedback/llm_interview_grader.py` — per-답변 채점기 (Phase 4에서 transcript 후채점으로 교체 예정, 참고용)
- `feedback/depth_ladder_template.md` — 질문 depth ladder 수기 템플릿 (프롬프트 원형 자산)
- `feedback/reflection_signal.py`
- `feedback/reflection_hook.py`
- `feedback/reflection_patterns/{new_judgment,concrete_improvement,reason_explanation,self_error_recognition}/patterns.json` (4)

### pipeline / root (2)
- `pipeline/evidence_bridge.py` — turn_engine의 import 의존 (`finding_category`)
- `timeout_config.py` — turn_engine·nvidia_client가 repo root 기준으로 import (`DEFAULT_MAX_TOKENS` 등)

## 목업 파일 목록과의 대조

- `AI/shared/p02-engine.js`의 `PIPELINE_FILES`는 실제로는 **23개**(계획서 표기 25개와 다름),
  `AI/shared/p03-engine.js`의 `CLASSIFIER_FILES`는 **12개**(계획서 표기 14개와 다름).
  두 목록의 35개 항목은 isolation patterns JSON 4개가 중복 → 유니크 31개이며,
  전부 원본 레포에 존재함을 확인하고 모두 복사했다. 나머지 9개는 오케스트레이션
  import 체인 의존으로 추가한 파일이다.
- LLM 프롬프트 manifest는 레포의 `docs/lab/prompt_manifest.json`에 있으며, 목업
  `AI/shared/prompt_manifest.json`과 같은 계보다. 여기에는 복사하지 않았다
  (Phase 3에서 Reflection 제거·후채점 구조로 수정본을 새로 만들 예정이라 목업 쪽을 참조).

## 검증 (2026-07-20)

`cd AI/pipeline` 상태에서 `cognition/`, `judgment/`, `feedback/`를 `sys.path`에 넣고
아래 모듈 import 성공 확인 (Windows, 로컬 CPython):

- `two_tier_scan` (`scan` callable 확인)
- `score_findings` (`score` callable 확인)
- `isolation_classifier`, `reflection_signal`
- `turn_engine` (→ evidence_bridge, timeout_config, generate_questions, idiom_filter 연쇄 import 성공)
- `nvidia_client` (→ nvidia_key_pool)
- `llm_interview_grader` (→ interview_rubric)
- `evidence_bridge`

주의: hook 모듈들은 학습 피드백을 `feedback_log.jsonl`로 기록하려 할 수 있다.
원본의 기존 로그 파일은 복사하지 않았으며, 실행 중 생성되는 로그는 커밋하지 않는다.
