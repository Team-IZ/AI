""" p04-3 선정 후 검증(T7b). LLM이 고른 것을 그대로 믿지 않는 지점이다. """
import pytest

from app.engines.analysis import stages, topics

FILES = {
    "app/auth.py": "def issue_token(user):\n    return sign(user)\n\ndef verify(token):\n    return True\n",
}
TEACHES = [{"id": "t1", "label": "인증 토큰"}, {"id": "t2", "label": "검증"}]


def _topic(teach_id, title, symbol, file="app/auth.py"):
    return {"teach_id": teach_id, "title": title, "rationale": "왜냐하면",
            "code_ref": {"file": file, "symbol": symbol}}


@pytest.fixture
def fake_stage(monkeypatch):
    """stages.call을 가짜로. 매니페스트·LLM 없이 검증 로직만 본다."""
    def _install(topic_list):
        def _call(stage_id, values, *, model_code, max_attempts=2):
            return stages.StageResult(data={"topics": topic_list}, usages=[{"status": "SUCCEEDED"}])
        monkeypatch.setattr(topics.stages, "call", _call)
    return _install


def _select(budget=3):
    return topics.select(FILES, TEACHES, {"overview": "x"}, [],
                         model_code="m", question_budget=budget)


def test_located_topic_carries_resolved_lines(fake_stage):
    fake_stage([_topic("t1", "토큰 발급", "def issue_token(user):")])

    s = _select()

    assert len(s.topics) == 1
    ref = s.topics[0]["code_ref"]
    assert (ref["file"], ref["line_start"], ref["line_end"]) == ("app/auth.py", 1, 2)
    assert ref["snippet"].startswith("def issue_token")


def test_duplicate_teach_is_dropped(fake_stage):
    """같은 teach를 두 번 물으면 검증 축이 겹친다."""
    fake_stage([
        _topic("t1", "토큰 발급", "def issue_token(user):"),
        _topic("t1", "또 토큰", "def verify(token):"),
    ])

    s = _select()

    assert len(s.topics) == 1
    assert any("teach 중복" in d["reason"] for d in s.dropped)


def test_unknown_teach_is_dropped(fake_stage):
    """없는 teach를 참조하는 문제는 만들 수 없다."""
    fake_stage([_topic("t99", "유령", "def issue_token(user):")])

    s = _select()

    assert s.topics == []
    assert any("없는 teach" in d["reason"] for d in s.dropped)


def test_unlocatable_symbol_is_dropped(fake_stage):
    """여기서 안 거르면 질문·힌트 생성이 근거 없이 돌아 LLM 호출만 태운다."""
    fake_stage([_topic("t1", "유령 코드", "def vanished():")])

    s = _select()

    assert s.topics == []
    assert any("찾을 수 없음" in d["reason"] for d in s.dropped)


def test_shortfall_reports_missing_count(fake_stage):
    """teaches가 예산보다 적으면 문제도 적게 나온다 — 억지로 채우지 않는다."""
    fake_stage([_topic("t1", "토큰 발급", "def issue_token(user):")])

    s = _select(budget=3)

    assert s.shortfall == 2