""" prompt_manifest.json에서 스테이지를 읽어와 LLM 호출용 메시지로 만드는 유일한 통로

D3 (2026-07-30): 프롬프트 문자열이 LLM 호출부에 도달하는 경로는 이 모듈의
load_stage()/render() 두 함수뿐이다 -- tools/lint_llm_calls.py의 PROMPT001 규칙이
이 두 이름(과 render_stage/build_messages)만 "지정 로더를 거쳤다"고 인정한다.
그러니 이 파일이 곧 그 화이트리스트의 근거다: 다른 데서 프롬프트 문자열을
만들면 안 되는 이유는 "규칙이 그렇다"가 아니라 "이 파일 하나만 매니페스트를
읽으므로, 여기 말고 다른 경로로 만든 프롬프트는 YAML/manifest 이력 밖에
있다"는 사실이다.

app/poc-engine.js/app/llm-stage.js(origin/feat/poc_full)의 POCStage 패턴을
그대로 옮긴다 -- required_placeholders 존재 검사, {key} 치환, truncation은
호출자가 stage.truncation[key]로 직접 잘라서 값을 만들어 넘긴다(이 함수가
자동으로 자르지 않는다 -- 원본 JS도 그렇다. 무엇을 몇 자로 자를지는 도메인
지식이라 호출자가 안다).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "prompt_manifest.json"
_manifest_cache: dict[str, Any] | None = None


@dataclass(frozen=True)
class Param:
    key: str
    type: str
    default: Any
    locked: bool = False
    note: str | None = None


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    kind: str
    function: str
    system: str
    user_template: str
    required_placeholders: tuple[str, ...] = ()
    optional_placeholders: tuple[str, ...] = ()
    system_note: str | None = None
    truncation: dict[str, int] = field(default_factory=dict)
    params: tuple[Param, ...] = ()


def _load_manifest(manifest_path: Path | None = None) -> dict[str, Any]:
    global _manifest_cache
    if manifest_path is not None:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    if _manifest_cache is None:
        _manifest_cache = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return _manifest_cache


def _stage_from_dict(raw: dict[str, Any]) -> Stage:
    return Stage(
        id=raw["id"],
        title=raw["title"],
        kind=raw["kind"],
        function=raw["function"],
        system=raw["system"],
        user_template=raw["user_template"],
        required_placeholders=tuple(raw.get("required_placeholders", [])),
        optional_placeholders=tuple(raw.get("optional_placeholders", [])),
        system_note=raw.get("system_note"),
        truncation=dict(raw.get("truncation", {})),
        params=tuple(
            Param(key=p["key"], type=p["type"], default=p.get("default"), locked=p.get("locked", False), note=p.get("note"))
            for p in raw.get("params", [])
        ),
    )


def load_stage(pipeline: str, stage_id: str, *, manifest_path: Path | None = None) -> Stage:
    """ manifest_path를 생략하면 app/prompt_manifest.json(커밋된 생성물)을 읽는다 --
    테스트는 임시 매니페스트 경로를 넘겨 격리할 수 있다. """
    manifest = _load_manifest(manifest_path)
    pipelines = manifest.get("pipelines", {})
    if pipeline not in pipelines:
        raise KeyError(f"알 수 없는 pipeline: {pipeline}")
    for raw_stage in pipelines[pipeline].get("stages", []):
        if raw_stage["id"] == stage_id:
            return _stage_from_dict(raw_stage)
    raise KeyError(f"알 수 없는 stage: {pipeline}/{stage_id}")


def param_default(stage: Stage, key: str) -> Any:
    for p in stage.params:
        if p.key == key:
            return p.default
    return None


def _fill_template(template: str, values: dict[str, Any]) -> str:
    """ app/llm-stage.js의 LabApp.fillTemplate()과 동일한 규칙: {key} 치환,
    values에 없는 키는 그대로 남긴다(무음 실패 대신 눈에 띄게). """
    return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def render(stage: Stage, values: dict[str, Any]) -> list[dict[str, str]]:
    """ required_placeholders 존재 검사 후 [{"role": "system", ...}, {"role": "user", ...}] 생성

    truncation은 여기서 하지 않는다 -- stage.truncation[key]를 보고 호출자가 값을
    미리 잘라서 values에 담아 넘긴다(app/llm-stage.js::call()과 동일한 책임 분담).
    """
    for key in stage.required_placeholders:
        if values.get(key) in (None, ""):
            raise ValueError(f"{stage.id}({stage.title}): 필수 값 누락 -- {key}")

    return [
        {"role": "system", "content": stage.system},
        {"role": "user", "content": _fill_template(stage.user_template, values)},
    ]
