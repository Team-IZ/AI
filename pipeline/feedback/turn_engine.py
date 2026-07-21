# D87: 스펙 04시트 턴 상태기계를 실제로 구현 -- D48/D50의 "규칙기반 적응형"을
#   D48 EXIT가 이미 예고한 대로 LLM 생성형으로 업그레이드
#   WHY: D86가 발견한 갭 -- 지금까지의 모든 Track A 벤치마크(generate_questions.py의
#        DEPTH_LADDER_TOOL)는 학생 답변 없이 finding 메타데이터만 보고 7필드를 한 번에
#        생성하는 도구였다. 스펙이 요구하는 건 이것과 다르다: Decision Point 하나당
#        "① 코드종속 질문(파일·함수명 삽입) → ② 답변평가 → ③ 표면/부분/방어 분류 →
#        ④ 방어 아니면 L2(트레이드오프) 반례, 실패시 L3(극단시나리오) → ⑤ L3도 실패시
#        Self Reflection 유도+확인질문 → ⑥ depth/시간 상한 또는 방어 성공시 다음 DP"
#        라는, 학생 답변에 실시간으로 반응하는 최소 1턴~최대 4턴 적응형 루프다.
#        pipeline/followup_generator.py(D48)가 이미 답변 분류+분기까지는 실제로 하지만
#        (isolation_classifier/reflection_signal, 둘 다 confirmed 정규식 매칭, D6 계보),
#        분기마다 나오는 질문은 사전 작성된 템플릿 5개 중 하나이지 LLM이 그 자리에서
#        새로 생성한 게 아니고, L2→L3→reflection으로 이어지는 다단계 에스컬레이션
#        자체가 아예 없다(follow-up 1단계뿐). D48 EXIT: "API가 생기면 이 규칙 트리의
#        각 분기점에서 Codex/Claude를 호출해 방향은 같지만 문구를 매번 새로 생성하도록
#        교체 가능(분기 로직은 그대로 재사용)" -- API는 D56/D58부터 이미 살아있었는데
#        이 업그레이드는 한 번도 실행된 적이 없었다. 이 모듈이 그 업그레이드다.
#   COST: 분류(표면/부분/방어) 자체는 여전히 정규식 기반이다(D48 EXIT가 "분기 로직은
#        그대로 재사용"이라고 명시했으므로 의도적으로 안 바꿈) -- LLM이 답변을 직접
#        판단하는 게 아니라, confirmed 패턴 매칭 결과를 3단계로 재해석만 한다. 이
#        분류가 실제 학생 답변의 미묘한 차이를 놓치면(예: confirmed 패턴 사전이 아직
#        얕으면) 에스컬레이션이 부정확하게 일어날 수 있다 -- 이건 이미 D34/D37에서
#        문서화된 정규식 채점기의 알려진 한계를 그대로 물려받는다.
#   EXIT: 분류까지 LLM 판단으로 바꾸고 싶으면 classify_answer()의 내부만 교체하면
#        되고(run_decision_point의 나머지 흐름은 안 바뀜), 그러면 D48이 명시적으로
#        보존하려 한 "결정론적/감사가능한 분기"라는 성질을 잃는다는 트레이드오프가 있음.
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "judgment"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline"))

from isolation_classifier import classify_justification  # noqa: E402
from reflection_signal import evaluate_reflection  # noqa: E402
from evidence_bridge import finding_category  # noqa: E402

try:
    # D104: centralized max_tokens (repo root, same pattern as nvidia_client.py's
    # DEFAULT_TIMEOUT_S import -- falls back to a literal if this file is ever
    # used somewhere without timeout_config.py on the path).
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from timeout_config import DEFAULT_MAX_TOKENS
except ImportError:
    DEFAULT_MAX_TOKENS = 2048
from idiom_filter import _find_file_content  # noqa: E402
from generate_questions import _as_openai_tool  # noqa: E402

RISK_TYPE_CATEGORIES = ("tier-b-risk", "repeated-pattern", "architecture-diffusion")
MAX_FILE_CHARS = 4000  # 프롬프트 비대화 방지 -- 파일이 이보다 길면 앞부분만 사용

LEVELS = ("l1", "l2", "l3", "reflection")


def fetch_code_context(finding, repo_root):
    """finding.file의 실제 소스를 읽어온다. 없으면 None(질문 생성 시 파일명만으로 대체)."""
    filename = finding.get("file")
    if not filename or not repo_root:
        return None
    content = _find_file_content(repo_root, filename)
    if content is None:
        return None
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + "\n... (이하 생략)"
    return content


def classify_answer(category, answer_text, level="l1"):
    """confirmed 정규식 분류기 출력을 표면/부분/방어 3단계로 재해석한다(D87, 새 매핑).

    cognition-isolation: classify_justification()의 matched_categories 개수 기준
      0개 → 표면 / 1개 → 부분 / 2개 이상 → 방어
      (이미 pipeline/followup_generator.py의 _isolation_followup이 쓰던 것과 동일한
      경계값 -- 거기선 분기만 했지 이름을 안 붙였을 뿐, 새로 발명한 임계값이 아니다)

    tier-b-risk/repeated-pattern/architecture-diffusion: evaluate_reflection()의
    서브신호를 쓰되, level에 따라 다르게 해석한다(D87-fix, 스모크테스트로 발견):
      - level="reflection": self_error_recognition을 필수로 요구하는 원래 의미가
        정확히 맞는 자리다(반성 단계 = "스스로 오류를 인정했는가"). required_ok=False
        → 표면 / True인데 optional<2 → 부분 / optional>=2 → 방어.
      - level="l1"/"l2"/"l3": self_error_recognition을 필수로 두면 안 된다 --
        아직 반례 도전을 안 받은 학생(또는 L1/L2 단계 학생)이 "내가 틀렸었다"고
        말할 이유가 없는데, 이 신호가 없다고 무조건 "표면"으로 깔면 아무리 근거있고
        구체적인 답변도 방어 판정을 못 받는다(실측: 잘 쓴 강한 답변이 "표면"으로
        나옴 -- self_error_recognition 요구가 원인이었음을 직접 확인). 그래서 이
        단계에서는 optional 3개(reason_explanation/concrete_improvement/
        new_judgment, 셋 다 "구체성·근거·판단력"을 재는 신호라 L1 답변 품질 판정에도
        그대로 쓸 수 있음)만으로 판정: 0개 → 표면 / 1개 → 부분 / 2개 이상 → 방어.
    """
    if category == "cognition-isolation":
        result = classify_justification(answer_text)
        n = len(result["matched_categories"])
        verdict = "surface" if n == 0 else ("partial" if n == 1 else "defended")
        return {"verdict": verdict, "raw": result}

    result = evaluate_reflection(answer_text)
    if level == "reflection":
        if not result["required_ok"]:
            verdict = "surface"
        elif result["optional_matches"] < result["min_optional_required"]:
            verdict = "partial"
        else:
            verdict = "defended"
    else:
        n = result["optional_matches"]
        verdict = "surface" if n == 0 else ("partial" if n == 1 else "defended")
    return {"verdict": verdict, "raw": result}


def _transcript_text(transcript):
    lines = []
    for turn in transcript:
        lines.append(f"[{turn['level'].upper()}] 질문: {turn['question']}")
        lines.append(f"[{turn['level'].upper()}] 학생 답변: {turn['answer']}")
    return "\n".join(lines)


SINGLE_QUESTION_TOOL = {
    "name": "ask_question",
    "description": "학생에게 던질 질문 하나를 생성한다.",
    "input_schema": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "학생에게 던질 질문 1개"}},
        "required": ["question"],
    },
}


def _build_level_prompt(level, finding, code_context, transcript, classification, extra_banned=None):
    code_block = f"\n## 실제 코드\n```\n{code_context}\n```\n" if code_context else ""
    header = (
        "당신은 학생/지원자의 코드 이해도(Ownership)를 검증하는 면접관입니다. "
        "반드시 실제 코드나 finding 내용을 근거로 질문하세요 — 일반론적 질문 금지.\n\n"
        f"## Decision Point\n{finding.get('finding')}\n"
        f"## 파일\n{finding.get('file')}\n"
        f"{code_block}"
    )
    if level == "l1":
        return header + (
            "\n지금 첫 질문을 던지는 단계입니다. 이 파일/코드에서 실제로 판단이 개입된 "
            "지점(왜 이렇게 설계했는가)을 구체적인 파일명·함수명·코드 라인을 인용해 "
            "질문하세요. 일반적인 질문이 아니라 이 코드에 종속된 질문이어야 합니다."
        )

    prior = _transcript_text(transcript)
    verdict_note = {
        "surface": "표면적(근거·구체성 부족)",
        "partial": "부분적(일부 근거는 있으나 아직 충분히 깊지 않음)",
    }.get(classification["verdict"], classification["verdict"])

    if level == "l2":
        prompt = header + (
            f"\n## 이미 물어본 질문 — 아래를 반복하면 실패입니다\n{prior}\n\n"
            f"위에서 학생의 방금 답변이 {verdict_note}으로 판정됐습니다.\n\n"
            "지금은 2단계(트레이드오프 반례) 질문 차례입니다. 반드시 아래 규칙을 지키세요:\n"
            "1. 위 '이미 물어본 질문'과 주제·문장이 겹치는 질문은 절대 금지 — 표현만 바꾼 "
            "재질문도 금지입니다. 완전히 다른 각도여야 합니다.\n"
            "2. 앞선 질문은 '왜 이렇게 설계했는가'를 물었습니다. 이번엔 그 설계 판단이 깨지는 "
            "구체적 반례나 대안 설계를 당신이 먼저 제시하고, 학생이 그것과 비교/방어하도록 "
            "압박하세요.\n"
            "3. 방금 답변에서 부족했던 근거·구체성을 정확히 겨냥하세요.\n\n"
            "이 규칙을 지켜, 앞 질문과 겹치지 않는 새로운 반례 질문 하나만 생성하세요."
        )
    elif level == "l3":
        prompt = header + (
            f"\n## 이미 물어본 질문 — 아래를 반복하면 실패입니다\n{prior}\n\n"
            f"트레이드오프 반례에도 학생 답변이 {verdict_note}으로 판정됐습니다. 이번엔 "
            "훨씬 더 극단적인 시나리오(예: 규모가 100배 커지거나, 악의적 공격자가 있거나, "
            "완전히 다른 제약이 주어지는 상황)를 제시해서 이 설계 판단이 정말로 "
            "견고한지 시험하는 질문을 던지세요. 위 '이미 물어본 질문'과 겹치지 않는 새로운 "
            "질문이어야 합니다."
        )
    else:  # reflection
        prompt = header + (
            f"\n## 이미 물어본 질문 — 아래를 반복하면 실패입니다\n{prior}\n\n"
            "극단 시나리오에도 학생이 방어하지 못했습니다. 이제 학생이 스스로 오류를 "
            "인정하고 개선안을 생각해보도록 유도하는 질문을 던지세요 — 직접 정답을 알려주지 "
            "말고, 학생이 스스로 '이 부분은 이렇게 바꿨어야 했다'는 결론에 도달하도록 "
            "유도하는 확인 질문 하나만 던지세요. 위 '이미 물어본 질문'과 겹치지 않는 새로운 "
            "질문이어야 합니다."
        )

    # D190: only non-empty on a dedup-triggered retry within the SAME level -- see
    # generate_question(). A rejected same-level attempt never makes it into `transcript`
    # (that only happens after the turn's answer comes back), so it has to be listed here.
    if extra_banned:
        banned_lines = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(extra_banned))
        prompt += f"\n\n## 방금 생성했으나 반려된 질문 (이전 질문과 겹침 감지됨) — 이것과도 겹치면 안 됩니다\n{banned_lines}"
    return prompt


def _parse_ask_question_response(response):
    """SINGLE_QUESTION_TOOL(ask_question) 전용 파서 -- generate_questions.py의
    parse_nvidia_tool_response()는 "depth_ladder_questions" 툴 이름에 하드코딩돼
    있어 재사용 불가(실측: 스모크테스트에서 항상 RuntimeError, 실제로는 모델이
    ask_question을 정상 호출했는데 파서가 다른 이름만 찾고 있었음)."""
    choice = response["choices"][0]["message"]
    for call in choice.get("tool_calls") or []:
        if call["function"]["name"] == "ask_question":
            result = json.loads(call["function"]["arguments"])
            if not result.get("question"):
                raise RuntimeError(f"question 필드가 비어있음: {result!r}")
            return result
    raise RuntimeError(
        "tool_calls에서 ask_question을 찾지 못함 — 모델이 tool_choice를 안 지켰을 수 있음. "
        f"content={choice.get('content')!r}"
    )


# D190: JS 자매 파일(docs/lab/p03-engine.js)과 동일한 재발 대응 -- D188은 프롬프트
# 텍스트만 고쳤는데, 사용자가 실사용 중 다른 finding(step-3.5-flash, load_api_keys)에서
# L2==Reflection 중복을 다시 목격. 확률적 생성인 이상 프롬프트만으로는 특정 모델x특정
# finding 조합의 재발을 완전히 막을 보장이 없다는 게 재실측 확인됨 -- 전체 WHY/COST/
# EXIT는 p03-engine.js의 동일 주석 참고(임계값 0.5는 이번 재발 실측: byte-identical
# 중복=1.0, 같은 스크린샷 안 정상 L2-vs-L3 쌍=0.20, D188 기존 데이터=0.92/0.06~0.22와
# 일관).
DEDUP_JACCARD_THRESHOLD = 0.5
DEDUP_MAX_RETRIES = 2


def _normalize_for_dedup(s):
    return " ".join((s or "").split())


def _char_ngrams(s, n=3):
    s = _normalize_for_dedup(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else set()


def _ngram_jaccard(a, b, n=3):
    na, nb = _char_ngrams(a, n), _char_ngrams(b, n)
    if not na or not nb:
        return 1.0 if _normalize_for_dedup(a) == _normalize_for_dedup(b) else 0.0
    intersection = len(na & nb)
    union = len(na | nb)
    return intersection / union if union else 0.0


def generate_question(level, finding, code_context, transcript, classification, client, model,
                       max_tokens=DEFAULT_MAX_TOKENS, on_retry=None):
    """on_retry(msg: str): 선택적 콜백 -- 중복 감지로 재생성할 때만 호출(기존 호출부는
    안 건드려도 됨, 기본값 None이면 조용히 재시도만 함)."""
    prior_questions = [t["question"] for t in transcript]
    rejected = []
    total_elapsed = 0.0
    tool = _as_openai_tool(SINGLE_QUESTION_TOOL)
    for attempt in range(DEDUP_MAX_RETRIES + 1):
        prompt = _build_level_prompt(level, finding, code_context, transcript, classification, extra_banned=rejected)
        t0 = time.time()
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": "ask_question"}},
            max_tokens=max_tokens,
            temperature=0.0,
        )
        total_elapsed += time.time() - t0
        result = _parse_ask_question_response(response)
        question = result["question"]
        dup_of = next((q for q in prior_questions if _ngram_jaccard(question, q) >= DEDUP_JACCARD_THRESHOLD), None)
        if not dup_of:
            return question, total_elapsed
        if attempt == DEDUP_MAX_RETRIES:
            if on_retry:
                on_retry(f"{level} 질문이 이전 질문과 유사해 보이지만 재생성 한도({DEDUP_MAX_RETRIES}회) 소진 — 그대로 진행")
            return question, total_elapsed
        if on_retry:
            on_retry(f"{level} 질문이 이전 질문과 겹쳐 재생성 중 ({attempt + 1}/{DEDUP_MAX_RETRIES})")
        rejected.append(question)


def run_decision_point(finding, repo_root, answer_fn, client, model, max_turns=4, max_tokens=DEFAULT_MAX_TOKENS, on_retry=None):
    """스펙 04시트의 6단계 흐름을 실행한다.

    answer_fn(question: str, level: str) -> str 는 실제 세션이면 학생에게 묻는 함수,
    벤치마크면 미리 준비된 답변을 순서대로 꺼내주는 함수로 교체 가능한 지점(시임)이다.

    max_tokens: generate_question()에 그대로 전달. D94b에서 선택 인자로 추가(당시 기본
    512 유지), D104에서 기본값을 중앙 설정 DEFAULT_MAX_TOKENS(2048)로 승격 -- 512는
    nemotron(내부 reasoning이 예산 소진, content:null, D97)과 mistral-large-3(tool
    arguments JSON이 캡에 잘려 unterminated, D103) 두 모델을 서로 다른 방식으로
    깨뜨렸다는 게 실측 확인됨. 모델 유형(reasoning 여부)으로 캡을 추정하지 말 것.
    """
    category = finding_category(finding["id"])
    code_context = fetch_code_context(finding, repo_root)

    transcript = []
    total_elapsed = 0.0
    for i, level in enumerate(LEVELS):
        if i >= max_turns:
            return {"verdict": "exhausted_at_cap", "turns": len(transcript),
                    "transcript": transcript, "elapsed_s": round(total_elapsed, 2)}

        classification = transcript[-1]["classification"] if transcript else None
        question, elapsed = generate_question(level, finding, code_context, transcript, classification, client, model, max_tokens=max_tokens, on_retry=on_retry)
        total_elapsed += elapsed
        answer = answer_fn(question, level)
        classification = classify_answer(category, answer, level=level)

        transcript.append({
            "level": level, "question": question, "answer": answer,
            "classification": classification,
        })

        if classification["verdict"] == "defended":
            return {"verdict": "defended", "turns": len(transcript),
                    "transcript": transcript, "elapsed_s": round(total_elapsed, 2)}

    return {"verdict": "exhausted_at_cap", "turns": len(transcript),
            "transcript": transcript, "elapsed_s": round(total_elapsed, 2)}
