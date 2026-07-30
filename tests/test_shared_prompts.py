""" app/engines/shared/prompts.py -- load_stage/render/param_default 테스트

D3: 이 두 함수(load_stage/render)가 tools/lint_llm_calls.py::PROMPT001의
유일한 허용 프로듀서다. 여기서 계약(필수 placeholder 검사, {key} 치환,
truncation은 호출자 책임)이 실제로 지켜지는지 확인한다.
"""
import json
from pathlib import Path

import pytest

from app.engines.shared.prompts import load_stage, param_default, render

REAL_MANIFEST = Path(__file__).resolve().parents[1] / "app" / "prompt_manifest.json"


def _write_manifest(tmp_path: Path, pipelines: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"manifest_version": "t-0.0.0", "pipelines": pipelines, "shared": {}}), encoding="utf-8")
    return path


def _stage_dict(**overrides):
    base = {
        "id": "t-1",
        "title": "테스트 스테이지",
        "kind": "prompt",
        "function": "f()",
        "system": "system prompt",
        "user_template": "hello {name}, optional: {extra}",
        "required_placeholders": ["name"],
        "optional_placeholders": ["extra"],
        "truncation": {"name": 100},
        "params": [{"key": "max_tokens", "type": "int", "default": 111}],
    }
    base.update(overrides)
    return base


def test_load_stage_from_manifest(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    stage = load_stage("t", "t-1", manifest_path=path)
    assert stage.id == "t-1"
    assert stage.required_placeholders == ("name",)
    assert stage.optional_placeholders == ("extra",)


def test_load_stage_unknown_pipeline_raises(tmp_path):
    path = _write_manifest(tmp_path, {})
    with pytest.raises(KeyError):
        load_stage("nope", "t-1", manifest_path=path)


def test_load_stage_unknown_stage_id_raises(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    with pytest.raises(KeyError):
        load_stage("t", "t-999", manifest_path=path)


def test_render_fills_template_and_returns_message_list(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    stage = load_stage("t", "t-1", manifest_path=path)
    messages = render(stage, {"name": "world", "extra": "x"})
    assert messages == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello world, optional: x"},
    ]


def test_render_raises_on_missing_required_placeholder(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    stage = load_stage("t", "t-1", manifest_path=path)
    with pytest.raises(ValueError, match="name"):
        render(stage, {})


def test_render_leaves_missing_optional_placeholder_untouched(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    stage = load_stage("t", "t-1", manifest_path=path)
    messages = render(stage, {"name": "world"})
    assert messages[1]["content"] == "hello world, optional: {extra}"


def test_param_default_returns_configured_value(tmp_path):
    path = _write_manifest(tmp_path, {"t": {"label": "t", "source_files": [], "has_llm_calls": True, "stages": [_stage_dict()]}})
    stage = load_stage("t", "t-1", manifest_path=path)
    assert param_default(stage, "max_tokens") == 111
    assert param_default(stage, "nonexistent") is None


def test_committed_manifest_p05_stages_load_cleanly():
    """ 실제로 빌드된 app/prompt_manifest.json의 p05 스테이지 둘 다 load_stage로 읽힌다 """
    stage1 = load_stage("p05", "p05-1", manifest_path=REAL_MANIFEST)
    stage2 = load_stage("p05", "p05-2", manifest_path=REAL_MANIFEST)
    assert stage1.required_placeholders == ("candidates_block",)
    assert stage2.required_placeholders == ("unlabeled_files_block",)
    render(stage1, {"candidates_block": "[]"})  # 필수값만 줘도 렌더링 자체는 성공해야 함
    render(stage2, {"unlabeled_files_block": "[]"})
