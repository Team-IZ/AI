""" p04-3 문제 선정 + 그 뒤의 검증 2단계. poc-engine.js:104~137 포팅.

LLM이 고른 topic을 그대로 믿지 않는다. 두 가지를 확인하고 어긴 것은 버린다.
  ① teach_id가 실제로 준 teaches 안에 있고 서로 다른가
  ② code_ref의 symbol이 실제 파일에서 위치를 잡히는가

②를 여기서 안 걸러내면 질문·힌트 생성이 "근거 없음"인 채로 계속 돌아
LLM 호출만 태우고 세션에서 조용히 깨진다(PoC가 실제로 겪은 경로).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import fragments, stages

log = logging.getLogger(__name__)

# 🔴 매니페스트가 자기모순이다. "topics는 정확히 N개"와 "코드에 없는 주제는 고르지
# 마라"가 같이 있는데, 실측에서 **개수 강제가 이긴다** — 2026-08-03 실호출에서
# LangGraph 레포에 `Runner.run() 루프`를 물으려고 `builder.add_edge(...)`를 앵커로
# 끌어다 붙였다. 그러면 `unmatchedTeaches`가 빈 배열로 나가고 PM 확정
# "개념이 코드에 없으면 없다"가 조용히 무너진다.
#
# vendor를 고치지 않고 우리 소유 경로(`extra_user`)로 뒤집는다 — 사용자 메시지
# **끝에** 붙으므로 앞 규칙을 명시적으로 무효화한다고 적어야 효과가 있다.
_NO_PADDING = (
    "\n\n## 개수보다 우선하는 규칙\n"
    "앞의 \"topics는 정확히 N개\"는 **상한이지 목표가 아니다.** 근거를 못 찾은 teach는 "
    "빼고 그만큼 적게 반환하라 — 0개여도 된다.\n"
    "다음은 전부 금지다:\n"
    "- 개수를 맞추려고 관련 없는 코드를 앵커로 끌어다 붙이기\n"
    "- teach가 특정 라이브러리·SDK 전용 이름인데 학생이 다른 라이브러리를 쓴 경우, "
    "\"비슷한 역할\"을 하는 코드로 대체하기\n"
    "코드에 그 개념이 실제로 구현돼 있을 때만 topic을 만들어라. **없는 것은 없다고 "
    "두는 편이 낫다** — 억지로 만든 문제는 학생이 쓰지도 않은 개념을 묻게 된다."
)


@dataclass
class Selection:
    topics: list[dict[str, Any]]          # 검증 통과분. code_ref에 산정된 줄 번호가 들어 있다
    usages: list[dict[str, Any]]
    dropped: list[dict[str, str]] = field(default_factory=list)   # [{title, reason}]
    # 문항을 못 만든 teach. **`problems`에 없는 teachId를 백엔드가 역산하지 않도록**
    # 명시적으로 들고 나온다 — 화면의 `―`(문항 없음)이 이 값으로 그려진다.
    unmatched: list[dict[str, str]] = field(default_factory=list)  # [{teach_id, reason}]
    budget: int = 3

    @property
    def shortfall(self) -> int:
        """요청 개수에 못 미친 수. 0이면 정상.

        0이 아니어도 실패가 아니다 — 억지로 채우면 물을 거리가 없는 문제가 섞인다.
        Spring에는 questionCountPlanned와 problems 길이 차이로 드러난다.
        """
        return self.budget - len(self.topics)


# `Runner.run` 처럼 **점으로 이어진 식별자**만 잡는다. 이런 이름은 특정 SDK·라이브러리
# 고유의 API라 다른 말로 바꿔 쓸 수 없다 — 코드에 그 문자열이 없으면 그 개념은 없다.
_API_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")

# 문항을 못 만든 사유의 기본 문장. **교육생 화면에 그대로 뜬다** — 백엔드가
# `assessment_problem`에 저장한다(2026-08-04 확정). 내부 진단 용어를 쓰지 않는다.
NOT_FOUND_REASON = "제출 코드에서 이 개념에 해당하는 부분을 찾지 못했습니다"


def _missing_api_token(teach: dict[str, Any], files: dict[str, str]) -> str | None:
    """teach 이름의 API 식별자가 코드에 아예 없으면 그 이름을 돌려준다.

    🔴 **LLM에게 "없으면 빼라"를 부탁하는 것으로는 안 막힌다** (2026-08-03 실측:
    프롬프트를 두 번 강화했는데 두 번 다 개수를 채웠다). LangGraph 레포에
    `Agents SDK의 Runner.run() 루프`를 물으려고 `builder.set_entry_point(...)`를
    앵커로 끌어다 붙였다 — 학생이 쓰지도 않은 개념을 묻게 된다.

    **점 있는 이름만 본다.** `매니저 패턴`은 코드에서 `Supervisor`로 나타날 수 있어
    글자로 막으면 안 되지만, `Runner.run`은 그렇게 나타날 방법이 없다. 좁게 잡는 대신
    걸리면 확실하다.
    """
    label = f"{teach.get('label', '')} {teach.get('id', '')}"
    tokens = {m.group(0) for m in _API_TOKEN.finditer(label)}
    haystack = "\n".join(files.values())
    for token in sorted(tokens):
        if token not in haystack:
            return token
    return None


# 모델이 돌려준 teach_id를 실제 id로 되돌린다. p04-1도 같은 문제를 겪어
# `stages`에 두고 함께 쓴다 — 두 곳에서 따로 고치면 한쪽만 낡는다.
_resolve_teach_id = stages.resolve_choice


def select(files: dict[str, str], teaches: list[dict[str, Any]],
           analysis_document: dict[str, Any], candidates: list[dict[str, Any]],
           *, model_code: str, fallback_model_code: str | None = None,
           question_budget: int = 3) -> Selection:
    """문제 후보를 골라 검증까지 마친 목록을 돌려준다.

    한 topic이 한 teach를 독점하므로 **teaches가 question_budget보다 적으면 문제도
    그만큼만 나온다.** 교안 분석이 teach를 적게 뽑으면 문제 수가 조용히 줄어든다.

    룰 후보(candidates)는 선택지가 아니라 맥락이다 — 매니페스트가 code_ref.file을
    "분석 문서에 등장한 파일"로 제약하고, 환각 방지는 아래 symbol 검증이 담당한다.
    """
    # 🔴 **물어볼 수 없는 teach는 LLM에게 보여주지도 않는다.** 목록에 남겨 두면
    # 개수를 채우려고 엉뚱한 코드를 앵커로 끌어다 붙인다(실측 2회 재현).
    askable, blocked = [], []
    for teach in teaches:
        token = _missing_api_token(teach, files)
        if token:
            blocked.append({"teach_id": teach.get("id") or "",
                            "reason": f"제출 코드에 `{token}`이(가) 없습니다"})
            log.info("p04-3 사전 제외: %s (%s 없음)", teach.get("id"), token)
        else:
            askable.append(teach)
    teaches = askable

    stage = stages.get_stage("p04-3")
    values = {
        "teaches_block": "\n".join(f"- {t.get('id')}: {t.get('label', '')}" for t in teaches),
        "analysis_block": json.dumps(analysis_document, ensure_ascii=False),
        "findings_block": json.dumps(candidates, ensure_ascii=False),
        # 물어볼 수 있는 teach 수를 넘겨준다 — 사전 제외분까지 세면 또 채우려 든다.
        "question_count": min(question_budget, len(teaches)),
    }
    result = stages.call("p04-3", values, model_code=model_code,
                         fallback_model_code=fallback_model_code, extra_user=_NO_PADDING)

    raw = result.data.get("topics")
    topics = raw if isinstance(raw, list) else []
    dropped: list[dict[str, str]] = []

    # 검증 ①: 존재하는 teach여야 하고 서로 달라야 한다.
    # 없는 teach를 참조하는 문제는 만들 수 없고, 같은 teach를 두 번 물으면 검증 축이 겹친다.
    teach_ids = {t.get("id") for t in teaches}
    seen: set[str] = set()
    kept = []
    for t in topics:
        tid = _resolve_teach_id(t.get("teach_id"), teach_ids)
        t["teach_id"] = tid
        if tid not in teach_ids:
            dropped.append({"title": t.get("title", ""), "reason": f"없는 teach: {tid}"})
            continue
        if tid in seen:
            dropped.append({"title": t.get("title", ""), "reason": f"teach 중복: {tid}"})
            continue
        seen.add(tid)
        kept.append(t)

    # 검증 ②: symbol이 실제 파일에서 잡혀야 한다. 산정된 줄 번호를 code_ref에 되먹여
    # 이후 단계가 symbol을 다시 찾지 않고 "산정된 사실"만 쓰게 한다.
    verified, failed = _locate_all(files, kept, dropped)

    # 🔴 **재시도 1회** (2026-08-03 PM 결정: "일단 최대한 teaches에 부합하는 거 찾아보고
    # 그래도 없으면 없다고 박아라"). 개념이 코드에 **있는데 LLM이 엉뚱한 symbol을 지목**한
    # 경우가 있다 — 한 번에 버리면 있는 개념을 없다고 박게 된다.
    #
    # 실패한 teach만 모아 다시 묻는다. 전부 성공하면 이 호출은 아예 없다.
    if failed:
        retried = _relocate(files, teaches, analysis_document, candidates, failed,
                            model_code=model_code, fallback_model_code=fallback_model_code)
        if retried is not None:
            result.usages.extend(retried.usages)
            more, _ = _locate_all(files, retried.topics, dropped)
            verified.extend(more)

    picked = verified[:question_budget]

    # 요청받은 teach 중 문항이 안 나온 것. 지어내지 않고 "없음"으로 남긴다
    # (2026-08-03 PM 결정). 사유는 화면에 그대로 띄울 수 있는 한 문장으로 만든다.
    matched = {t.get("teach_id") for t in picked}
    # 🔴 **`dropped`의 사유를 여기로 흘리지 않는다.** 백엔드가 이 문장을
    # `assessment_problem`에 저장해 **교육생 화면에 그대로 띄운다**(2026-08-04 확정).
    # `dropped`는 우리 진단용이라 화면에 낼 물건이 아니다 —
    # `코드에서 찾을 수 없음: "worker = state.get(...\\))"`는 **모델이 잘못 인용한 원문**을
    # 학생에게 보여주는 것이고, `코드가 아니라 문자열·주석을 가리킵니다`는 학생이 아니라
    # 모델 얘기다. 진단은 로그에 남는다(아래 log.warning).
    #
    # 사전 제외분(blocked)만 구체적인 사유를 갖는다 — "코드에 그 API가 없다"는
    # 결정론적으로 확인한 사실이라 화면에 내도 된다.
    unmatched = blocked + [
        {"teach_id": t["id"], "reason": NOT_FOUND_REASON}
        for t in teaches[:question_budget] if t.get("id") and t["id"] not in matched
    ]

    # 🔴 **버린 이유는 로그에만 남는다.** 응답의 unmatched는 화면에 띄울 한 문장이라
    # "없는 teach: X" 같은 내부 사유를 못 싣는다 — 그런데 문제가 0개로 나왔을 때
    # 원인이 ①(teach_id 불일치)인지 ②(symbol 못 찾음)인지가 여기서만 갈린다.
    if dropped:
        log.warning("p04-3 버림 %d건: %s", len(dropped), dropped)
    if not picked:
        log.warning("p04-3 문제 0개. LLM이 낸 topic %d건, teach_id %s",
                    len(topics), [t.get("teach_id") for t in topics])

    return Selection(topics=picked, usages=result.usages, dropped=dropped,
                     unmatched=unmatched, budget=question_budget)
    
def _is_prose(snippet: str) -> bool:
    """앵커가 코드가 아니라 산문(프롬프트 문자열·주석)인가.

    🔴 **문자열 리터럴에는 설계 판단이 없다.** 2026-08-03 반복 실행에서 모델이
    프롬프트 한복판을 앵커로 잡았다 — `역할: 당신은 제공받은 PPT 슬라이드...`.
    그러면 L2("왜 이렇게 했나")·L4("언제 깨지나")가 물을 대상 자체를 잃는다.

    판별은 **비ASCII 비율**로 한다. 코드 줄은 식별자·연산자라 거의 ASCII이고,
    한국어가 절반을 넘으면 주석이거나 문자열 본문이다. 언어에 안 묶이는 신호라
    파서 없이 쓸 수 있다 — 다만 한국어 문서에만 유효한 휴리스틱이다.
    """
    text = (snippet or "").strip()
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return non_ascii / len(text) > 0.5


def _locate_all(files: dict[str, str], topics: list[dict[str, Any]],
                dropped: list[dict[str, str]]) -> tuple[list[dict[str, Any]],
                                                        list[dict[str, Any]]]:
    """symbol을 실제 파일에서 잡아 code_ref에 줄 번호를 되먹인다.

    돌려주는 것은 (검증 통과, 위치를 못 잡은 것). 못 잡은 것도 `dropped`에 사유가
    남는다 — 재시도가 실패하면 그 사유가 최종 기록이다.
    """
    verified: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for topic in topics:
        ref = topic.get("code_ref") or {}
        located = fragments.extract_fragment(files, ref.get("file"), ref.get("symbol", ""))
        if not located["valid"]:
            dropped.append({"title": topic.get("title", ""), "reason": located["reason"],
                            "teach_id": topic.get("teach_id") or ""})
            failed.append(topic)
            continue
        if _is_prose(located.get("snippet", "")):
            dropped.append({"title": topic.get("title", ""),
                            "reason": "코드가 아니라 문자열·주석을 가리킵니다",
                            "teach_id": topic.get("teach_id") or ""})
            failed.append(topic)
            continue
        topic["code_ref"] = {
            "file": located["file"],
            "line_start": located["line_start"],
            "line_end": located["line_end"],
            "snippet": located["snippet"],
        }
        verified.append(topic)
    return verified, failed


def _relocate(files: dict[str, str], teaches: list[dict[str, Any]],
              analysis_document: dict[str, Any], candidates: list[dict[str, Any]],
              failed: list[dict[str, Any]], *, model_code: str,
              fallback_model_code: str | None = None):
    """위치를 못 잡은 teach만 모아 p04-3을 한 번 더 부른다.

    **개념이 코드에 있는데 LLM이 엉뚱한 symbol을 지목한 경우를 구제한다.**
    한 번에 버리면 있는 개념을 "없음"으로 박게 되고, 그건 오퍼레이터가 고른 개념을
    조용히 빼는 것이다(2026-08-03 PM: "최대한 찾아보고 그래도 없으면 없다고 박아라").

    **한 번만 한다.** 두 번째도 못 찾으면 실제로 코드에 없을 가능성이 훨씬 높고,
    LLM 콜을 더 태울 값어치가 없다. 실패하면 조용히 None을 돌려준다 — 재시도가
    깨져서 1차 결과까지 잃으면 안 된다.
    """
    failed_ids = {t.get("teach_id") for t in failed}
    subset = [t for t in teaches if t.get("id") in failed_ids]
    if not subset:
        return None

    values = {
        "teaches_block": "\n".join(
            f"- {t.get('id')}: {t.get('label', '')}" for t in subset),
        "analysis_block": json.dumps(analysis_document, ensure_ascii=False),
        "findings_block": json.dumps(candidates, ensure_ascii=False),
        "question_count": len(subset),
    }
    missed = "\n".join(
        f"- {t.get('teach_id')}: {(t.get('code_ref') or {}).get('symbol', '')!r}"
        for t in failed
    )
    hint = (
        "\n\n## 재시도\n"
        "앞선 시도에서 아래 symbol을 코드에서 찾지 못했다. **파일에 실제로 존재하는 "
        "선언·호출 문자열을 그대로** code_ref.symbol에 써라 — 요약하거나 다시 쓰지 마라.\n"
        f"{missed}\n\n"
        "해당 개념이 코드에 실제로 없으면 그 teach는 topics에서 빼라. "
        "지어내지 마라 — 없는 것은 없다고 두는 편이 낫다."
    )
    try:
        result = stages.call("p04-3", values, model_code=model_code,
                             fallback_model_code=fallback_model_code, extra_user=hint)
    except stages.StageError as exc:
        # 재시도가 깨져도 콜은 나갔다. **원장을 버리면 "왜 이 토큰을 썼나"가 사라지고,
        # 콜 수만 보고 "재시도가 안 돌았다"고 오독하게 된다.**
        return Selection(topics=[], usages=exc.usages, budget=len(subset))

    raw = result.data.get("topics")
    topics = raw if isinstance(raw, list) else []
    # 재시도가 엉뚱한 teach를 들고 오면 무시한다. 1차에서 이미 성공한 것을 덮어쓰면 안 된다.
    topics = [t for t in topics if t.get("teach_id") in failed_ids]
    return Selection(topics=topics, usages=result.usages, budget=len(subset))
