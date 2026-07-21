import json
import os
import sys

# D11: 피드백 블록 7단계 질문 생성을 실제 LLM 호출(Anthropic tool-use)로 자동화
#   WHY: 지금까지는 사람이 findings.md를 수기로 채웠음(README "다음 단계" 3번) — 즉흥 생성
#        편차 문제(D1)를 코드 레벨에서 해소하려면 스키마를 tool-use로 강제해야 함
#   COST: Anthropic API 키/네트워크 의존성 생김, 호출당 비용 발생, 오프라인 실행 불가
#   EXIT: ANTHROPIC_API_KEY 없으면 조용히 실패하지 않고 여기서 즉시 중단. API 없이 쓰려면
#        feedback/depth_ladder_template.md의 수기 체크리스트로 되돌아가면 됨(코드 삭제 불필요)
#
# D56: 기본 제공자를 Anthropic Claude에서 NVIDIA Build(qwen/qwen3.5-397b-a17b)로 전환
#   WHY: 2026-07-06 별도 세션에서 실제 코드(compression_rank1_test.py)를 NVIDIA 3개 모델
#        (qwen3.5-397b-a17b / nemotron-3.3-super-49b / llama-3.1-8b-instruct)에 리뷰시키고
#        각 지적을 코드와 대조 검증함 — qwen3.5-397b-a17b가 사실관계 오류 없이 가장 정확했음
#        (nemotron-49b는 "index 파일을 여러 번 로드한다"는 틀린 지적을 확신도 High로 냄,
#        llama-3.1-8b는 할루시네이션+반복 필러). NVIDIA Build 무료 티어는 40 RPM/모델/키 한도
#        외 별도 quota가 없어(주간/월간 제한 없음, 2026-07-06 확인) 키 풀링만 하면 비용 없이
#        운용 가능(../../nvidia-build 참고, 7키 풀 시 이론상 280 RPM)
#   COST: NVIDIA Build는 OpenAI 호환 스키마(tools/tool_calls, 문자열 arguments)라 Anthropic의
#        객체형 tool_use 블록과 응답 형태가 완전히 다름 — 파싱을 provider별로 분기해야 함.
#        이 저장소 세션에는 NVIDIA_API_KEY가 없어(nvidia-build 쪽 세션에서만 보유) qwen3.5가
#        이 정확한 depth_ladder_questions 스키마(7필드 강제)에서도 tool_choice를 순순히 지키는지는
#        **실제 호출로 검증 못 함** — parse_nvidia_tool_response()의 파싱/에러 처리 로직만
#        smoke_test_nvidia_parsing.py로 구조 검증(네트워크 없이 고정 fixture 사용)
#   EXIT: FEEDBACK_PROVIDER=anthropic 환경변수 하나로 즉시 원복 — 코드 삭제 불필요, 두 경로 모두
#        유지됨. NVIDIA 쪽에서 tool_choice 미준수가 실제로 확인되면 이 파일을 고칠 필요 없이
#        기본값(PROVIDER 변수)만 "anthropic"으로 되돌리면 됨
#
# D58: 기본 모델을 qwen/qwen3.5-397b-a17b에서 qwen/qwen3-next-80b-a3b-instruct로 교체
#   WHY: 2026-07-06 라이브 전수조사(87개 카탈로그 모델 × 12개 실제 finding, LMS+study_match
#        fixture 전부)로 D56의 근거(3모델 코드리뷰 벤치마크)를 이 저장소의 실제 task(스키마
#        강제 tool-calling)로 재검증함. qwen3.5-397b-a17b는 tool_choice 100% 준수(12/12)하지만
#        평균 52.9s/호출 — qwen3-next-80b-a3b-instruct도 100% 준수(12/12)하면서 13.2s로 4배
#        빠르고, 질문 품질도 대조 확인 결과 대등하게 구체적/맞춤형(예: tier-b-risk:Bookshelf.jsx
#        dangerous-html finding에서 DOMPurify/markdown-to-jsx 등 구체적 대안까지 언급).
#        qwen3-next-80b-a3b-instruct는 별도 코드리뷰 벤치마크(nvidia-build repo)에서도 속도·
#        정확도·재현성 3축 전부 상위였음 — 두 독립적인 task에서 같은 결론이 나온 것.
#        결과 전체는 SURVEY_RESULTS.md 참고.
#   COST: 없음 확인됨 — 같은 증거 기준으로 순수 개선. 단, 이 서베이는 idiom_filtered 케이스가
#        2/12뿐이라 "짧게/쉽게" 지시 준수 여부는 약하게만 검증됨(그래도 두 모델 모두 통과).
#   EXIT: 이 모델이 실전에서 문제 생기면 SURVEY_RESULTS.md의 순위표에서 다음 후보로 즉시 교체
#        가능(전부 100% 준수 확인됨): stepfun-ai/step-3.5-flash(4.6s, 가장 빠름, 미검증 영역=
#        재현성), deepseek-ai/deepseek-v4-pro(48.9s, 별도 벤치마크에서 재현성 최고 0.9)

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

DEPTH_LADDER_KEYS = ["what", "how", "why", "alternative", "trade_off", "constraint", "reflection"]

DEPTH_LADDER_TOOL = {
    "name": "depth_ladder_questions",
    "description": "판단 블록 finding에 대한 Ownership Cycle 7단계 질문을 전부 채워 반환한다. "
                    "하나라도 비면 안 된다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "description": "무엇을 했는지 사실 확인 질문"},
            "how": {"type": "string", "description": "구체적 구현 메커니즘을 묻는 질문"},
            "why": {"type": "string", "description": "그 방식을 선택한 이유를 묻는 질문"},
            "alternative": {"type": "string", "description": "대안을 인지하고 있는지 묻는 질문"},
            "trade_off": {"type": "string", "description": "선택지 간 트레이드오프 비교를 요구하는 질문"},
            "constraint": {"type": "string", "description": "제약조건(시간/기술/팀) 반영 여부를 묻는 질문"},
            "reflection": {"type": "string", "description": "자기 수정을 유도하는 질문"},
        },
        "required": DEPTH_LADDER_KEYS,
    },
}


def _as_openai_tool(anthropic_tool):
    """Anthropic {name, description, input_schema} -> OpenAI {type, function:{...,parameters}}.

    NVIDIA Build's /v1/chat/completions is OpenAI-compatible, not Anthropic-compatible —
    the two APIs wrap the same JSON-schema payload differently. See D56.
    """
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
MODEL = os.environ.get("FEEDBACK_MODEL", _DEFAULT_MODEL)


def build_prompt(finding):
    return (
        "다음은 코드 리뷰 판단 블록이 뽑아낸 finding이다. 이 finding을 근거로 학생/지원자의 "
        "코드 이해도(Ownership)를 검증하는 Depth Ladder 7단계 질문을 생성하라. "
        "각 질문은 반례나 트레이드오프를 스스로 떠올리게 유도해야 하며, 단순 정답 확인 질문이면 안 된다. "
        "finding이 이미 관용 패턴으로 판정되어 강등된 경우(idiom_filtered=true)에는 "
        "질문 난이도를 낮추고 짧게 생성하라.\n\n"
        f"finding: {finding.get('finding')}\n"
        f"file: {finding.get('file')}\n"
        f"design_intent: {finding.get('design_intent')}\n"
        f"priority: {finding.get('priority')}\n"
        f"idiom_filtered: {finding.get('idiom_filtered', False)}\n"
    )


def _validate_depth_ladder(result):
    missing = [k for k in DEPTH_LADDER_KEYS if not result.get(k)]
    if missing:
        raise ValueError(f"7단계 중 누락된 필드: {missing}")
    return result


def _generate_via_anthropic(client, finding):
    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[DEPTH_LADDER_TOOL],
        tool_choice={"type": "tool", "name": "depth_ladder_questions"},
        messages=[{"role": "user", "content": build_prompt(finding)}],
    )
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "depth_ladder_questions":
            return _validate_depth_ladder(block.input)
    raise RuntimeError("tool_use 블록을 찾지 못함 — 모델이 스키마를 안 지킴")


def parse_nvidia_tool_response(response):
    """OpenAI 호환 tool_calls 응답에서 depth_ladder_questions 인자를 뽑아 검증한다.

    _generate_via_nvidia()와 smoke_test_nvidia_parsing.py가 공유하는 순수 함수 —
    네트워크 없이도(고정 응답 fixture로) 테스트 가능하도록 분리해뒀다.
    """
    choice = response["choices"][0]["message"]
    for call in choice.get("tool_calls") or []:
        if call["function"]["name"] == "depth_ladder_questions":
            result = json.loads(call["function"]["arguments"])
            return _validate_depth_ladder(result)
    raise RuntimeError(
        "tool_calls를 찾지 못함 — NVIDIA 모델이 이 요청에서 tool_choice를 지키지 않았을 수 있음. "
        f"content={choice.get('content')!r}"
    )


def _generate_via_nvidia(client, finding):
    response = client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": build_prompt(finding)}],
        tools=[_as_openai_tool(DEPTH_LADDER_TOOL)],
        tool_choice={"type": "function", "function": {"name": "depth_ladder_questions"}},
        max_tokens=1024,
        temperature=0.0,
    )
    return parse_nvidia_tool_response(response)


def generate_for_finding(client, finding):
    if PROVIDER == "nvidia":
        return _generate_via_nvidia(client, finding)
    return _generate_via_anthropic(client, finding)


def to_markdown(results_with_findings):
    lines = ["# 피드백 블록 자동 생성 결과 (Depth Ladder 7단계)\n"]
    labels = {
        "what": "What", "how": "How", "why": "Why", "alternative": "Alternative",
        "trade_off": "Trade-off", "constraint": "Constraint", "reflection": "Reflection",
    }
    for item in results_with_findings:
        lines.append(f"## {item['finding_id']}\n")
        if "error" in item:
            lines.append(f"> 생성 실패: {item['error']}\n")
            continue
        for i, key in enumerate(DEPTH_LADDER_KEYS, start=1):
            lines.append(f"{i}. **{labels[key]}** — {item['questions'][key]}")
        lines.append("")
    return "\n".join(lines)


def _build_client():
    if PROVIDER == "nvidia":
        if NvidiaRotatingClient is None:
            print("nvidia_client 모듈을 찾을 수 없습니다 (feedback/nvidia_client.py 확인).", file=sys.stderr)
            sys.exit(1)
        try:
            pool = NvidiaKeyPool.from_env()
        except ValueError as e:
            print(f"{e}", file=sys.stderr)
            print("FEEDBACK_PROVIDER=anthropic 으로 되돌리려면 그 환경변수를 export 하세요.", file=sys.stderr)
            sys.exit(1)
        return NvidiaRotatingClient(pool=pool)

    if anthropic is None:
        print("anthropic 패키지가 없습니다. `pip install anthropic` 실행 후 재시도하세요.", file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY 환경변수가 없습니다. 조용히 실패하지 않고 여기서 중단합니다.", file=sys.stderr)
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


def main():
    if len(sys.argv) < 2:
        print("usage: generate_questions.py <judgment_output.json> [top_n] [--md]", file=sys.stderr)
        sys.exit(1)

    client = _build_client()

    args = [a for a in sys.argv[1:] if a != "--md"]
    want_md = "--md" in sys.argv[1:]

    with open(args[0], encoding="utf-8") as f:
        data = json.load(f)
    top_n = int(args[1]) if len(args) > 1 else None

    findings = data["findings"]
    if top_n:
        findings = findings[:top_n]

    results = []
    for finding in findings:
        try:
            questions = generate_for_finding(client, finding)
            results.append({"finding_id": finding["id"], "questions": questions})
        except Exception as e:
            results.append({"finding_id": finding["id"], "error": str(e)})

    if want_md:
        print(to_markdown(results))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
