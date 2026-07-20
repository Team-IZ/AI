# D63: 5축(FR-04-01) 채점을 스키마강제 tool-calling(GRADING_TOOL)으로 구현 — 자유서술 채점 아님
#   WHY: D56/D58이 이미 generate_questions.py의 DEPTH_LADDER_TOOL(7필드)에서 스키마강제
#        tool-calling이 안정적으로 동작함을 검증했다(qwen3-next-80b-a3b-instruct 100% 준수).
#        자유서술 채점은 파싱이 불안정하고 "근거 인용 강제"(기획명세서 00시트 확정 결정)를
#        구조적으로 보장할 수 없다.
#   COST: 5축×{score,evidence} 스키마가 7필드 DEPTH_LADDER보다 커서 일부 모델의 tool-calling
#        실패율이 오를 수 있다(미검증 — benchmarks/grading_run_benchmark.py가 실측한다).
#   EXIT: 실패율이 높으면 축별로 5번 개별 tool call로 쪼갠다.
#
# D64: Ground truth를 목표레벨 사전라벨링 시뮬레이션 답변으로 구성(실제 학생 답변 전무)
#   WHY: README/EVALUATION.md가 이미 "실제 학생 답변으로 단 한 건도 검증된 적 없다"고
#        인정한 상태 — 유일하게 지금 실행 가능한 방법이 시뮬레이션.
#   COST: 목표레벨=정답 취급은 자기참조적 라벨(우리가 그 레벨을 의도하고 썼다는 것)일 뿐
#        실제 사람 채점자와의 합치도가 아니다. benchmarks/grading_testset.py에 명시.
#   EXIT: 실제 학생 인터뷰 데이터가 쌓이면 사람이 채점한 실답변 셋으로 교체.
#
# 참고: 기획명세서_AI기반_프로젝트교육품질관리플랫폼.xlsx(00시트, "확정 설계 결정")는 질문생성기와
# 채점기가 "같은 모델·다른 프롬프트"로 동작해야 한다고 이미 확정(Locked)했다 — 이 모듈은
# generate_questions.py와 별개 프롬프트를 쓰지만 같은 PROVIDER/MODEL 환경변수 컨벤션을
# 공유해서 "같은 모델"이라는 결정을 코드로도 지킨다(MODEL 기본값이 generate_questions.py의
# _DEFAULT_MODEL과 동일).
from __future__ import annotations

import json
import os
import sys

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from nvidia_client import NvidiaRotatingClient
    from nvidia_key_pool import NvidiaKeyPool
except ImportError:
    NvidiaRotatingClient = None
    NvidiaKeyPool = None

try:
    # D104: centralized max_tokens (repo root), same fallback pattern as nvidia_client.py.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from timeout_config import DEFAULT_MAX_TOKENS
except ImportError:
    DEFAULT_MAX_TOKENS = 2048

import interview_rubric as rubric

# FR-04-01 이름(코드_이해/반례_대응/대안_비교/설계_논리/자기_수정)을 스키마에 쓴다 — 기획명세서
# 04시트 R33C6("5축=코드이해·설계논리·대안비교·반례대응·자기수정")·ERD(02시트)의 공식 명칭과
# 일치시키기 위해 RUBRIC의 내부 키(구조_인지도 등, D57 이전 레거시)가 아니라 별칭을 쓴다.
FR_AXES = tuple(rubric.FR_AXIS_ALIAS.values())

GRADING_TOOL = {
    "name": "grade_interview_answer",
    "description": (
        "학생 답변을 FR-04-01 5축 루브릭으로 채점한다. 5축 전부 채워야 하며, "
        "각 축은 1~5점 정수와 그 점수의 근거(답변의 어느 부분을 보고 판단했는지, 근거 인용 필수 — "
        "기획명세서 확정 결정)를 함께 낸다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            axis: {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "description": "1~5 정수"},
                    "evidence": {"type": "string", "description": "이 점수의 근거(답변 인용/요약, 빈 문자열 금지)"},
                },
                "required": ["score", "evidence"],
            }
            for axis in FR_AXES
        },
        "required": list(FR_AXES),
    },
}


def _as_openai_tool(anthropic_tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": anthropic_tool["name"],
            "description": anthropic_tool["description"],
            "parameters": anthropic_tool["input_schema"],
        },
    }


PROVIDER = os.environ.get("FEEDBACK_PROVIDER", "nvidia")
_DEFAULT_MODEL = "qwen/qwen3-next-80b-a3b-instruct" if PROVIDER == "nvidia" else "claude-sonnet-5"
MODEL = os.environ.get("GRADING_MODEL", os.environ.get("FEEDBACK_MODEL", _DEFAULT_MODEL))


def _rubric_block() -> str:
    lines = []
    for axis in FR_AXES:
        lines.append(f"### {axis}")
        for score in (5, 4, 3, 2, 1):
            lines.append(f"  {score}점: {rubric.describe(axis, score)}")
    return "\n".join(lines)


def build_grading_prompt(finding: dict, question: str, answer: str) -> str:
    return (
        "다음은 코드리뷰 인터뷰의 한 Decision Point에 대한 학생 답변이다. "
        "아래 5축 루브릭의 레벨 설명을 근거로 삼아 답변을 채점하라. "
        "루브릭에 없는 기준을 새로 만들지 말고, 반드시 아래 레벨 설명과 답변 내용을 직접 "
        "대조해서 근거를 남겨라. 답변이 질문과 무관하거나 텅 비어 있으면 해당 축은 1점을 주고 "
        "그 사실을 근거에 그대로 적어라.\n\n"
        f"{_rubric_block()}\n\n"
        f"finding: {finding.get('finding')}\n"
        f"file: {finding.get('file')}\n"
        f"질문: {question}\n"
        f"학생 답변: {answer}\n"
    )


def _validate_grading(result: dict) -> dict:
    missing = [a for a in FR_AXES if a not in result]
    if missing:
        raise ValueError(f"5축 중 누락: {missing}")
    for axis in FR_AXES:
        entry = result[axis]
        score = entry.get("score")
        if not isinstance(score, int) or not (1 <= score <= 5):
            raise ValueError(f"{axis}.score는 1~5 정수여야 함, got {score!r}")
        if not entry.get("evidence"):
            raise ValueError(f"{axis}.evidence가 비어있음")
    return result


def parse_nvidia_tool_response(response: dict) -> dict:
    """OpenAI 호환 tool_calls 응답에서 grade_interview_answer 인자를 뽑아 검증한다.

    generate_questions.parse_nvidia_tool_response()와 동일 패턴 — 네트워크 없이도
    (고정 응답 fixture로) 테스트 가능하도록 순수 함수로 분리해뒀다.
    """
    choice = response["choices"][0]["message"]
    for call in choice.get("tool_calls") or []:
        if call["function"]["name"] == "grade_interview_answer":
            result = json.loads(call["function"]["arguments"])
            return _validate_grading(result)
    raise RuntimeError(
        "tool_calls를 찾지 못함 — 모델이 이 요청에서 tool_choice를 지키지 않았을 수 있음. "
        f"content={choice.get('content')!r}"
    )


def _grade_via_nvidia(client, finding, question, answer):
    response = client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": build_grading_prompt(finding, question, answer)}],
        tools=[_as_openai_tool(GRADING_TOOL)],
        tool_choice={"type": "function", "function": {"name": "grade_interview_answer"}},
        # D104: was 1536 (D63); DEFAULT_MAX_TOKENS(2048) is a strict superset --
        # you only pay for tokens actually generated, so raising the cap is safe.
        max_tokens=DEFAULT_MAX_TOKENS,
        temperature=0.0,
    )
    return parse_nvidia_tool_response(response)


def _grade_via_anthropic(client, finding, question, answer):
    message = client.messages.create(
        model=MODEL,
        max_tokens=DEFAULT_MAX_TOKENS,  # D104: was 1536, see _grade_via_nvidia
        tools=[GRADING_TOOL],
        tool_choice={"type": "tool", "name": "grade_interview_answer"},
        messages=[{"role": "user", "content": build_grading_prompt(finding, question, answer)}],
    )
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "grade_interview_answer":
            return _validate_grading(block.input)
    raise RuntimeError("tool_use 블록을 찾지 못함 — 모델이 스키마를 안 지킴")


def grade_answer(client, finding: dict, question: str, answer: str) -> dict:
    if PROVIDER == "nvidia":
        return _grade_via_nvidia(client, finding, question, answer)
    return _grade_via_anthropic(client, finding, question, answer)


def _build_client():
    if PROVIDER == "nvidia":
        if NvidiaRotatingClient is None:
            print("nvidia_client 모듈을 찾을 수 없습니다 (feedback/nvidia_client.py 확인).", file=sys.stderr)
            sys.exit(1)
        try:
            pool = NvidiaKeyPool.from_env()
        except ValueError as e:
            print(f"{e}", file=sys.stderr)
            sys.exit(1)
        return NvidiaRotatingClient(pool=pool)

    if anthropic is None:
        print("anthropic 패키지가 없습니다. `pip install anthropic` 실행 후 재시도하세요.", file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


def main():
    if len(sys.argv) < 2:
        print("usage: llm_interview_grader.py <testset.json>", file=sys.stderr)
        sys.exit(1)
    client = _build_client()
    with open(sys.argv[1], encoding="utf-8") as f:
        testset = json.load(f)

    results = []
    for case in testset:
        try:
            graded = grade_answer(client, case["finding"], case["question"], case["answer"])
            results.append({"case_id": case["id"], "target_level": case.get("target_level"), "graded": graded})
        except Exception as e:
            results.append({"case_id": case["id"], "error": str(e)})

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
