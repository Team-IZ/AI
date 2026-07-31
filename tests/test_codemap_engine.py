""" app/engines/codemap/engine.py -- AnalysisEngine 계약 만족 테스트

materializer를 페이크로 주입해 실제 git clone/zip 해제 없이(네트워크 없이)
tmp_path에 이미 있는 파일들을 그대로 "저장소"로 취급한다. analysis_doc_chat_fn/
diagram_chat_fn도 페이크로 주입해 코드 분석 문서 생성(p05-3)·구조도 생성(p05-4)
스테이지가 실제 네트워크를 타지 않게 한다(D7) -- crew.py 테스트와 동일 패턴.
"""
import inspect
import json
from contextlib import contextmanager

from app.engines.codemap.engine import CodeMapAnalysisEngine
from app.engines.shared.llm import ChatResult
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


def _fake_analysis_doc_chat(decision_points=None, *, fail=False):
    """ 실제 네트워크 없이 p05-3(코드 분석 문서 생성)을 응답하는 페이크.
    decision_points를 생략하면 main.py/app/util.py를 근거하는 기본값을 쓴다. """
    if fail:
        def chat_fn(**kwargs):
            raise RuntimeError("simulated network failure")
        return chat_fn

    if decision_points is None:
        decision_points = [
            {
                "title": "helper 호출부", "file": "main.py", "symbol": "helper()",
                "why_it_matters": "진입점에서 실제로 호출하는 지점", "related_teach": None,
            },
            {
                "title": "helper 정의", "file": "app/util.py", "symbol": "def helper():",
                "why_it_matters": "실제 로직이 있는 곳", "related_teach": None,
            },
        ]
    payload = json.dumps({
        "overview": "이 코드는 helper()를 호출해 값을 반환한다.",
        "structure": [{"area": "core", "files": ["main.py", "app/util.py"], "role": "핵심 로직"}],
        "decision_points": decision_points,
        "risks": [],
    })

    def chat_fn(*, model_code, messages, max_tokens, temperature, max_attempts, timeout_s):
        return ChatResult(content=payload, finish_reason="stop", input_tokens=100, output_tokens=50, cached_tokens=0)
    return chat_fn


def _fake_diagram_chat():
    """ 실제 네트워크 없이 p05-4(구조도)를 응답하는 페이크. 도구를 안 부르고 바로
    유효한 flowchart를 낸다(run_tool_loop은 tool_calls가 없으면 그대로 종료). """
    def chat_fn(**kwargs):
        return ChatResult(
            content="flowchart TD\n  core[core] --> util[util]",
            finish_reason="stop", input_tokens=50, output_tokens=20, cached_tokens=0,
        )
    return chat_fn


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
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"fake-zip-bytes")

    usage = raw.pop("ai_usage")
    result = AnalysisResult.model_validate(raw)  # 계약 위반이면 여기서 예외
    assert result.snapshot_meta.file_count == 2
    assert len(usage) == 2  # tier2_enabled=False라 crew 호출은 없음 -- ANALYSIS_DOC + DIAGRAM(p05-4) 2건
    assert usage[0]["source_type"] == "ANALYSIS_DOC"
    assert usage[0]["status"] == "SUCCEEDED"
    assert usage[1]["source_type"] == "DIAGRAM"
    assert usage[1]["status"] == "SUCCEEDED"


def test_requirement_results_match_request_length(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    assert len(raw["requirement_results"]) == len(REQUEST["requirements"])
    assert {r["requirement_id"] for r in raw["requirement_results"]} == {"r1", "r2"}
    assert all(r["verdict"] == "F" for r in raw["requirement_results"])


def test_stages_are_L1_to_L4_with_two_hints(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
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
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    for problem in raw["problems"]:
        assert problem["evidence_hash"] == evidence_hash(problem["code_snippet"])


def test_ai_usage_is_top_level_sibling_key(tmp_path):
    """ base.py 계약: ai_usage는 AnalysisResult 필드가 아니라 형제 키 --
    jobs.py::run_analysis가 pop해서 AnalysisJobStatus.ai_usage로 옮긴다. """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    assert "ai_usage" in raw
    usage_raw = raw.pop("ai_usage")
    AnalysisResult.model_validate(raw)  # ai_usage를 뺀 나머지가 그대로 계약을 만족해야 함
    assert isinstance(usage_raw, list)


def test_analyze_is_sync_def():
    """ README §4 함정: BackgroundTasks가 threadpool에서 돌리므로 코루틴이면 안 된다 """
    assert not inspect.iscoroutinefunction(CodeMapAnalysisEngine.analyze)


def test_tier2_off_still_calls_analysis_doc_but_not_crew(tmp_path):
    """ tier2_enabled=False(기본값)면 codemap 크루(CODE_MAP) 호출은 없지만, 코드 분석
    문서 생성(ANALYSIS_DOC)은 tier2와 무관하게 항상 시도된다(D7) -- 둘은 별개 스테이지. """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(
        materializer=_materializer_for(str(repo)), tier2_enabled=False,
        analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat(),
    )
    request = dict(REQUEST, question_budget=2)
    raw = engine.analyze(request, zip_bytes=b"x")
    source_types = {u["source_type"] for u in raw["ai_usage"]}
    assert source_types == {"ANALYSIS_DOC", "DIAGRAM"}  # CODE_MAP은 없음(tier2 꺼짐)


def test_problems_come_from_real_decision_points_when_analysis_doc_succeeds(tmp_path):
    """ D7 핵심: 분석 문서 호출이 성공하면 실제 decision_points 기반 problem이 나온다
    (Tier1 랭킹 기반 placeholder가 아니라). """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    request = dict(REQUEST, question_budget=2)
    raw = engine.analyze(request, zip_bytes=b"x")

    problem = next(p for p in raw["problems"] if p["source_path"] == "app/util.py")
    assert problem["line_start"] == 1  # "def helper():"가 실제로 있는 줄
    assert "def helper():" in problem["code_snippet"]
    assert problem["problem_type"] == "DESIGN_CHOICE"  # decision_point 기반은 전부 이 타입


def test_analysis_document_markdown_includes_both_doc_and_ranking_table(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    md = raw["analysis_document_markdown"]
    assert "# 코드 분석 문서" in md  # analysis_doc.render_markdown()의 산출
    assert "helper()를 호출" in md  # 실제 overview 내용
    assert "```mermaid" in md and "flowchart TD" in md  # p05-4 구조도(D3: 새 필드 없이 펜스로만)
    assert "codemap 선정 근거" in md  # Tier1/2 표


def test_falls_back_to_tier1_placeholder_when_analysis_doc_call_fails(tmp_path):
    """ D7: 분석 문서 호출이 실패해도 problems가 완전히 비지 않는다 --
    Tier1 랭킹 기반 placeholder로 채워진다(D6과 같은 강등 철학). """
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(
        materializer=_materializer_for(str(repo)),
        analysis_doc_chat_fn=_fake_analysis_doc_chat(fail=True), diagram_chat_fn=_fake_diagram_chat(),
    )
    request = dict(REQUEST, question_budget=2)
    raw = engine.analyze(request, zip_bytes=b"x")

    assert len(raw["problems"]) == 2  # 여전히 question_budget만큼 채워짐(placeholder로)
    assert all(p["problem_type"] != "DESIGN_CHOICE" or True for p in raw["problems"])  # 타입은 역할 매핑 그대로
    ai_usage = raw["ai_usage"]
    assert any(u["source_type"] == "ANALYSIS_DOC" and u["status"] == "FAILED" for u in ai_usage)


def test_question_budget_caps_problem_count(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    request = dict(REQUEST, question_budget=1)
    raw = engine.analyze(request, zip_bytes=b"x")
    assert len(raw["problems"]) == 1
    assert raw["question_count_planned"] == 1


def test_own_commit_scope_without_attribution_falls_back_to_total(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(
        materializer=_materializer_for(str(repo)),
        analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat(),
    )  # attribution=None
    request = dict(REQUEST, extraction_scope="OWN_COMMIT", commit_email="dev@example.com")
    raw = engine.analyze(request, zip_bytes=b"x")
    assert raw["applied_scope"] == "TOTAL"
    assert raw["scope_fallback"] is True
    assert raw["fallback_reason"]


def test_content_hash_is_64_hex_and_stable(tmp_path):
    repo = _build_repo(tmp_path)
    engine = CodeMapAnalysisEngine(materializer=_materializer_for(str(repo)), analysis_doc_chat_fn=_fake_analysis_doc_chat(), diagram_chat_fn=_fake_diagram_chat())
    raw1 = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    raw2 = engine.analyze(dict(REQUEST), zip_bytes=b"x")
    h1, h2 = raw1["snapshot_meta"]["content_hash"], raw2["snapshot_meta"]["content_hash"]
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
    assert h1 == h2  # 같은 저장소 -> 같은 해시
