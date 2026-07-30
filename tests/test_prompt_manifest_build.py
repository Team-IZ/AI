""" tools/build_prompt_manifest.py -- YAML -> prompt_manifest.json 빌드 스크립트 테스트

D3-2: 이 스크립트가 존재하는 이유(사용자 요청)는 롤백 안전성이다 -- YAML을
지워도 손으로 관리하는 JSON만 남으면 기존 소비자가 안 바뀐다는 것을
test_build_preserves_other_pipelines가 증명한다.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_prompt_manifest import build_pipeline_object, main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_yaml(dir_path: Path, filename: str, content: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / filename
    path.write_text(content, encoding="utf-8")
    return path


STAGE_A = """
id: x-1
pipeline: x
title: Stage A
kind: prompt
function: f()
system: sys A
user_template: "hi {name}"
required_placeholders: [name]
params:
  - key: max_tokens
    type: int
    default: 100
"""

STAGE_B = """
id: x-2
pipeline: x
title: Stage B
kind: prompt
function: g()
system: sys B
user_template: "bye {name}"
required_placeholders: [name]
"""


def test_build_pipeline_object_sorts_stages_by_id(tmp_path):
    _write_yaml(tmp_path, "b.yaml", STAGE_B)
    _write_yaml(tmp_path, "a.yaml", STAGE_A)
    obj = build_pipeline_object(tmp_path, "x", label="X", source_files=[])
    assert [s["id"] for s in obj["stages"]] == ["x-1", "x-2"]


def test_build_pipeline_object_ignores_other_pipelines(tmp_path):
    _write_yaml(tmp_path, "a.yaml", STAGE_A)
    _write_yaml(tmp_path, "other.yaml", STAGE_A.replace("pipeline: x", "pipeline: y").replace("x-1", "y-1"))
    obj = build_pipeline_object(tmp_path, "x", label="X", source_files=[])
    assert len(obj["stages"]) == 1


def test_build_pipeline_object_raises_when_empty(tmp_path):
    with pytest.raises(ValueError):
        build_pipeline_object(tmp_path, "x", label="X", source_files=[])


def test_build_is_deterministic(tmp_path):
    _write_yaml(tmp_path, "a.yaml", STAGE_A)
    _write_yaml(tmp_path, "b.yaml", STAGE_B)
    out = tmp_path / "manifest.json"
    main(["--src", str(tmp_path), "--out", str(out), "--pipeline", "x", "--label", "X"])
    first = out.read_text(encoding="utf-8")
    main(["--src", str(tmp_path), "--out", str(out), "--pipeline", "x", "--label", "X"])
    second = out.read_text(encoding="utf-8")
    assert first == second


def test_check_mode_detects_yaml_edit(tmp_path):
    src = tmp_path / "src"
    _write_yaml(src, "a.yaml", STAGE_A)
    out = tmp_path / "manifest.json"
    main(["--src", str(src), "--out", str(out), "--pipeline", "x", "--label", "X"])

    assert main(["--src", str(src), "--out", str(out), "--pipeline", "x", "--label", "X", "--check"]) == 0

    _write_yaml(src, "a.yaml", STAGE_A.replace("Stage A", "Stage A (edited)"))
    assert main(["--src", str(src), "--out", str(out), "--pipeline", "x", "--label", "X", "--check"]) == 1


def test_build_preserves_other_pipelines(tmp_path):
    """ 롤백 안전성의 핵심 -- x 파이프라인을 새로 넣어도 기존 y 파이프라인은 그대로 남는다 """
    out = tmp_path / "manifest.json"
    out.write_text(json.dumps({
        "manifest_version": "v1", "pipelines": {"y": {"label": "Y", "source_files": [], "has_llm_calls": False, "stages": []}}, "shared": {"k": "v"},
    }), encoding="utf-8")

    src = tmp_path / "src"
    _write_yaml(src, "a.yaml", STAGE_A)
    main(["--src", str(src), "--out", str(out), "--pipeline", "x", "--label", "X"])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "y" in data["pipelines"]
    assert data["pipelines"]["y"]["label"] == "Y"
    assert "x" in data["pipelines"]
    assert data["shared"] == {"k": "v"}


def test_yaml_keys_match_manifest_stage_keys_exactly(tmp_path):
    _write_yaml(tmp_path, "a.yaml", STAGE_A)
    obj = build_pipeline_object(tmp_path, "x", label="X", source_files=[])
    stage = obj["stages"][0]
    assert set(stage.keys()) <= {
        "id", "title", "kind", "function", "system", "system_note",
        "user_template", "required_placeholders", "optional_placeholders",
        "truncation", "params",
    }
    assert "pipeline" not in stage  # 메타데이터는 스테이지 객체에 새지 않는다


def test_every_required_placeholder_appears_in_template(tmp_path):
    """ codemap의 실제 YAML 둘 다 -- required_placeholders에 적힌 이름이 user_template
    안에 실제로 {키} 형태로 등장하는지(오타 방지) """
    prompts_dir = REPO_ROOT / "app" / "engines" / "codemap" / "prompts"
    obj = build_pipeline_object(prompts_dir, "p05", label="test", source_files=[])
    for stage in obj["stages"]:
        for key in stage["required_placeholders"]:
            assert "{" + key + "}" in stage["user_template"], f"{stage['id']}: {{{key}}}가 템플릿에 없음"
