""" 도구 호출(tool-calling) 루프 -- CrewAI 없이 멀티라운드 tool use를 구현하는 재사용 모듈

D1 (2026-07-31): crewai 패키지 대신 이 모듈로 "도구를 쓰는 에이전트" 능력을 구현한다.
  WHY: OpenAI 호환 tool-calling 루프(메시지 배열에 tool 스키마를 실어 보내고,
    모델이 요청하는 tool_calls를 우리 코드가 직접 실행해 결과를 다시 메시지에
    append하는 방식)는 표준 패턴이고, SDK/프레임워크 없이 raw HTTP만으로 구현
    가능하다는 게 실제로 확인된 사실이다(OpenAI 쿡북/Temporal 문서 등). 우리
    app.engines.shared.llm.chat()은 이미 Phase 3부터 tools 파라미터와
    ChatResult.tool_calls를 갖고 있었다 -- 이 루프가 그 배선을 처음 실제로 쓴다.
    CallBudget.max_tool_rounds(D8, budget.py)도 지금까지 아무도 세지 않던 필드였는데
    여기서 처음 실제로 카운트한다.
  COST: crewai가 대신 해주던 재시도/파싱/메모리/계층적 위임 같은 편의 기능을 전혀
    안 가져온다 -- 이 루프가 하는 일은 딱 "요청 -> 도구 실행 -> 재요청" 반복과
    라운드 상한뿐이다. 더 정교한 기능이 필요해지면 우리가 직접 짜야 한다.
  EXIT: crewai의 메모리/계층적 위임 등이 실제로 필요해지는 스테이지가 생기면,
    그 스테이지만 requirements-codemap.txt(보관됨, D2 참고)를 설치해 crew.py
    패턴(과거 버전, git 이력에 남아 있음)으로 개별 예외를 두면 된다 -- 이 모듈이
    전체 서비스의 유일한 tool-use 경로가 되어야 한다는 강제는 없다.

D2: "멀티에이전트 협업"은 이 모듈에 별도 추상화를 두지 않는다. 여러 페르소나가
  순차로 협업하는 흐름은 이 함수(또는 shared.llm.chat())를 다른 system 메시지로
  여러 번 호출하고 이전 호출의 결과를 다음 호출의 입력에 섞어 넣으면 그게 전부다
  (poc-engine.js의 스테이지 체이닝과 analysis_doc.py가 이미 이 패턴이다) -- CrewAI의
  Agent/Task/Crew 클래스는 이 체이닝을 감싸는 편의 레이어였을 뿐, 체이닝 자체에
  프레임워크가 필요했던 적이 없다. 억지로 감쌀 추상화를 만들면 오히려 YAGNI 위반.

D3: 도구 실행은 항상 caller가 넘긴 tool_registry(이름 -> 로컬 함수) 안에서만
  일어난다 -- 이 모듈 자신은 어떤 파일도 열지 않고 어떤 프로세스도 실행하지
  않는다. crew.py의 D12(학생 코드에 파일 접근 도구를 주지 않음) 같은 안전 원칙은
  "어떤 도구를 registry에 넣을지"를 정하는 호출부의 책임으로 그대로 넘어간다 --
  이 모듈은 그 결정을 강제하지도, 우회하지도 않는다.

라운드 상한: budget.max_tool_rounds는 "총 LLM 호출 횟수"의 상한이다(도구 실행
자체는 라운드를 안 먹는다). 마지막 라운드에서도 모델이 여전히 tool_calls를
요청하면, 더 부르지 않고 그 라운드의 응답을 그대로 반환한다(도구 결과 없이
받은 응답이라 불완전할 수 있음 -- 호출자가 finish_reason/tool_calls로 판단).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, MutableSequence, Sequence

from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult, chat, classify_failure_code
from app.engines.shared.timing import LlmCallTimer
from app.schemas.usage import AiUsage

ToolFn = Callable[[Mapping[str, Any]], str]

_FAILED_RESULT = ChatResult(content="", finish_reason="ERROR", input_tokens=0, output_tokens=0, cached_tokens=0)


def _parse_tool_call(tool_call: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """ OpenAI 호환 tool_calls[i] -> (call_id, 도구 이름, 파싱된 인자 dict).
    arguments가 깨진 JSON이면(모델이 가끔 그런다) 빈 dict로 대체한다 -- 도구 함수가
    필수 인자 누락을 스스로 판단해 에러 문자열을 돌려주게 한다(이 함수가 죽지 않음). """
    call_id = tool_call.get("id", "")
    function = tool_call.get("function") or {}
    name = function.get("name", "")
    raw_args = function.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
    except (ValueError, TypeError):
        args = {}
    return call_id, name, args


def _run_tool(tool_registry: Mapping[str, ToolFn], name: str, args: dict[str, Any]) -> str:
    tool_fn = tool_registry.get(name)
    if tool_fn is None:
        return f"TOOL_NOT_FOUND: {name}"
    try:
        return tool_fn(args)
    except Exception as exc:  # noqa: BLE001 -- 도구 실행 실패가 루프 전체를 죽이면 안 됨(D6과 동일 철학)
        return f"TOOL_ERROR: {type(exc).__name__}: {exc}"


def run_tool_loop(
    *,
    model_code: str,
    messages: MutableSequence[dict[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    tool_registry: Mapping[str, ToolFn],
    max_tokens: int,
    budget: CallBudget,
    job_id: str,
    temperature: float = 0.0,
    json_mode: bool = False,
    chat_fn: Callable[..., ChatResult] = chat,
) -> tuple[ChatResult, list[AiUsage]]:
    """ 반환: (마지막 ChatResult, 라운드별 AiUsage 목록).

    messages는 호출자가 넘긴 리스트를 제자리에서(in-place) 계속 확장한다 --
    루프가 끝난 뒤에도 대화 전체(도구 호출/결과 포함)를 호출자가 들여다볼 수
    있게 하기 위함이다(디버깅/감사 목적. analysis_doc.py의 problems처럼 이
    내용이 그대로 최종 산출물에 노출되지는 않는다 -- 노출하려면 호출자가
    ground.py 스타일 검증을 별도로 거쳐야 한다).

    호출 실패(네트워크/타임아웃 등)는 그 라운드에서 즉시 멈추고 FAILED 기록과
    빈 ChatResult를 반환한다(D6과 동일 강등 철학 -- 예외를 올리지 않는다).
    """
    ai_usage: list[AiUsage] = []
    round_no = 0

    while True:
        round_no += 1
        timer = LlmCallTimer(
            budget.feature_code, model_code, source_type=budget.source_type, source_id=job_id, attempt_no=round_no,
        )
        try:
            with timer:
                result = chat_fn(
                    model_code=model_code, messages=list(messages), max_tokens=max_tokens,
                    temperature=temperature, json_mode=json_mode, tools=tools,
                    max_attempts=budget.max_attempts_per_call, timeout_s=budget.timeout_s,
                )
        except Exception as exc:  # noqa: BLE001 -- D6과 동일: 실패는 강등, 예외를 올리지 않는다
            ai_usage.append(timer.build(status="FAILED", failure_code=classify_failure_code(exc)))
            return _FAILED_RESULT, ai_usage

        ai_usage.append(timer.build(
            input_token_count=result.input_tokens,
            output_token_count=result.output_tokens,
            cached_token_count=result.cached_tokens,
            status="SUCCEEDED",
        ))

        if not result.tool_calls or round_no >= budget.max_tool_rounds:
            return result, ai_usage

        messages.append({"role": "assistant", "content": result.content, "tool_calls": list(result.tool_calls)})
        for tool_call in result.tool_calls:
            call_id, name, args = _parse_tool_call(tool_call)
            content = _run_tool(tool_registry, name, args)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
