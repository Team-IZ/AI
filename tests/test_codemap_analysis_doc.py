""" app/engines/codemap/analysis_doc.py -- 코드 분석 문서 생성(p05-3) 테스트

parse_analysis_doc_response는 ground.py와 같은 원칙(closed-vocabulary, 자유
서술 절대 미노출)을 새 필드(file/related_teach)에 적용한 것이라 그 테스트
패턴을 그대로 따른다. run_analysis_doc은 fake chat_fn을 주입해 네트워크 없이
검증한다(crew.py 테스트와 동일 패턴).
"""
from app.engines.codemap.analysis_doc import (
    build_problems,
    parse_analysis_doc_response,
    render_markdown,
    run_analysis_doc,
)
from app.engines.codemap.models import AnalysisDoc, DecisionPoint, StructureArea
from app.engines.shared.budget import CallBudget
from app.engines.shared.llm import ChatResult, LlmTimeoutError

ALLOWED_PATHS = frozenset({"src/main.py", "src/util.py"})
ALLOWED_TEACHES = frozenset({"teach-1", "teach-2"})

BUDGET = CallBudget(
    feature_code="CODE_ANALYSIS", source_type="ANALYSIS_DOC",
    max_llm_calls=1, max_tool_rounds=0, max_attempts_per_call=3, timeout_s=600,
)


def _valid_decision_point(**overrides):
    dp = {
        "title": "예외 처리 분기",
        "file": "src/main.py",
        "symbol": "def handle():",
        "why_it_matters": "에러 흐름을 이해했는지 검증하기 좋음",
        "related_teach": "teach-1",
    }
    dp.update(overrides)
    return dp


# --- parse_analysis_doc_response -------------------------------------------

def test_valid_response_parses_cleanly():
    raw = {
        "overview": "이 코드는 결제를 처리한다.",
        "structure": [{"area": "결제", "files": ["src/main.py"], "role": "결제 흐름 담당"}],
        "decision_points": [_valid_decision_point()],
        "risks": ["하드코딩된 재시도 횟수"],
    }
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.overview == "이 코드는 결제를 처리한다."
    assert doc.structure == (StructureArea(area="결제", files=("src/main.py",), role="결제 흐름 담당"),)
    assert len(doc.decision_points) == 1
    assert doc.decision_points[0].related_teach == "teach-1"
    assert doc.risks == ("하드코딩된 재시도 횟수",)
    assert rejected == ()


def test_missing_overview_is_flagged_but_does_not_crash():
    doc, rejected = parse_analysis_doc_response({}, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.overview == ""
    assert "MISSING_OVERVIEW" in rejected


def test_structure_item_with_file_outside_allowed_paths_is_filtered():
    raw = {"overview": "x", "structure": [{"area": "a", "files": ["src/main.py", "src/evil.py"], "role": "r"}]}
    doc, _ = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.structure[0].files == ("src/main.py",)  # src/evil.py는 조용히 걸러짐


def test_decision_point_with_unknown_file_is_rejected():
    raw = {"overview": "x", "decision_points": [_valid_decision_point(file="src/nonexistent.py")]}
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.decision_points == ()
    assert "UNKNOWN_FILE" in rejected


def test_decision_point_missing_symbol_is_rejected():
    raw = {"overview": "x", "decision_points": [_valid_decision_point(symbol="")]}
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.decision_points == ()
    assert "MISSING_SYMBOL" in rejected


def test_decision_point_missing_title_or_why_is_rejected():
    raw1 = {"overview": "x", "decision_points": [_valid_decision_point(title="")]}
    _, rejected1 = parse_analysis_doc_response(raw1, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert "MISSING_TITLE" in rejected1

    raw2 = {"overview": "x", "decision_points": [_valid_decision_point(why_it_matters="")]}
    _, rejected2 = parse_analysis_doc_response(raw2, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert "MISSING_WHY" in rejected2


def test_unknown_teach_id_is_nulled_not_whole_item_dropped():
    """ related_teach가 후보 밖이면 그 필드만 null -- decision_point 자체는 살아남는다 """
    raw = {"overview": "x", "decision_points": [_valid_decision_point(related_teach="made-up-teach-id")]}
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert len(doc.decision_points) == 1
    assert doc.decision_points[0].related_teach is None
    assert "UNKNOWN_TEACH_ID_NULLED" in rejected


def test_related_teach_none_is_valid_as_is():
    raw = {"overview": "x", "decision_points": [_valid_decision_point(related_teach=None)]}
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.decision_points[0].related_teach is None
    assert rejected == ()


def test_duplicate_decision_point_is_rejected():
    dp = _valid_decision_point()
    raw = {"overview": "x", "decision_points": [dp, dict(dp)]}
    doc, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert len(doc.decision_points) == 1
    assert "DUPLICATE_DECISION_POINT" in rejected


def test_free_prose_never_reaches_rejected_reasons():
    """ ground.py와 동일 원칙: 모델이 지어낸 자유 텍스트가 rejected에 안 남는다 """
    raw = {"overview": "x", "decision_points": [_valid_decision_point(
        file="this file definitely does not exist but sounds important",
    )]}
    _, rejected = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    joined = " ".join(rejected)
    assert "definitely does not exist" not in joined


def test_non_string_risks_are_filtered_out():
    raw = {"overview": "x", "risks": ["real risk", 123, None, ""]}
    doc, _ = parse_analysis_doc_response(raw, ALLOWED_PATHS, ALLOWED_TEACHES)
    assert doc.risks == ("real risk",)


# --- render_markdown ---------------------------------------------------------

def test_render_markdown_includes_all_sections():
    doc = AnalysisDoc(
        overview="개요 문장",
        structure=(StructureArea(area="결제", files=("src/main.py",), role="담당"),),
        decision_points=(DecisionPoint(title="t", file="src/main.py", symbol="def f():", why_it_matters="w", related_teach=None),),
        risks=("위험1",),
    )
    md = render_markdown(doc)
    assert "# 코드 분석 문서" in md
    assert "개요 문장" in md
    assert "결제" in md
    assert "t" in md and "src/main.py" in md
    assert "위험1" in md


def test_render_markdown_handles_empty_doc_without_crashing():
    md = render_markdown(AnalysisDoc(overview="", structure=(), decision_points=(), risks=()))
    assert "(개요 없음)" in md


# --- build_problems -----------------------------------------------------------

def test_build_problems_grounds_symbol_to_real_lines():
    files = {"src/main.py": "def a():\n    pass\n\n\ndef handle():\n    return 1\n"}
    dps = (DecisionPoint(title="t", file="src/main.py", symbol="def handle():", why_it_matters="w", related_teach=None),)
    problems, ungrounded = build_problems(dps, files, extractor_version="v1", question_budget=5)
    assert len(problems) == 1
    assert problems[0]["source_path"] == "src/main.py"
    assert problems[0]["line_start"] == 5
    assert ungrounded == ()


def test_build_problems_drops_ungrounded_symbol():
    files = {"src/main.py": "def a():\n    pass\n"}
    dps = (DecisionPoint(title="t", file="src/main.py", symbol="def nonexistent():", why_it_matters="w", related_teach=None),)
    problems, ungrounded = build_problems(dps, files, extractor_version="v1", question_budget=5)
    assert problems == []
    assert len(ungrounded) == 1


def test_build_problems_respects_question_budget():
    files = {"src/main.py": "def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n"}
    dps = tuple(
        DecisionPoint(title=f"t{i}", file="src/main.py", symbol=sym, why_it_matters="w", related_teach=None)
        for i, sym in enumerate(["def a():", "def b():", "def c():"])
    )
    problems, _ = build_problems(dps, files, extractor_version="v1", question_budget=2)
    assert len(problems) == 2


def test_build_problems_evidence_hash_matches_snippet():
    from app.engines.shared.evidence import evidence_hash

    files = {"src/main.py": "def handle():\n    return 1\n"}
    dps = (DecisionPoint(title="t", file="src/main.py", symbol="def handle():", why_it_matters="w", related_teach=None),)
    problems, _ = build_problems(dps, files, extractor_version="v1", question_budget=5)
    assert problems[0]["evidence_hash"] == evidence_hash(problems[0]["code_snippet"])


def test_build_problems_stages_are_placeholder_L1_to_L4():
    files = {"src/main.py": "def handle():\n    return 1\n"}
    dps = (DecisionPoint(title="t", file="src/main.py", symbol="def handle():", why_it_matters="w", related_teach=None),)
    problems, _ = build_problems(dps, files, extractor_version="v1", question_budget=5)
    axes = [s["axis_code"] for s in problems[0]["stages"]]
    assert axes == ["L1", "L2", "L3", "L4"]
    assert all(s["flagged"] is True for s in problems[0]["stages"])


# --- run_analysis_doc ---------------------------------------------------------

def _fake_chat(content, usage=None):
    def chat_fn(*, model_code, messages, max_tokens, temperature, max_attempts, timeout_s):
        return usage or ChatResult(content=content, finish_reason="stop", input_tokens=100, output_tokens=50, cached_tokens=0)
    return chat_fn


def test_run_analysis_doc_returns_empty_when_no_selected_paths():
    doc, rejected, ai_usage = run_analysis_doc(
        files_by_path={}, selected_paths=(), teaches=(), model_code="m", budget=BUDGET, job_id="job-1",
    )
    assert doc.overview == ""
    assert rejected == ()
    assert ai_usage == []


def test_run_analysis_doc_zero_budget_skips_call():
    calls = {"n": 0}

    def chat_fn(**kwargs):
        calls["n"] += 1
        return ChatResult(content="{}", finish_reason="stop", input_tokens=0, output_tokens=0, cached_tokens=0)

    zero_budget = CallBudget(
        feature_code="CODE_ANALYSIS", source_type="ANALYSIS_DOC",
        max_llm_calls=0, max_tool_rounds=0, max_attempts_per_call=1, timeout_s=60,
    )
    doc, rejected, ai_usage = run_analysis_doc(
        files_by_path={"a.py": "x=1"}, selected_paths=("a.py",), teaches=(), model_code="m",
        budget=zero_budget, job_id="job-1", chat_fn=chat_fn,
    )
    assert calls["n"] == 0
    assert doc.overview == ""
    assert rejected == ()
    assert ai_usage == []


def test_run_analysis_doc_success_produces_doc_and_ai_usage():
    import json

    json_text = json.dumps({
        "overview": "요약",
        "structure": [],
        "decision_points": [_valid_decision_point()],
        "risks": [],
    })
    doc, rejected, ai_usage = run_analysis_doc(
        files_by_path={"src/main.py": "def handle():\n    return 1\n"},
        selected_paths=("src/main.py",),
        teaches=[{"id": "teach-1", "label": "예외 처리", "unitId": "u1", "sourcePages": [1, 2]}],
        model_code="z-ai/glm-5.2", budget=BUDGET, job_id="job-1",
        chat_fn=_fake_chat(json_text),
    )
    assert doc.overview == "요약"
    assert len(doc.decision_points) == 1
    assert rejected == ()
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "SUCCEEDED"
    assert ai_usage[0].source_type == "ANALYSIS_DOC"


def test_run_analysis_doc_failure_falls_back_to_empty_doc():
    def chat_fn(**kwargs):
        raise LlmTimeoutError("timed out")

    doc, rejected, ai_usage = run_analysis_doc(
        files_by_path={"a.py": "x=1"}, selected_paths=("a.py",), teaches=(), model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert doc.overview == ""
    assert rejected == ()
    assert len(ai_usage) == 1
    assert ai_usage[0].status == "FAILED"
    assert ai_usage[0].failure_code == "TIMEOUT"


def test_run_analysis_doc_truncates_code_block_per_stage_config():
    """ prompt_manifest.json의 p05-3 truncation.code_block(12000)을 실제로 지키는지 --
    _build_code_block이 파일마다 "### path\n```\n"로 감싸므로, 정확히 huge[:12000]이
    부분 문자열로 남는지가 아니라 "20000자 원문 전체가 그대로 들어가진 않는지"만 확인한다. """
    captured = {}

    def chat_fn(*, model_code, messages, max_tokens, temperature, max_attempts, timeout_s):
        captured["user"] = messages[1]["content"]
        return ChatResult(content='{"overview": "x"}', finish_reason="stop", input_tokens=1, output_tokens=1, cached_tokens=0)

    huge = "x" * 20_000
    run_analysis_doc(
        files_by_path={"a.py": huge}, selected_paths=("a.py",), teaches=(), model_code="m",
        budget=BUDGET, job_id="job-1", chat_fn=chat_fn,
    )
    assert huge not in captured["user"]
    # code_block 부분만 12000자로 잘렸어야 한다 -- 프롬프트 나머지(규칙 설명 등)를
    # 감안해도 20000자 원문이 그대로 들어갔을 때보다는 훨씬 짧아야 한다.
    assert len(captured["user"]) < len(huge)
