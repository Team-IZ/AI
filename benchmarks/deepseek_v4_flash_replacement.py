""" deepseek-ai/deepseek-v4-flash 대체 모델 벤치마크.

deepseek-v4-flash가 맡던 3가지 역할(세션 답변 채점 p04-5 · 보고서 생성 p04-6 ·
면담 브리프 생성 ib-1)에 대해 20개 후보 모델을 품질/소요시간 2축으로 재측정한다.
후보 선정 근거는 `~/.claude/plans/jazzy-puzzling-crayon.md` §1 참고 — 과거 D116
4축 벤치마크(Code_reviewer_with_feedback/turn_engine_4axis_summary.json), P01
30/21모델 벤치마크, nvidia-build 3축 벤치마크, SURVEY_RESULTS.md(87모델
tool-calling) 등 기존 실측 데이터를 재활용해서 좁힌 목록이다 — 이번 스크립트는
그 위에 "품질·속도"만 새로 잰다.

D-bench1: NvidiaKeyPool/NvidiaRotatingClient는 이 저장소에 이미 vendored된
app/llm/vendor/ 사본을 그대로 쓴다(app/llm/client.py의 chat() 경유) -- 별도로
Code_reviewer_with_feedback을 import하지 않는다(교차 저장소 import는 한쪽만
바뀌면 다른 쪽이 조용히 낡는다, vendor/SOURCE.md와 같은 이유).
  WHY: 이미 검증된 7-key 로테이션·429/529 재시도 로직을 재사용 -- 새로 짜면
       같은 버그(예: per-model이 아니라 전체 공유 버킷)를 다시 밟을 위험이 있다.
  COST: .env 키는 Code_reviewer_with_feedback/.env에만 있고 이 저장소엔 없다 --
        실행 전 그 경로에서 os.environ으로 직접 끌어와야 한다(아래 _load_keys).
  EXIT: 7키가 소진되거나 폐기되면 이 파일의 _load_keys()만 다른 .env 경로로 바꾸면 된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_CODE_REVIEWER_ENV = Path.home() / "Desktop" / "Code_reviewer_with_feedback" / ".env"


def _load_keys(env_file: Path) -> int:
    """NVIDIA_API_KEY_<N>만 os.environ에 올린다. 이미 있는 값은 안 덮는다.

    app.config.load_api_keys_into_env()과 같은 규약(이미 있는 환경변수 우선)을
    따라야 vendor NvidiaKeyPool.from_env()가 기대하는 모양과 어긋나지 않는다.
    """
    if not env_file.exists():
        return 0
    loaded = 0
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("NVIDIA_API_KEY_") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name not in os.environ:
            os.environ[name] = value.strip()
            loaded += 1
    return loaded


_loaded = _load_keys(_CODE_REVIEWER_ENV)
if _loaded == 0 and not all(f"NVIDIA_API_KEY_{i}" in os.environ for i in range(1, 8)):
    print(f"[경고] {_CODE_REVIEWER_ENV}에서 키를 못 읽었고 환경변수에도 없습니다.", file=sys.stderr)

from app.engines import interview_brief as ib_engine  # noqa: E402
from app.engines.analysis import grading, report, scoring, stages  # noqa: E402
from app.llm import client  # noqa: E402
from app.schemas.interview_brief import InterviewBriefRequest  # noqa: E402

# ── 후보 20개 (플랜 §1과 동일 순서) ──────────────────────────────────────────
CANDIDATE_MODELS = [
    "deepseek-ai/deepseek-v4-pro",
    "minimaxai/minimax-m3",
    "minimaxai/minimax-m2.7",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "thinkingmachines/inkling",
    "openai/gpt-oss-20b",
    "mistralai/mistral-nemotron",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "nvidia/nemotron-3-super-120b-a12b",
    "moonshotai/kimi-k2.6",
    "openai/gpt-oss-120b",
    "mistralai/mistral-small-4-119b-2603",
    "abacusai/dracarys-llama-3.1-70b-instruct",
    "google/gemma-4-31b-it",
    "meta/llama-4-maverick-17b-128e-instruct",
    "nvidia/nvidia-nemotron-nano-9b-v2",
    "mistralai/mistral-large-3-675b-instruct-2512",
    "meta/llama-3.3-70b-instruct",
    "google/gemma-3n-e4b-it",
    "meta/llama-3.1-70b-instruct",
]

# 후보 20개에 없는 고정 판단모델 -- 순환논리(자기 응답을 자기가 채점) 방지.
# nemotron-3-super-49b-v1(.5 아님, 구버전)은 후보에 없어 안전하다.
JUDGE_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"

# D-bench2(드라이런 실측, 2026-08-07): report.build()는 timeout_s를 안 받는 공개
# 시그니처라 내부적으로 client.DEFAULT_TIMEOUT_S(600초)·max_attempts=6이 고정된다 --
# 실제로 mistral-nemotron 1건이 630초를 태웠다(20모델x3역할x3반복 규모에서 report
# 역할만으로 수 시간이 될 수 있다). 벤치마크에서는 report.build()를 안 쓰고
# stages.call("p04-6", ...)을 직접 불러 더 짧은 timeout/시도횟수를 준다.
BENCH_REPORT_TIMEOUT_S = 60.0
BENCH_REPORT_MAX_ATTEMPTS = 3

# 판단모델 호출 자체도 네트워크 호출이라 일시적 타임아웃이 난다(드라이런 실측:
# 20초 timeout 1회차는 TIMEOUT, 60초로 올리니 2.9초 만에 성공 -- 모델이 느린 게
# 아니라 단발성 실패였다). client.chat()은 stages.call과 달리 재시도가 없어
# 여기서 직접 2회 시도한다.
JUDGE_TIMEOUT_S = 30.0
JUDGE_MAX_ATTEMPTS = 2

ROLES = ("grading", "report", "interview_brief")

# ── 픽스처 1: 세션 답변 채점(p04-5) -- 4축 x 3persona = 12케이스 ────────────
_CODE_SNIPPET = (
    "class OrderService:\n"
    "    def apply_discount(self, order, user):\n"
    "        if user.is_member and order.total > 50000:\n"
    "            order.total *= 0.9\n"
    "        return order\n"
)

# persona별 기대 점수 범위(구조검사용). 실제 정답이 없는 서술형이라 "범위 안에
# 들었는가"로만 판정한다 -- scoring.py의 0~5 루브릭 문구를 그대로 따른 것.
GRADING_CASES: list[dict[str, Any]] = []
_PERSONA_ANSWERS = {
    "strong": {
        "L1": "apply_discount는 order와 user를 받아서, user.is_member가 True이고 "
              "order.total이 50000을 넘으면 order.total에 0.9를 곱해 10% 할인하고, "
              "그 order를 그대로 리턴합니다. 조건을 안 만족하면 할인 없이 그대로 리턴돼요.",
        "L2": "회원 등급별 할인 정책을 하나의 서비스 메서드에 모아두면 주문 처리 로직 "
              "여기저기에 할인 조건이 흩어지는 걸 막을 수 있어서 이렇게 설계했습니다. "
              "할인율이 바뀌어도 이 메서드 하나만 고치면 되게 하려는 목적이에요.",
        "L3": "할인을 order 모델 자체에 메서드로 넣는 대안도 있었는데, 그러면 주문 "
              "데이터와 정책 로직이 섞여서 정책만 따로 테스트하기 어려워집니다. 지금 "
              "구조는 서비스 계층에 정책을 모아서 order는 순수 데이터로 남길 수 있어요.",
        "L4": "동시에 여러 스레드가 같은 order 객체에 이 메서드를 부르면 order.total이 "
              "레이스 컨디션으로 중복 할인될 수 있습니다. 또 order.total이 부동소수점이라 "
              "누적 계산 시 반올림 오차가 쌓일 수 있어요.",
    },
    "weak": {
        "L1": "할인해주는 코드인 것 같아요.",
        "L2": "그냥 이렇게 짜는 게 편해서요.",
        "L3": "다른 방법은 잘 모르겠습니다.",
        "L4": "문제 없을 것 같은데요.",
    },
    "ambiguous": {
        "L1": "회원이면 할인해주고 아니면 안 해주는 함수예요. total이 바뀌어요.",
        "L2": "보통 이렇게 조건문으로 처리하는 방식을 배워서 썼습니다.",
        "L3": "다른 방식으로 짜는 것도 봤는데 이름은 기억 안 나요.",
        "L4": "큰 문제는 없을 것 같은데 애매한 경우가 있을 수도 있어요.",
    },
}
_EXPECTED_RANGE = {
    "strong": (4, 5),
    "weak": (0, 1),
    "ambiguous": (2, 3),
}
for _axis in scoring.AXIS_CODES:
    for _persona, _answers in _PERSONA_ANSWERS.items():
        GRADING_CASES.append({
            "case_id": f"{_axis}-{_persona}",
            "axis_code": _axis,
            "question": scoring.AXES[_axis]["question_intent"],
            "answer": _answers[_axis],
            "expected_range": _EXPECTED_RANGE[_persona],
            "code_snippet": _CODE_SNIPPET,
            "code_ref": "app/services/order_service.py:1-5",
        })

# ── 픽스처 2: 보고서(p04-6) -- 위 12케이스 중 strong persona로 만든 가상 transcript ──
_TEACH_ID = "t-state-mgmt"
REPORT_FIXTURE = {
    "problem_id": "bench-problem-1",
    "problem_no": 1,
    "teaches": [{"id": _TEACH_ID, "label": "상태 관리", "unit_id": "U3", "source_pages": [42, 43]}],
    "analysis_documents": [{"overview": "주문 서비스의 회원 할인 로직", "structure": []}],
    "requirement_results": [
        {"requirement_id": "r1", "verdict": "PASS", "note": None},
    ],
    "transcript": [
        {
            "axis_code": axis, "score": 5, "passed": True, "hints_used": 0,
            "question_text": scoring.AXES[axis]["question_intent"],
            "hint_text": None,
            "answer_text": _PERSONA_ANSWERS["strong"][axis],
        }
        for axis in scoring.AXIS_CODES
    ],
}

# ── 픽스처 3: 면담 브리프(ib-1) -- tests/test_interview_brief.py의 _request()와 동일 ──
INTERVIEW_BRIEF_FIXTURE: dict[str, Any] = {
    "target": {
        "userName": "김OO", "className": "A반", "projectName": "미니프로젝트 3차",
        "projectCategory": "MINI_PROJECT", "roundName": "3회차",
    },
    "briefContext": {"briefType": "STANDARD", "isFirstInterview": False},
    "riskReasons": [{
        "reasonCode": "STAGE_DECLINE", "evaluationStatus": "MATCHED",
        "reasonSummary": "2회차 L3 도달 -> 3회차 L1 도달",
        "detectedAt": "2026-08-01T09:12:00Z",
        "sourceInterviewSourceId": "src-risk-1",
    }],
    "validityReview": {"status": "NOT_REQUIRED"},
    "comprehension": {
        "attemptType": "INITIAL", "attemptStatus": "COMPLETED",
        "terminalReasonCode": "COMPLETED", "sessionEndReasonCode": "TERMINATED_AT_L2",
        "attemptInterviewSourceId": "src-attempt-1",
        "sessionInterviewSourceId": "src-session-1",
        "problems": [{
            "problemNo": 1, "conceptName": "상태 관리",
            "problemScope": "TEAM_SHARED_PROBLEM", "generationStatus": "GENERATED",
            "interviewSourceId": "src-problem-1",
            "stages": [{
                "problemStageId": "ps-1", "axisCode": "L2", "status": "NOT_PASSED",
                "questionText": "이 메서드가 호출되는 시점은?",
                "questionAnswerText": "잘 모르겠습니다",
                "questionScore": 1, "questionPassed": False,
                "interviewSourceId": "src-stage-1",
            }],
        }],
    },
    "priorInterviews": [],
    "observationNotes": [],
}


# ── 시행 1건 실행 ────────────────────────────────────────────────────────

def _run_grading_case(model_code: str, case: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        grade = grading.grade(
            case["axis_code"], case["question"], case["answer"],
            model_code=model_code, code_snippet=case["code_snippet"],
            code_ref=case["code_ref"],
        )
    except Exception as exc:  # noqa: BLE001 -- 벤치마크는 실패도 데이터다
        return {
            "ok": False, "elapsed_s": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }
    elapsed = time.monotonic() - started
    lo, hi = case["expected_range"]
    struct_ok = lo <= grade.score <= hi
    return {
        "ok": True, "elapsed_s": elapsed, "struct_ok": struct_ok,
        "score": grade.score, "matched_level": grade.matched_level,
        "evidence": grade.evidence, "missing": grade.missing,
        "case_id": case["case_id"], "expected_range": case["expected_range"],
    }


def _run_report(model_code: str) -> dict[str, Any]:
    """report.build()를 안 쓴다 -- timeout_s를 못 받는 시그니처라(위 D-bench2 참고)

    벤치마크에 부적합하다. 같은 프롬프트 조립(_teaches_block 등)만 재사용하고
    stages.call은 여기서 짧은 timeout으로 직접 부른다.
    """
    started = time.monotonic()
    try:
        result = stages.call("p04-6", {
            "teaches_block": report._teaches_block(REPORT_FIXTURE["teaches"]),
            "analysis_block": report._analysis_block(REPORT_FIXTURE["analysis_documents"]),
            "requirements_block": report._requirements_block(REPORT_FIXTURE["requirement_results"]),
            "transcript_block": report._transcript_block(REPORT_FIXTURE["transcript"]),
        }, model_code=model_code, timeout_s=BENCH_REPORT_TIMEOUT_S,
           max_attempts=BENCH_REPORT_MAX_ATTEMPTS)
    except Exception as exc:  # noqa: BLE001 -- 벤치마크는 타임아웃/실패도 데이터다
        return {"ok": False, "elapsed_s": time.monotonic() - started,
                "error": f"{type(exc).__name__}: {exc}"}
    elapsed = time.monotonic() - started
    data = result.data
    allowed_teach_ids = {t["id"] for t in REPORT_FIXTURE["teaches"]}
    cited = {g.get("teach_id") for g in (data.get("gaps") or []) if isinstance(g, dict) and g.get("teach_id")}
    struct_ok = bool(data.get("summary")) and cited.issubset(allowed_teach_ids)
    return {
        "ok": True, "elapsed_s": elapsed, "struct_ok": struct_ok,
        "narrative": data,
    }


def _run_interview_brief(model_code: str) -> dict[str, Any]:
    req = InterviewBriefRequest.model_validate(INTERVIEW_BRIEF_FIXTURE)
    allowed_ids = ib_engine._collect_allowed_source_ids(req)
    min_items, max_items = (6, 8) if req.brief_context.is_first_interview else (4, 8)

    started = time.monotonic()
    try:
        result = stages.call("ib-1", {
            "target_block": ib_engine._target_block(req.target),
            "brief_context_block": ib_engine._brief_context_block(req.brief_context),
            "validity_review_block": ib_engine._validity_review_block(req.validity_review),
            "risk_reasons_block": ib_engine._risk_reasons_block(req.risk_reasons),
            "comprehension_block": ib_engine._comprehension_block(req.comprehension),
            "prior_interviews_block": ib_engine._prior_interviews_block(req.prior_interviews),
            "observation_notes_block": ib_engine._observation_notes_block(req.observation_notes),
        }, model_code=model_code, timeout_s=client.SESSION_TIMEOUT_S,
           max_attempts=client.SESSION_MAX_ATTEMPTS)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed_s": time.monotonic() - started,
                "error": f"{type(exc).__name__}: {exc}"}
    elapsed = time.monotonic() - started

    items = result.data.get("items") or []
    orders = sorted(i.get("suggestedOrder") for i in items if isinstance(i.get("suggestedOrder"), int))
    order_ok = orders == list(range(1, len(items) + 1))
    count_ok = min_items <= len(items) <= max_items
    ids_ok = all(
        (i.get("interviewSourceId") is None) or (i.get("interviewSourceId") in allowed_ids)
        for i in items
    )
    struct_ok = count_ok and order_ok and ids_ok
    return {
        "ok": True, "elapsed_s": elapsed, "struct_ok": struct_ok,
        "opening_remark": result.data.get("openingRemark"), "items": items,
        "count_ok": count_ok, "order_ok": order_ok, "ids_ok": ids_ok,
    }


# ── LLM 판단(2차 품질 채점, 구조검사 통과분만) ────────────────────────────

# D-bench4(2026-08-07, 사용자 지시): 품질 축의 채점 기준점을 "루브릭에 홀로 부합하는가"
# 에서 "같은 파이프라인을 Sonnet이 거쳤을 때의 응답과 얼마나 가까운가"로 바꾼다.
#   WHY: 고정 판단모델(JUDGE_MODEL, NVIDIA Build 소형~중형 모델) 혼자 절대적으로
#        "좋다/나쁘다"를 판정하게 하면 판단모델 자체의 편향·불안정성이 그대로
#        품질 점수에 섞인다(이 세션에서 이미 JUDGE_MODEL 호출 자체가 1차 시도에서
#        타임아웃난 전례 있음). Sonnet 응답을 구체적 기준점(anchor)으로 주면
#        판단모델은 "이 후보가 그 기준점에 얼마나 가까운가"라는 상대 비교만 하면
#        되고, 이게 절대 채점보다 훨씬 안정적이다(LLM-as-judge 일반 원칙).
#   COST: Sonnet 기준답안은 세 역할 각각 1건(고정 픽스처 1개)만 만든다 -- 반복 없이
#         단일 기준점이라, Sonnet 자신의 응답 변동성(같은 프롬프트라도 매번 다를 수
#         있음)은 반영하지 못한다. 기준점 자체가 "언제나 만점"이 아닐 수 있다는
#         뜻이기도 하다.
#   EXIT: 기준점을 다시 뽑으려면 claude -p --model sonnet --safe-mode로 같은
#         프롬프트를 재실행해 benchmarks/results/sonnet_reference.json을 갱신하면 된다
#         (생성 스크립트: 이 파일 실행 이력 참고, /tmp/run_sonnet_ref.py 패턴).
_SONNET_REF_PATH = _REPO_ROOT / "benchmarks" / "results" / "sonnet_reference.json"
try:
    SONNET_REFERENCE: dict[str, Any] = json.loads(_SONNET_REF_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    SONNET_REFERENCE = {}
    print(f"[경고] {_SONNET_REF_PATH} 없음 -- Sonnet 기준점 없이 진행", file=sys.stderr)


def _judge(prompt: str) -> dict[str, Any] | None:
    """JUDGE_MODEL에게 1~5점 채점을 시킨다. 실패하면 None(집계에서 판단점수만 빠짐).

    client.chat()은 stages.call과 달리 자체 재시도가 없다 -- 드라이런 실측(2026-08-07):
    20초 timeout 1회차가 TIMEOUT으로 실패했는데 60초로는 2.9초 만에 성공했다(모델이
    느린 게 아니라 단발성 실패). 그래서 여기서 JUDGE_MAX_ATTEMPTS번 직접 재시도한다.
    """
    last_err: Exception | None = None
    for attempt in range(JUDGE_MAX_ATTEMPTS):
        try:
            res = client.chat(
                JUDGE_MODEL,
                [
                    {"role": "system", "content": "You are a strict Korean-language grading "
                     "assistant. Output strict JSON only: {\"score\": 1-5 int, \"reason\": \"...\"}"},
                    {"role": "user", "content": prompt},
                ],
                timeout_s=JUDGE_TIMEOUT_S, response_format={"type": "json_object"},
            )
            data = json.loads(res.content)
            score = int(data.get("score"))
            return {"score": max(1, min(5, score)), "reason": str(data.get("reason", ""))}
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    print(f"[judge 실패, {JUDGE_MAX_ATTEMPTS}회 재시도 후 포기] {type(last_err).__name__}: {last_err}",
          file=sys.stderr)
    return None


def _judge_grading(trial: dict[str, Any], case: dict[str, Any]) -> dict[str, Any] | None:
    ref = SONNET_REFERENCE.get("grading")
    ref_block = (
        f"\n\n[기준점: Sonnet이 같은 파이프라인으로 낸 응답]\n"
        f"점수: {ref['score']}, matched_level: {ref['matched_level']}\n"
        f"evidence: {ref['evidence']}\nmissing: {ref['missing']}"
    ) if ref else ""
    prompt = (
        f"채점 축: {case['axis_code']}\n루브릭:\n{scoring.rubric_block(case['axis_code'])}\n\n"
        f"학생 답변: {case['answer']}\n\n"
        f"AI가 매긴 점수: {trial['score']}, 근거: {trial['evidence']}"
        f"{ref_block}\n\n"
        "위 기준점(Sonnet 응답)에 견줘 이 AI의 채점이 얼마나 일치하는지 1~5점으로 "
        "평가하라(5=기준점과 사실상 동일한 판단, 1=기준점과 크게 어긋남). "
        "기준점이 없으면 루브릭 자체에 부합하는지로 평가하라."
    )
    return _judge(prompt)


def _judge_report(trial: dict[str, Any]) -> dict[str, Any] | None:
    narrative = trial["narrative"]
    transcript_text = "\n".join(
        f"{t['axis_code']}: {t['answer_text']}" for t in REPORT_FIXTURE["transcript"]
    )
    ref = SONNET_REFERENCE.get("report")
    ref_block = (
        f"\n\n[기준점: Sonnet이 같은 파이프라인으로 낸 보고서]\n"
        f"summary: {ref['summary']}\nstrengths: {ref['strengths']}\ngaps: {ref['gaps']}\n"
        f"autonomy_note: {ref['autonomy_note']}"
    ) if ref else ""
    prompt = (
        f"문답 기록:\n{transcript_text}\n\n"
        f"AI가 쓴 보고서 서술:\nsummary: {narrative.get('summary')}\n"
        f"strengths: {narrative.get('strengths')}\ngaps: {narrative.get('gaps')}\n"
        f"autonomy_note: {narrative.get('autonomy_note')}"
        f"{ref_block}\n\n"
        "위 기준점(Sonnet 보고서)과 비교해 이 AI의 서술이 근거성·구체성·통찰 면에서 "
        "얼마나 비슷한 수준인지 1~5점으로 평가하라(5=기준점과 동등하거나 그 이상, "
        "1=근거 없는 서술 다수로 기준점에 크게 못 미침). 기준점이 없으면 문답 기록에 "
        "실제로 근거하는지로만 평가하라."
    )
    return _judge(prompt)


def _judge_interview_brief(trial: dict[str, Any]) -> dict[str, Any] | None:
    questions = "\n".join(f"- {i.get('questionText')}" for i in trial["items"])
    ref = SONNET_REFERENCE.get("interview_brief")
    ref_block = ""
    if ref:
        ref_questions = "\n".join(f"- {i.get('questionText')}" for i in ref.get("items", []))
        ref_block = (
            f"\n\n[기준점: Sonnet이 같은 파이프라인으로 낸 응답]\n"
            f"여는 말: {ref.get('openingRemark')}\n질문 목록:\n{ref_questions}"
        )
    prompt = (
        f"여는 말: {trial['opening_remark']}\n질문 목록:\n{questions}"
        f"{ref_block}\n\n"
        "위 기준점(Sonnet 응답)과 비교해 이 AI의 여는말·질문이 자연스러운 구어체 "
        "한국어 수준·질문의 구체성 면에서 얼마나 비슷한지 1~5점으로 평가하라"
        "(5=기준점과 동등하거나 그 이상, 1=번역투·추상적이라 기준점에 크게 못 미침). "
        "기준점이 없으면 자연스러운 구어체인지로만 평가하라."
    )
    return _judge(prompt)


# ── 오케스트레이션 ──────────────────────────────────────────────────────

def _run_one(model_code: str, role: str, repeat_idx: int) -> dict[str, Any]:
    if role == "grading":
        case = GRADING_CASES[repeat_idx % len(GRADING_CASES)]
        trial = _run_grading_case(model_code, case)
        if trial.get("ok") and trial.get("struct_ok"):
            judged = _judge_grading(trial, case)
            trial["judge"] = judged
    elif role == "report":
        trial = _run_report(model_code)
        if trial.get("ok") and trial.get("struct_ok"):
            trial["judge"] = _judge_report(trial)
    else:
        trial = _run_interview_brief(model_code)
        if trial.get("ok") and trial.get("struct_ok"):
            trial["judge"] = _judge_interview_brief(trial)
    trial.update({"model": model_code, "role": role, "repeat_idx": repeat_idx})
    return trial


def run_concurrent(jobs: list[tuple], call_fn, max_workers: int = 8) -> list[dict[str, Any]]:
    """Code_reviewer_with_feedback/benchmarks/harness.py::run_concurrent와 같은 패턴."""
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(call_fn, *job): job for job in jobs}
        done = 0
        for fut in as_completed(futures):
            done += 1
            job = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                results.append({"ok": False, "error": str(exc), "model": job[0],
                                 "role": job[1], "repeat_idx": job[2]})
            print(f"[{done}/{len(jobs)}] {job[0]} / {job[1]} #{job[2]} 완료", file=sys.stderr)
    return results


def summarize(raw: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple, list[dict]] = {}
    for r in raw:
        by_key.setdefault((r["model"], r["role"]), []).append(r)

    summary: dict[str, Any] = {}
    for (model, role), trials in by_key.items():
        n = len(trials)
        ok_trials = [t for t in trials if t.get("ok")]
        struct_ok_trials = [t for t in ok_trials if t.get("struct_ok")]
        judged = [t["judge"]["score"] for t in struct_ok_trials if t.get("judge")]
        struct_pass_rate = len(struct_ok_trials) / n if n else 0.0
        judge_mean = sum(judged) / len(judged) if judged else None
        # D-bench2: judge_mean이 None인 건 "후보 모델이 나쁘다"가 아니라 "판단모델
        # 호출 자체가 실패했다"는 뜻이다(위 _judge 참고) -- 후보를 0점 처리하면
        # 안 되므로 그 경우 구조검사 통과율만으로 품질을 매긴다(판단모델 감점분 없음).
        quality = struct_pass_rate * ((judge_mean / 5) if judge_mean is not None else 1.0)
        elapsed = [t["elapsed_s"] for t in ok_trials if "elapsed_s" in t]
        summary.setdefault(model, {})[role] = {
            "n_trials": n, "n_ok": len(ok_trials), "n_struct_ok": len(struct_ok_trials),
            "struct_pass_rate": struct_pass_rate, "judge_mean": judge_mean,
            "n_judged": len(judged),  # struct_ok 중 실제로 판단모델 채점까지 받은 수
            "quality_score": quality,
            "mean_elapsed_s": sum(elapsed) / len(elapsed) if elapsed else None,
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--models", nargs="*", default=None,
                         help="지정 시 이 모델만 실행(드라이런용). 기본은 20개 전부")
    parser.add_argument("--roles", nargs="*", default=list(ROLES))
    parser.add_argument("--out-dir", default=str(_REPO_ROOT / "benchmarks" / "results"))
    parser.add_argument("--tag", default="deepseek_v4_flash_replacement")
    args = parser.parse_args()

    models = args.models or CANDIDATE_MODELS
    jobs = [
        (model, role, i)
        for model in models
        for role in args.roles
        for i in range(args.repeats)
    ]
    print(f"총 {len(jobs)}건 실행 (모델 {len(models)} x 역할 {len(args.roles)} x "
          f"반복 {args.repeats}), max_workers={args.max_workers}", file=sys.stderr)

    started = datetime.now(timezone.utc)
    raw = run_concurrent(jobs, _run_one, max_workers=args.max_workers)
    finished = datetime.now(timezone.utc)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{args.tag}_raw.json"
    summary_path = out_dir / f"{args.tag}_summary.json"

    raw_path.write_text(json.dumps({
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "models": models, "roles": args.roles, "repeats": args.repeats,
        "judge_model": JUDGE_MODEL, "trials": raw,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path.write_text(json.dumps(summarize(raw), ensure_ascii=False, indent=2),
                             encoding="utf-8")

    print(f"raw -> {raw_path}", file=sys.stderr)
    print(f"summary -> {summary_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
