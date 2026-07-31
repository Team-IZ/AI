""" 구조도(Mermaid flowchart) 생성 -- p05-4, agent_loop.run_tool_loop()의 첫 실사용처

D1 (2026-07-31, 데모): analysis_doc.py(p05-3)가 뽑은 structure[]를 Mermaid flowchart로
  시각화한다. crew.py/analysis_doc.py는 둘 다 도구 없이 단일 호출이었는데, 이 스테이지가
  agent_loop.run_tool_loop()을 실제로 처음 쓴다 -- mermaid_syntax_lookup 도구 하나를 주고,
  모델이 문법이 불확실하면 스스로 호출하게 한다(안 불러도 정상 -- 루프는 tool_calls가
  없으면 그대로 끝난다).
  WHY: nvidia-demo 원본의 writer 에이전트가 WebsiteSearchTool(mermaid.js 라이브 크롤링+
    임베딩 검색)로 하던 일과 같은 목적이지만, 여기서는 정적 치트시트로 대체한다 -- 이
    저장소엔 임베딩/벡터스토어 인프라가 없고(analysis_doc.py도 마찬가지), 데모 목적상
    라이브 웹 크롤링을 새로 앓을 이유가 없다.
  COST: mermaid.js 문법이 실제로 바뀌면 치트시트가 낡는다(라이브 크롤링이었으면 안 낡음).
    커버리지도 flowchart/classDiagram 둘뿐 -- 코드 구조도에 필요한 최소 범위만.
  EXIT: 라이브 조회가 필요해지면 mermaid_syntax_lookup() 내부만 httpx 호출로 바꾸면 된다
    (llm.py::_default_transport와 같은 패턴 -- SDK 불필요, D1 agent_loop.py 참고).

D2: 정확성 검증은 구조적 검사(check_mermaid_syntax)뿐이다 -- 실제 mermaid-cli 렌더링
  검증은 안 한다(데모 범위 밖). 검증 실패 시 다이어그램 없이 강등한다(D6과 동일 철학) --
  틀린 다이어그램을 문서에 끼워 넣느니 아예 빼는 게 낫다.

D3: 백엔드 스키마 변경이 필요 없다 -- analysis_document_markdown은 이미 자유 텍스트라
  engine.py가 ```mermaid 펜스 블록을 그 안에 이어붙이기만 하면 된다(별도 필드 신설 없음).
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.engines.codemap.models import AnalysisDoc, StructureArea
from app.engines.shared.agent_loop import run_tool_loop
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import chat
from app.engines.shared.prompts import load_stage, param_default, render
from app.schemas.usage import AiUsage

# D1: mermaid.js를 라이브로 안 크롤링하는 대신 두는 정적 치트시트. 코드 구조도에
# 실제로 쓰이는 두 종류만(flowchart/classDiagram) -- sequenceDiagram 등은 이 스테이지의
# 입력(structure[]는 정적 영역 목록이지 시간순 상호작용이 아님)과 안 맞아 뺐다.
MERMAID_CHEATSHEETS: dict[str, str] = {
    "flowchart": (
        "flowchart TD\n"
        "  A[노드 이름] --> B[다른 노드]\n"
        "  A --> C{조건 노드}\n"
        "  C -->|예| D[결과1]\n"
        "  C -->|아니오| E[결과2]\n"
        "방향: TD(위->아래)/LR(왼쪽->오른쪽). 노드 id는 공백 없이, 표시 이름은 대괄호 안에."
    ),
    "classdiagram": (
        "classDiagram\n"
        "  ClassA --> ClassB : uses\n"
        "  ClassA : +method()\n"
        "  ClassA : -field\n"
        "관계 화살표: --> (연관), --|> (상속), --* (구성)."
    ),
}

MERMAID_TOOL_SCHEMA: tuple[Mapping[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "mermaid_syntax_lookup",
            "description": "Look up Mermaid diagram syntax by diagram type when unsure of exact syntax.",
            "parameters": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "enum": ["flowchart", "classDiagram"],
                        "description": "The Mermaid diagram type to look up.",
                    },
                },
                "required": ["diagram_type"],
            },
        },
    },
)


def mermaid_syntax_lookup(args: Mapping[str, Any]) -> str:
    """ agent_loop.ToolFn 시그니처: args dict -> 결과 문자열. 모르는 diagram_type이면
    지원 목록을 안내한다(도구 실행 실패로 루프를 죽이지 않는다, agent_loop.py의 원칙). """
    diagram_type = str(args.get("diagram_type", "")).strip().lower()
    if diagram_type not in MERMAID_CHEATSHEETS:
        return f"UNKNOWN_DIAGRAM_TYPE: {diagram_type!r}. 지원: flowchart, classDiagram"
    return MERMAID_CHEATSHEETS[diagram_type]


_FENCE_RE = re.compile(r"^```(?:mermaid)?\s*\n?|\n?```$", re.MULTILINE)
_DIAGRAM_KEYWORDS = ("flowchart", "graph", "classdiagram", "sequencediagram", "statediagram", "erdiagram")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def check_mermaid_syntax(text: str) -> tuple[bool, str | None]:
    """ 구조적 검사만 한다(D2) -- 실제 렌더 검증은 안 함. (유효 여부, 실패 사유) """
    cleaned = _strip_fences(text)
    if not cleaned:
        return False, "EMPTY"

    first_line = cleaned.splitlines()[0].strip().lower()
    if not any(first_line.startswith(kw) for kw in _DIAGRAM_KEYWORDS):
        return False, "UNKNOWN_DIAGRAM_TYPE_HEADER"

    for open_ch, close_ch in (("[", "]"), ("(", ")"), ("{", "}")):
        if cleaned.count(open_ch) != cleaned.count(close_ch):
            return False, f"UNBALANCED_{open_ch}{close_ch}"

    return True, None


def _build_structure_block(structure: Sequence[StructureArea]) -> str:
    if not structure:
        return "(구조 정보 없음)"
    lines = []
    for s in structure:
        files_str = ", ".join(s.files) if s.files else "(파일 없음)"
        lines.append(f"- {s.area} ({files_str}): {s.role}")
    return "\n".join(lines)


def run_diagram_stage(
    *,
    doc: AnalysisDoc,
    model_code: str,
    budget: CallBudget,
    job_id: str,
    chat_fn=chat,
) -> tuple[str, list[AiUsage]]:
    """ 반환: (검증된 Mermaid 소스 또는 "", AiUsage 목록). 구조 정보가 없거나 예산이
    0이거나 검증 실패면 ""을 반환한다 -- job을 안 죽인다(D6). """
    if not doc.structure or budget.max_llm_calls < 1:
        return "", []

    stage = load_stage("p05", "p05-4")
    structure_block = _build_structure_block(doc.structure)
    limit = stage.truncation.get("structure_block", len(structure_block))
    values = {"structure_block": structure_block[:limit]}
    messages = list(render(stage, values))
    max_tokens = param_default(stage, "max_tokens") or 800
    temperature = param_default(stage, "temperature") or 0.0

    result, ai_usage = run_tool_loop(
        model_code=model_code,
        messages=messages,
        tools=MERMAID_TOOL_SCHEMA,
        tool_registry={"mermaid_syntax_lookup": mermaid_syntax_lookup},
        max_tokens=max_tokens,
        temperature=temperature,
        budget=budget,
        job_id=job_id,
        chat_fn=chat_fn,
    )

    valid, _reason = check_mermaid_syntax(result.content)
    if not valid:
        return "", ai_usage
    return _strip_fences(result.content), ai_usage
