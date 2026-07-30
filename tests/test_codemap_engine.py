""" app/engines/codemap/engine.py -- AnalysisEngine 계약 만족 테스트

materializer를 페이크로 주입해 실제 git clone/zip 해제 없이(네트워크 없이)
tmp_path에 이미 있는 파일들을 그대로 "저장소"로 취급한다.
"""
import inspect
from contextlib import contextmanager

from app.engines.codemap.engine import CodeMapAnalysisEngine
from app.schemas.analysis import AnalysisResult


def _materializer_for(repo_dir: str):
    @contextmanager
    def materializer(request, zip_bytes, workspace_root):
        yield repo_dir
    return materializer


def _write(root, relpath, content):
    full = root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _build_repo(tmp_path):
    _write(tmp_path, "main.py", "from app.util import helper\nhelper()\n")
    _write(tmp_path, "app/util.py", "def helper():\n    return 1\n")
    return tmp_path


REQUEST = {
    "method": "ZIP_WITH_GITLOG",
    "extraction_scope": "TOTAL",
    "question_budget": 2,
    "requirements": [{"requirementId": "r1", "text": "must have a helper"}, {"requirementId": "r2", "text": "must be documented"}],
    "model_code": None,
    "submission_id": "sub-1",
}


def test_result_validates_against_analysis_result(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"fake-zip-bytes")

    usage = raw.pop("ai_usage")
    result = AnalysisResult.model_validate(raw)  # 계약 위반이면 여기서 예외
    assert result.snapshot_meta.file_count == 2
    assert usage == []  # tier2_enabled=False 기본값 -- 크루 호출 자체가 없다


def test_requirement_results_match_request_length(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    assert len(raw["requirement_results"]) == len(REQUEST["requirements"])
    assert {r["requirement_id"] for r in raw["requirement_results"]} == {"r1", "r2"}
    assert all(r["verdict"] == "F" for r in raw["requirement_results"])


def test_stages_are_L1_to_L4_with_two_hints(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    assert raw["problems"], "적어도 하나는 problem이 만들어져야 한다"
    for problem in raw["problems"]:
        axes = [s["axis_code"] for s in problem["stages"]]
        assert axes == ["L1", "L2", "L3", "L4"]
        for stage in problem["stages"]:
            assert len(stage["hints"]) == 2
            assert [h["hint_level"] for h in stage["hints"]] == [1, 2]
            assert stage["flagged"] is True  # 아직 실제 질문 생성 전이므로 검수 필요 표시


def test_evidence_hash_matches_code_snippet(tmp_path):
    from app.engines.shared.evidence import evidence_hash

    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    for problem in raw["problems"]:
        assert problem["evidence_hash"] == evidence_hash(problem["code_snippet"])


def test_ai_usage_is_top_level_sibling_key(tmp_path):
    """ base.py 계약: ai_usage는 AnalysisResult 필드가 아니라 형제 키 --
    jobs.py::run_analysis가 pop해서 AnalysisJobStatus.ai_usage로 옮긴다. """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    assert "ai_usage" in raw
    usage_raw = raw.pop("ai_usage")
    AnalysisResult.model_validate(raw)  # ai_usage를 뺀 나머지가 그대로 계약을 만족해야 함
    assert isinstance(usage_raw, list)


def test_analyze_is_sync_def():
    """ README §4 함정: BackgroundTasks가 threadpool에서 돌리므로 코루틴이면 안 된다 """
    assert not inspect.iscoroutinefunction(CodeMapAnalysisEngine.analyze)


def test_tier2_off_produces_same_problems_as_tier1(tmp_path):
    """ tier2_enabled=False(기본값)면 LLM 호출이 아예 없고, Tier 1 순위 그대로
    problem 순서가 정해진다 -- main.py는 entry_point 신호(stem="main")와 얕은
    경로 깊이(rank.py 기준)가 겹쳐 util.py보다 우선한다(실측: entry_point 신호가
    fan-in 신호보다 총점을 더 크게 끌어올림). """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), tier2_enabled=False)
    request = dict(REQUEST, question_budget=2)
    raw = engine.analyze(request, zip_bytes=b"x")
    assert raw["ai_usage"] == []  # tier2 꺼져 있으면 LLM 호출 자체가 없다
    paths = [p["source_path"] for p in raw["problems"]]
    assert paths.index("main.py") < paths.index("app/util.py")


def test_question_budget_caps_problem_count(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    request = dict(REQUEST, question_budget=1)
    raw = engine.analyze(request, zip_bytes=b"x")
    assert len(raw["problems"]) == 1
    assert raw["question_count_planned"] == 1


def test_own_commit_scope_without_attribution_falls_back_to_total(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))  # attribution=None
    request = dict(REQUEST, extraction_scope="OWN_COMMIT", commit_email="dev@example.com")
    raw = engine.analyze(request, zip_bytes=b"x")
    assert raw["applied_scope"] == "TOTAL"
    assert raw["scope_fallback"] is True
    assert raw["fallback_reason"]


def test_content_hash_is_64_hex_and_stable(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)))
    raw1 = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    raw2 = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    h1, h2 = raw1["snapshot_meta"]["content_hash"], raw2["snapshot_meta"]["content_hash"]
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
    assert h1 == h2  # 같은 저장소 -> 같은 해시
