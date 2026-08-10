""" multipart 엔드포인트의 요청 본문을 스펙에 드러내는 헬퍼.

**왜 필요한가**: `/analyses`와 `/curricula`는 JSON 문자열(`payload`) + 파일을 받는다.
자동 바인딩을 안 쓰므로 FastAPI가 요청 모델을 못 보고, **`openapi.json`에 그 스키마가
아예 안 나온다**. 백엔드는 이 파일 하나로 구현하는데 요청 필드를 하나도 모르게 된다
(2026-08-02 발견: `AnalysisRequest`·`CurriculumRequest` 둘 다 components에 없었다).

**$ref를 펼치는 이유**: 모델이 components에 등록되지 않으므로 `#/$defs/X` 참조를 그대로
두면 Swagger가 해석하지 못한다($defs 경로가 문서에 없다). 중첩 모델을 참조 자리에
직접 펼쳐 self-contained로 만든다.
"""

import json
from typing import Any

from pydantic import BaseModel


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """모델의 JSON 스키마를 $ref 없이 펼쳐 돌려준다."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return walk(defs[ref.split("/")[-1]])
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(raw)


def multipart_body(model: type[BaseModel], *, file_description: str,
                   payload_example: str, json_example: dict[str, Any] | None = None,
                   json_content: bool = True,
                   examples: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """`payload`(JSON 문자열) + `file`을 받는 요청 본문 스펙.

    `json_content=False`면 multipart만 문서화한다 — 파일이 필수인 엔드포인트다.

    `examples`는 `{이름: {"summary": ..., "value": {...}}}` 형태의 **이름 붙은 예시**다.
    Swagger UI가 드롭다운으로 띄워 준다 — 예시가 하나뿐이면 모드가 여럿인 엔드포인트에서
    "그래서 개인 모드는 어떻게 보내나"를 문서가 답하지 못한다(2026-08-10 백엔드 요청).
    multipart의 `payload` 파트에도 같은 값을 JSON 문자열로 깔아 준다.
    """
    payload_schema = inline_schema(model)
    content: dict[str, Any] = {}

    if json_content:
        entry: dict[str, Any] = {"schema": payload_schema}
        if examples:
            entry["examples"] = examples
        elif json_example is not None:
            entry["example"] = json_example
        content["application/json"] = entry

    multipart_examples = {
        name: {**spec, "value": {"payload": json.dumps(spec["value"], ensure_ascii=False)}}
        for name, spec in (examples or {}).items()
    }
    content["multipart/form-data"] = {
        "schema": {
            "type": "object",
            "required": ["payload", "file"],
            "properties": {
                # 전송은 문자열이지만 **내용은 JSON이고 구조가 정해져 있다.**
                # OpenAPI 3.1의 contentMediaType/contentSchema가 정확히 이 경우를
                # 위한 것이다 — 설명 문장으로만 두면 백엔드가 필드를 못 읽는다.
                "payload": {
                    "type": "string",
                    "contentMediaType": "application/json",
                    "contentSchema": payload_schema,
                    "description": f"요청 JSON을 문자열로. 구조는 {model.__name__}",
                    "example": payload_example,
                },
                "file": {"type": "string", "format": "binary",
                         "description": file_description},
            },
        },
        "encoding": {"payload": {"contentType": "application/json"}},
    }
    if multipart_examples:
        content["multipart/form-data"]["examples"] = multipart_examples
    return {"required": True, "content": content}
