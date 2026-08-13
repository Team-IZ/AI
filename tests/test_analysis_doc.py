""" p04-1 분석 문서 생성. 파이프라인의 첫 LLM 단계다.

여기가 비면 문제 선정·질문·보고서가 전부 빈다. 그래서 "생성물을 믿지 않는" 검사가
두 군데 있다 — 필수 필드(overview)와 근거 없는 decision_point.
"""
import pytest

from app.engines.analysis import analysis_doc, stages
from app.schemas.analysis import AnalysisDocument

FILES = {
    "app/pay.py": "import os\n\n\ndef pay(order, method):\n    if method == 'card':\n        return charge(order)\n    return None\n",
    "app/util.py": "def noop():\n    pass\n",
}

TEACHES = [{"id": "t1", "label": "예외 처리"}, {"id": "t2", "label": "결제 흐름"}]

CANDIDATES = [
    {"source_path": "app/pay.py", "problem_type": "RISK_POINT", "summary": "결제 분기"},
]


def _doc(**overrides):
    base = {
        "overview": "결제 요청을 카드 결제로 넘기는 코드다.",
        "structure": [{"area": "결제", "files": ["app/pay.py"], "role": "결제 분기"}],
        "decision_points": [
            {
                "title": "결제 수단 분기",
                "file": "app/pay.py",
                "symbol": "def pay(order, method):",
                "why_it_matters": "다른 수단이 오면 조용히 None이 된다",
                "related_teach": "t1",
            }
        ],
        "risks": ["미지원 수단이 무시된다"],
    }
    base.update(overrides)
    return base


def _fake(monkeypatch, data):
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        _call.values = values
        return stages.StageResult(data=data, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(analysis_doc.stages, "call", _call)
    return _call


def test_builds_document_in_llm_shape(monkeypatch):
    """반환값은 LLM 원본 모양이다 — 다운스트림 프롬프트에 그대로 실린다."""
    _fake(monkeypatch, _doc())

    result = analysis_doc.build(FILES, TEACHES, CANDIDATES, model_code="m")

    dp = result.document["decision_points"][0]
    assert dp["file"] == "app/pay.py"          # source_path가 아니다
    assert dp["symbol"] == "def pay(order, method):"
    assert "line_start" not in dp              # 줄 번호는 아직 없다
    assert result.usages


def test_prompt_carries_teaches_findings_and_code(monkeypatch):
    """세 블록이 다 실려야 모델이 근거를 갖는다. 하나라도 비면 지어낸다."""
    call = _fake(monkeypatch, _doc())

    analysis_doc.build(FILES, TEACHES, CANDIDATES, model_code="m")

    assert "t1: 예외 처리" in call.values["teaches_block"]
    assert "app/pay.py: 결제 분기" in call.values["findings_block"]
    assert "def pay(order, method):" in call.values["code_block"]


def test_empty_overview_stops_the_pipeline(monkeypatch):
    """overview는 AnalysisDocument 필수 필드다. 비면 뒤 단계 토큰을 태우기 전에 끊는다."""
    _fake(monkeypatch, _doc(overview="   "))

    with pytest.raises(stages.StageError):
        analysis_doc.build(FILES, TEACHES, CANDIDATES, model_code="m")


def test_decision_point_without_anchor_is_dropped(monkeypatch):
    """file·symbol이 없으면 줄 번호를 못 뽑는다 — 근거로 못 쓰므로 버리고 이유를 남긴다."""
    _fake(monkeypatch, _doc(decision_points=[
        {"title": "앵커 없음", "why_it_matters": "..."},
        _doc()["decision_points"][0],
    ]))

    result = analysis_doc.build(FILES, TEACHES, CANDIDATES, model_code="m")

    assert len(result.document["decision_points"]) == 1
    assert "앵커 없음" in result.dropped[0]


def test_invented_teach_id_is_cleared(monkeypatch):
    """모델이 지어낸 teach id를 남기면 보고서가 없는 교안을 복습하라고 가리킨다."""
    _fake(monkeypatch, _doc(decision_points=[
        {**_doc()["decision_points"][0], "related_teach": "t-nope"},
    ]))

    result = analysis_doc.build(FILES, TEACHES, CANDIDATES, model_code="m")

    assert result.document["decision_points"][0]["related_teach"] is None


def test_to_schema_resolves_line_numbers():
    """symbol을 실제 파일에서 찾아 줄 번호를 산정한다. LLM이 센 값을 쓰지 않는다."""
    schema = analysis_doc.to_schema(_doc(), FILES)

    dp = schema["decision_points"][0]
    assert dp["evidence_valid"] is True
    assert dp["source_path"] == "app/pay.py"
    assert dp["line_start"] == 4               # def pay(...) 가 4번째 줄
    assert dp["line_end"] >= dp["line_start"]

    # 응답 스키마를 그대로 통과해야 한다.
    AnalysisDocument.model_validate(schema)


def test_unfound_symbol_has_no_line_numbers():
    """못 찾은 근거에 줄 번호가 남으면 백엔드가 지어낸 위치를 화면에 그린다."""
    doc = _doc(decision_points=[
        {**_doc()["decision_points"][0], "symbol": "def refund(order):"},
    ])

    schema = analysis_doc.to_schema(doc, FILES)

    dp = schema["decision_points"][0]
    assert dp["evidence_valid"] is False
    assert dp["line_start"] is None and dp["line_end"] is None
    assert dp["source_path"] == "app/pay.py"   # 검수 단서로 모델이 지목한 경로는 남긴다
    AnalysisDocument.model_validate(schema)


def test_string_params_fill_their_placeholders(monkeypatch):
    """문자열 param은 프롬프트 자리표시자이기도 하다 — 안 채우면 그대로 나간다.

    2026-08-02 실측: p01-2가 "KT AIVLE School {course_label} curriculum"으로 나갔고
    교안 결과가 한/영 혼재로 돌아왔다. 에러는 안 났다.
    """
    seen = {}

    def _chat(model_code, messages, **kwargs):
        seen["user"] = messages[1]["content"]
        raise stages.client.LlmError("stop", {"status": "FAILED", "failure_code": "TIMEOUT"})

    monkeypatch.setattr(stages.client, "chat", _chat)

    with pytest.raises(stages.StageError):
        stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문"},
                    model_code="m", max_attempts=1)

    assert "{course_label}" not in seen["user"]
    assert "KT AIVLE School Java curriculum" in seen["user"]   # 매니페스트 기본값


def test_caller_value_beats_the_manifest_default(monkeypatch):
    seen = {}

    def _chat(model_code, messages, **kwargs):
        seen["user"] = messages[1]["content"]
        raise stages.client.LlmError("stop", {"status": "FAILED", "failure_code": "TIMEOUT"})

    monkeypatch.setattr(stages.client, "chat", _chat)

    with pytest.raises(stages.StageError):
        stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문",
                              "course_label": "SQL"}, model_code="m", max_attempts=1)

    assert "KT AIVLE School SQL curriculum" in seen["user"]


def test_context_overflow_budget_rises_only_once(monkeypatch):
    """CONTEXT_OVERFLOW 재시도의 예산 상향은 2배까지다.

    무한 2배는 nemotron의 폭주 사고를 키운다 -- 예산을 주는 만큼 사고에 쓰고 content는
    그대로 비어서 실패 한 번의 비용만 커지고(2026-08-13 실측: 1500에서 63~186초,
    6000에서 187초), 커진 호출이 공급자 게이트웨이 상한에 닿으면 HTTP 504가 된다.
    """
    budgets = []

    def _chat(model_code, messages, **kwargs):
        budgets.append(kwargs["max_tokens"])
        raise stages.client.LlmError(
            "잘림", {"status": "FAILED", "failure_code": "CONTEXT_OVERFLOW"})

    monkeypatch.setattr(stages.client, "chat", _chat)

    with pytest.raises(stages.StageError):
        stages.call("p01-2", {"chunk_range": "1-2", "chunk_text": "본문"},
                    model_code="m", max_attempts=4)

    first = budgets[0]
    assert budgets == [first, first * 2, first * 2, first * 2], budgets
