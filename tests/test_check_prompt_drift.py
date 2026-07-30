""" tools/check_prompt_drift.py -- canonical-JSON 해시 비교 테스트 (D5, PR-5)

합성 매니페스트로 메커니즘 자체를 검증한다. 실제 이 브랜치와 feat/poc_full
사이에는 아직 공유되는 파이프라인이 없다(이 브랜치=p05뿐, poc_full=p04뿐) --
그래서 실제 두 branch를 체크아웃해 비교하는 CI 스텝은 아직 추가하지 않는다
(docs/code-importance-map/PARALLEL_RUN_CHECKLIST.md PR-5 참고). 이 도구는
나중에 공유 파이프라인이 생기면 바로 쓸 수 있게 지금 준비해 둔 것이다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_prompt_drift import check, main  # noqa: E402


def _write_manifest(tmp_path: Path, name: str, pipelines: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"manifest_version": "v", "pipelines": pipelines}), encoding="utf-8")
    return path


SAMPLE_STAGE = {"id": "p04-1", "title": "t", "kind": "prompt", "function": "f()", "system": "s", "user_template": "u"}


def test_identical_pipeline_passes(tmp_path):
    local = _write_manifest(tmp_path, "local.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    passed, message = check(local, remote, "p04")
    assert passed
    assert "OK" in message


def test_identical_pipeline_passes_regardless_of_key_order(tmp_path):
    """ canonical JSON(sort_keys=True) 비교라 dict 키 순서만 다른 건 드리프트로 안 본다 """
    stage_reordered = {"user_template": "u", "system": "s", "function": "f()", "kind": "prompt", "title": "t", "id": "p04-1"}
    local = _write_manifest(tmp_path, "local.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"has_llm_calls": True, "source_files": [], "label": "L", "stages": [stage_reordered]}})
    passed, _ = check(local, remote, "p04")
    assert passed


def test_diverged_pipeline_fails(tmp_path):
    edited_stage = dict(SAMPLE_STAGE, system="이 프롬프트가 한쪽에서만 고쳐졌다")
    local = _write_manifest(tmp_path, "local.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [edited_stage]}})
    passed, message = check(local, remote, "p04")
    assert not passed
    assert "FAIL" in message


def test_missing_on_one_side_fails(tmp_path):
    local = _write_manifest(tmp_path, "local.json", {"p05": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": []}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": []}})
    passed, message = check(local, remote, "p04")
    assert not passed
    assert "local에만 없음" in message or "local" in message


def test_missing_on_both_sides_is_not_a_drift():
    """ 애초에 양쪽 다 그 파이프라인을 안 다루면(비교 대상 아님) 실패로 보지 않는다 """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        local = _write_manifest(tmp_path, "local.json", {"p05": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": []}})
        remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": []}})
        passed, message = check(local, remote, "p99_neither_has_this")
        assert passed
        assert "비교 대상 아님" in message


def test_main_exits_nonzero_on_drift(tmp_path, capsys):
    edited_stage = dict(SAMPLE_STAGE, system="drift")
    local = _write_manifest(tmp_path, "local.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [edited_stage]}})
    code = main(["--local", str(local), "--remote", str(remote), "--pipeline", "p04"])
    assert code == 1


def test_main_exits_zero_when_clean(tmp_path):
    local = _write_manifest(tmp_path, "local.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    remote = _write_manifest(tmp_path, "remote.json", {"p04": {"label": "L", "source_files": [], "has_llm_calls": True, "stages": [SAMPLE_STAGE]}})
    code = main(["--local", str(local), "--remote", str(remote), "--pipeline", "p04"])
    assert code == 0


def test_this_branchs_p05_matches_itself():
    """ 실제 커밋된 app/prompt_manifest.json이 자기 자신과 비교하면 당연히 일치 --
    도구가 실제 파일 형식에 대해서도 제대로 동작하는지 확인 """
    real_manifest = Path(__file__).resolve().parents[1] / "app" / "prompt_manifest.json"
    passed, _ = check(real_manifest, real_manifest, "p05")
    assert passed
