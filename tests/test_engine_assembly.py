""" 조립기 — 부품을 엮어 AnalysisResult가 실제로 나오는지.

**LLM은 전부 가짜다.** 여기서 재는 것은 모델 품질이 아니라 **연결**이다:
어느 단계의 출력이 다음 단계의 입력 모양과 맞는가, 최종 결과가 계약(스키마)을
통과하는가. 실호출은 이 테스트가 초록인 뒤에 한다.
"""
import io
import zipfile
from copy import deepcopy

import pytest

from app.engines.analysis import engine as engine_mod
from app.engines.analysis import stages
from app.schemas.analysis import AnalysisResult

SOURCE = (
    "import os\n"
    "\n"
    "\n"
    "def pay(order, method):\n"
    "    if method == 'card':\n"
    "        return charge(order)\n"
    "    return None\n"
)


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("repo/app/pay.py", SOURCE)
    return buf.getvalue()


REQUEST = {
    "method": "ZIP_WITH_GITLOG",
    "extraction_scope": "TOTAL",
    "question_budget": 1,
    "focus_items": [{"id": "focus-1", "name": "결제"}],
    "requirements": [{"requirement_id": "r1", "text": "결제를 처리한다"}],
    "teaches": [{"id": "t1", "label": "결제 흐름"}],
    "provider_model_code": "fake-model",
}

# 스테이지별 가짜 응답. 실제 모델이 내는 모양을 그대로 흉내낸다.
_RESPONSES = {
    "p04-1": {
        "overview": "결제 요청을 카드 결제로 넘기는 코드다.",
        "structure": [{"area": "결제", "files": ["app/pay.py"], "role": "결제 분기"}],
        "decision_points": [{
            "title": "결제 수단 분기", "file": "app/pay.py",
            "symbol": "def pay(order, method):",
            "why_it_matters": "다른 수단이 오면 조용히 None이 된다",
            "related_teach": "t1",
        }],
        "risks": ["미지원 수단이 무시된다"],
    },
    "p04-2": {"results": [{"requirement": "결제를 처리한다", "verdict": "P",
                           "evidence": {"file": "app/pay.py", "lines": [4, 7],
                                        "quote": "def pay(order, method):"}}]},
    "p04-3": {"topics": [{"teach_id": "t1", "title": "결제 수단 분기",
                          "rationale": "판단이 개입된 지점",
                          "code_ref": {"file": "app/pay.py",
                                       "symbol": "def pay(order, method):"}}]},
    "p04-4": {"levels": [
        {"axis": "L1_코드기술", "question": "이 함수가 무엇을 하나요?"},
        {"axis": "L2_설계논리", "question": "왜 이렇게 설계했나요?"},
        {"axis": "L3_대안", "question": "다른 방법과 비교하면 어떤가요?"},
        {"axis": "L4_반례한계", "question": "언제 깨지나요?"},
    ]},
    "p04-7": {"hint": "같은 질문을 다시 드릴게요. 짧은 문장으로 나눠 말해 주세요."},
}


@pytest.fixture
def fake_llm(monkeypatch):
    """모든 스테이지 호출을 가로챈다. 호출된 스테이지 순서를 기록해 돌려준다."""
    called: list[str] = []

    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None,
              extra_user=""):
        called.append(stage_id)
        # deepcopy가 아니면 앞 테스트가 이 dict을 고쳐놓는다 — topics.select가
        # code_ref를 산정 결과로 갈아끼우면서 symbol을 지운다. 그러면 다음 테스트는
        # 근거 검증에 실패해 조용히 일반 문제 폴백으로 떨어진다(에러 없이 결과만 다름).
        return stages.StageResult(data=deepcopy(_RESPONSES[stage_id]),
                                  usages=[{"status": "SUCCEEDED", "model_code": model_code,
                                           "input_token_count": 10, "output_token_count": 5,
                                           "cached_token_count": 0, "failure_code": None,
                                           "latency_ms": 100,
                                           "occurred_at": "2026-08-02T00:00:00Z"}])

    for mod in ("analysis_doc", "requirements", "topics", "questions", "hints"):
        monkeypatch.setattr(f"app.engines.analysis.{mod}.stages.call", _call)
    return called


def test_pipeline_produces_a_valid_analysis_result(fake_llm):
    """전 구간이 이어져 계약을 통과해야 한다. 여기서 깨지면 실호출은 볼 것도 없다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")                       # job 계층이 떼어 가는 필드다

    result = AnalysisResult.model_validate(raw)

    assert len(result.problems) == 1
    problem = result.problems[0]
    assert problem.source_path == "app/pay.py"
    assert problem.line_start == 4            # symbol을 실제 파일에서 찾아 산정
    assert result.analysis_document.decision_points[0].evidence_valid is True


def test_teach_id_is_carried_into_the_problem(fake_llm):
    """선정은 teach 기준인데 조립기가 그 값을 안 실으면 연결이 응답에서 끊긴다.

    엔진은 이미 topic["teach_id"]로 질문·힌트를 만든다 — 한 줄이 빠져 있었을 뿐이다.
    """
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")

    problem = AnalysisResult.model_validate(raw).problems[0]

    assert problem.teach_id == "t1"


def test_provider_model_code_is_used(fake_llm):
    """요청 필드는 providerModelCode다 — 공급자에 그대로 넘길 문자열이라야 호출이 된다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())

    assert {u["model_code"] for u in raw["ai_usage"]} == {"fake-model"}


def test_all_four_axes_are_frozen_with_two_hints_each(fake_llm):
    """전면 동결 — 문제당 질문 4개 + 힌트 8개가 여기서 다 만들어져 나간다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")

    stage_rows = AnalysisResult.model_validate(raw).problems[0].stages

    assert [s.axis_code for s in stage_rows] == ["L1", "L2", "L3", "L4"]
    for s in stage_rows:
        assert s.question_text
        assert [h.hint_level for h in s.hints] == [1, 2]


def test_every_stage_is_called(fake_llm):
    """단계 하나가 조용히 빠지면 결과는 나오는데 내용이 빈다."""
    engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())

    assert set(fake_llm) == {"p04-1", "p04-2", "p04-3", "p04-4", "p04-7"}
    # 힌트는 축 4개 × 2개 = 8콜
    assert fake_llm.count("p04-7") == 8


def test_usage_is_stamped_with_feature_code(fake_llm):
    """`llm/client.py`는 어느 기능이 불렀는지 모른다 — 엔진이 찍어야 원장이 성립한다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())

    codes = {u["feature_code"] for u in raw["ai_usage"]}
    assert codes == {"CODE_ANALYSIS", "QUESTION_GENERATION"}


def test_requirement_failure_does_not_kill_the_analysis(monkeypatch, fake_llm):
    """요구사항 판정은 문답과 독립이다(PM 설계 v2 §8-3). 깨져도 분석은 나가야 한다."""
    def _boom(stage_id, values, *, model_code, max_attempts=2, timeout_s=None,
              extra_user=""):
        if stage_id == "p04-2":
            raise stages.StageError("p04-2: 터짐", [])
        return stages.StageResult(data=deepcopy(_RESPONSES[stage_id]), usages=[])

    for mod in ("analysis_doc", "requirements", "topics", "questions", "hints"):
        monkeypatch.setattr(f"app.engines.analysis.{mod}.stages.call", _boom)

    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")
    result = AnalysisResult.model_validate(raw)

    # 개수는 유지된다 — jobs.py가 요청 requirements와 1:1을 검사한다
    assert len(result.requirement_results) == 1
    assert result.requirement_results[0].verdict == "FAIL"
    assert "판정 실패" in result.requirement_results[0].note
    assert len(result.problems) == 1        # 문답은 살아 있다


def test_focus_item_id_is_echoed(fake_llm):
    """C-1 확정 — 강사가 준 focusItems[].id를 그대로 돌려준다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")

    assert AnalysisResult.model_validate(raw).problems[0].question_focus_item_id == "focus-1"


def test_github_url_without_repo_url_fails_loudly(fake_llm):
    """받아올 곳이 없으면 끊는다. 빈 결과를 내면 '문제 0개'가 정상처럼 보인다."""
    # AnalysisFailed로 감싸 나온다 — 원장을 들고 나오려고 엔진이 전부 감싼다.
    with pytest.raises(engine_mod.AnalysisFailed, match="repoUrl"):
        engine_mod.RealAnalysisEngine().analyze({**REQUEST, "method": "GITHUB_URL"}, None)


def test_failure_keeps_the_ledger(fake_llm, monkeypatch):
    """실패해도 태운 콜은 남는다. **없으면 백엔드가 비용을 못 매긴다.**"""
    def boom(*args, **kwargs):
        raise engine_mod.stages.StageError("p04-3: JSON 파싱 실패", [{"status": "FAILED"}])

    monkeypatch.setattr(engine_mod.topics, "select", boom)
    with pytest.raises(engine_mod.AnalysisFailed) as exc:
        engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())

    # p04-1(분석 문서)이 태운 것 + 터진 p04-3의 것이 함께 있어야 한다.
    assert len(exc.value.ai_usage) > 1
    assert exc.value.ai_usage[-1]["feature_code"] == "QUESTION_GENERATION"


def test_code_snippet_is_the_whole_file(fake_llm):
    """파편만 주면 학생이 판단할 재료가 없다. 화면에 띄울 것은 파일 전체다."""
    files = {"a/b.py": "\n".join(f"line {i}" for i in range(1, 41))}
    ref = {"file": "a/b.py", "line_start": 10, "line_end": 12}

    assert engine_mod._display_source(files, ref, "fragment") == files["a/b.py"]


def test_oversized_file_falls_back_to_the_fragment(fake_llm):
    """통째로 띄우는 것이 도움이 안 되는 크기다. 잘라서 줄 번호를 어긋나게 하지 않는다."""
    files = {"a/b.py": "x" * (engine_mod._MAX_DISPLAY_CHARS + 1)}

    assert engine_mod._display_source(files, {"file": "a/b.py"}, "fragment") == "fragment"


def test_unmatched_teach_reaches_the_response(monkeypatch, fake_llm):
    """개념이 코드에 없으면 `―`로 그릴 값이 응답에 실려야 한다."""
    request = {**REQUEST, "question_budget": 2,
               "teaches": [{"id": "t1", "label": "결제 흐름"},
                           {"id": "t-nope", "label": "동시성 제어"}]}

    raw = engine_mod.RealAnalysisEngine().analyze(request, _zip())
    raw.pop("ai_usage")
    result = AnalysisResult.model_validate(raw)

    assert [u.teach_id for u in result.unmatched_teaches] == ["t-nope"]
    assert result.unmatched_teaches[0].reason


def test_references_are_filled(fake_llm):
    """지금까지 항상 빈 배열이었다. 채우는 데 필요한 것이 다 있었는데 조립을 안 했다."""
    raw = engine_mod.RealAnalysisEngine().analyze(REQUEST, _zip())
    raw.pop("ai_usage")

    refs = AnalysisResult.model_validate(raw).problems[0].references
    kinds = [r.reference_type for r in refs]

    assert kinds.count("PRIMARY_BLOCK") == 1
    assert kinds.count("QUESTION_HIGHLIGHT") == 4          # 4축
    assert kinds.count("CURRICULUM_EVIDENCE") == 1         # teach 가 붙은 문제다
    assert [r.display_order for r in refs] == list(range(1, len(refs) + 1))

    highlight = next(r for r in refs if r.reference_type == "QUESTION_HIGHLIGHT")
    assert highlight.axis_code == "L1"                     # 축이 없으면 DB CHECK 위반
    curriculum = next(r for r in refs if r.reference_type == "CURRICULUM_EVIDENCE")
    assert curriculum.teach_id == "t1"
    assert curriculum.path is None                         # 교안 근거는 코드 라인이 없다


def test_prefetched_root_skips_materialize(monkeypatch, tmp_path, fake_llm):
    """analysisInput 경로(D2, jobs._run_via_analysis_input)에서는 엔진이 스스로

    클론/압축해제하지 않고 이미 fetch된 경로를 그대로 스캔해야 한다 -- 안 그러면
    검증했던 코드와 다른 코드(브랜치가 그 사이 움직인)를 분석하게 된다.
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pay.py").write_text(SOURCE)

    def _boom(*args, **kwargs):
        raise AssertionError("prefetched_root가 있으면 materialize()를 부르면 안 된다")

    monkeypatch.setattr(engine_mod.materialize, "materialize", _boom)

    raw = engine_mod.RealAnalysisEngine().analyze(
        {**REQUEST, "method": "GITHUB_URL"}, None, prefetched_root=str(tmp_path),
    )
    raw.pop("ai_usage")
    result = AnalysisResult.model_validate(raw)

    assert result.problems[0].source_path == "app/pay.py"
    # prefetched_root는 git repo가 아닐 수도 있다 -- head_sha()가 조용히 None으로
    # 물러나야지 여기서 터지면 안 된다(materialize.head_sha의 기존 계약).
    assert result.commit_sha is None


def test_old_reference_types_are_gone():
    """옛 값을 보내면 새 정의서 CHECK 에 걸려 INSERT 가 깨진다."""
    from app.schemas.analysis import ReferenceType
    from typing import get_args

    assert set(get_args(ReferenceType)) == {
        "PRIMARY_BLOCK", "QUESTION_HIGHLIGHT", "CALLER",
        "RELATED_CONTEXT", "CURRICULUM_EVIDENCE",
    }
