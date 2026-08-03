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
    def _install(topic_list, retry_list=None):
        """`retry_list`를 주면 두 번째 호출(재시도)이 그것을 돌려준다."""
        calls = []

        def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None,
                  extra_user=""):
            calls.append(extra_user)
            payload = retry_list if (len(calls) > 1 and retry_list is not None) else topic_list
            return stages.StageResult(data={"topics": payload},
                                      usages=[{"status": "SUCCEEDED"}])

        monkeypatch.setattr(topics.stages, "call", _call)
        return calls
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
    
def test_unlocatable_symbol_is_retried_once(fake_stage):
    """개념이 코드에 **있는데 LLM이 엉뚱한 symbol을 지목**한 경우를 구제한다.

    한 번에 버리면 있는 개념을 "없음"으로 박게 되고, 그건 오퍼레이터가 고른 개념을
    조용히 빼는 것이다(2026-08-03 PM: "최대한 찾아보고 그래도 없으면 없다고 박아라").
    """
    calls = fake_stage(
        [_topic("t1", "발급", "def ghost():")],          # 1차: 못 찾는 symbol
        retry_list=[_topic("t1", "발급", "def issue_token(user):")],   # 재시도: 진짜 symbol
    )

    s = topics.select(FILES, TEACHES, {"overview": "x"}, [],
                      model_code="m", question_budget=1)

    assert len(calls) == 2                      # 재시도가 실제로 돌았다
    assert "재시도" in calls[1]
    assert [t["teach_id"] for t in s.topics] == ["t1"]
    assert s.topics[0]["code_ref"]["line_start"] == 1


def test_retry_happens_only_once(fake_stage):
    """두 번째도 못 찾으면 실제로 코드에 없을 가능성이 훨씬 높다. 더 태우지 않는다."""
    calls = fake_stage([_topic("t1", "발급", "def ghost():")])

    s = topics.select(FILES, TEACHES, {"overview": "x"}, [],
                      model_code="m", question_budget=1)

    assert len(calls) == 2                      # 1차 + 재시도 1회로 끝
    assert s.topics == []                       # 지어내지 않는다


def test_missing_concept_is_left_out_not_replaced(fake_stage):
    """🔴 teach 앵커 없는 "일반 문제"를 만들지 않는다 (2026-08-03 PM 결정).

    예전에는 부족분을 분석 문서의 다른 판단 지점으로 채웠다 — 그러면 오퍼레이터가
    고른 개념이 조용히 다른 것으로 갈리고, 학생마다 다른 개념을 시험 보게 된다.
    """
    fake_stage([_topic("t1", "발급", "def issue_token(user):")])
    doc = {"decision_points": [
        {"title": "토큰 검증", "file": "app/auth.py",
         "symbol": "def verify(token):", "why_it_matters": "신뢰 경계"},
    ]}

    s = topics.select(FILES, TEACHES, doc, [], model_code="m", question_budget=2)

    assert len(s.topics) == 1                   # 2개를 요청했지만 1개만 나온다
    assert [t["teach_id"] for t in s.topics] == ["t1"]
    assert s.shortfall == 1                     # 부족분은 숨기지 않고 보고한다
    assert not hasattr(topics, "_general_topics")


def test_unmatched_teach_is_reported_explicitly(fake_stage):
    """`―`(문항 없음)을 백엔드가 역산하지 않도록 명시적으로 보낸다.

    problems 길이 차이로는 "몇 개가 없다"까지만 알 수 있고 **어느 개념이 빠졌는지**는
    모른다. 개념별 도달 격자를 그리려면 그 값이 필요하다.
    """
    fake_stage([_topic("t1", "발급", "def issue_token(user):")])

    s = topics.select(FILES, TEACHES, {"overview": "x"}, [],
                      model_code="m", question_budget=2)

    assert [u["teach_id"] for u in s.unmatched] == ["t2"]
    assert s.unmatched[0]["reason"]                 # 화면에 띄울 한 문장이 있다


def test_sdk_only_teach_is_excluded_before_asking():
    """🔴 코드에 없는 API는 물어볼 수 없다. **LLM에게 보여주지도 않는다.**

    실측 2회 재현: LangGraph 레포에 `Runner.run()`을 물으려고 `builder.add_edge(...)`,
    `builder.set_entry_point(...)`를 앵커로 끌어다 붙였다. 프롬프트를 두 번 강화해도
    개수를 채웠다 — 부탁이 아니라 배제로 막는다.
    """
    files = {"pipeline/graph.py": "builder.add_edge('parser', 'supervisor')\n"}

    absent = {"id": "runner", "label": "Agents SDK의 Runner.run() 루프"}
    assert topics._missing_api_token(absent, files) == "Runner.run"

    # 점 없는 개념 이름은 안 막는다 — `매니저 패턴`은 코드에서 `Supervisor`로 나타난다.
    concept = {"id": "manager", "label": "매니저 패턴의 동작 방식"}
    assert topics._missing_api_token(concept, files) is None

    # 코드에 실제로 있는 API는 통과한다.
    present = {"id": "edge", "label": "builder.add_edge 로 노드를 잇는다"}
    assert topics._missing_api_token(present, files) is None


def test_teach_id_echoed_with_the_label_is_recovered():
    """🔴 모델은 준 id를 그대로 안 돌려준다. 목록 줄 전체를 적어 온다.

    2026-08-03 실측: `- {id}: {label}` 목록을 보고 teach_id에
    `"매니저 패턴의 동작 방식: 매니저 패턴의 동작 방식"`을 넣었다. 정확 일치만
    인정하면 코드에 있는 개념이 전부 "없음"으로 나간다.
    """
    ids = {"t1", "t2"}

    assert topics._resolve_teach_id("t1: 인증 토큰", ids) == "t1"
    assert topics._resolve_teach_id("t1", ids) == "t1"

    # 여러 id가 걸리면 어느 것인지 모른다 — 되살리지 않는다.
    assert topics._resolve_teach_id("t1 과 t2 둘 다", ids) == "t1 과 t2 둘 다"


def test_prose_anchor_is_rejected():
    """🔴 문자열 리터럴에는 설계 판단이 없다 — L2·L4가 물을 대상을 잃는다.

    2026-08-03 반복 실행에서 모델이 프롬프트 한복판을 앵커로 잡았다.
    """
    assert topics._is_prose(
        "역할: 당신은 제공받은 PPT 슬라이드 지식 데이터베이스(Context)에만 "
        "철저히 기반하여 답변하는 교육 비서입니다.") is True
    assert topics._is_prose("") is True

    # 코드는 식별자·연산자라 거의 ASCII다. 한글 주석이 꼬리에 붙어도 통과한다.
    assert topics._is_prose("is_relevant = min_score < 0.95") is False
    assert topics._is_prose("worker = state.get('next_worker', 'FINISH')  # 다음 워커") is False
