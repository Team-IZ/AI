""" 채점 설정(T7b). 값 자체보다 "어디서 왔는지"가 중요한 파일이다. """
from app.engines.analysis import scoring


def test_axis_order_matches_contract():
    """축 순서가 곧 진행 순서다. 어긋나면 L3 답변을 L4 기준으로 채점한다."""
    assert scoring.AXIS_CODES == ["L1", "L2", "L3", "L4"]
    assert scoring.AXES["L3"]["label"] == "대안 비교"
    assert scoring.AXES["L4"]["label"] == "반례 대응·한계"


def test_poc_ids_round_trip():
    """프롬프트·모델 응답은 PoC ID를 쓰고 스키마는 L1~L4를 쓴다. 양방향이 맞아야 한다."""
    for code, axis in scoring.AXES.items():
        assert scoring.POC_ID_TO_CODE[axis["poc_id"]] == code


def test_hint_caps_lower_the_ceiling():
    """도움을 받을수록 도달 가능한 최대치가 낮아져야 자력이 측정된다."""
    assert scoring.cap_for(0) == 5
    assert scoring.cap_for(1) == 4
    assert scoring.cap_for(2) == 3


def test_retest_requires_l1_and_l2():
    """L1·L2 둘 다 통과해야 재시험이 아니다 (PoC는 L1만 봐서 틀렸다)."""
    assert scoring.is_retest_target({"L1": False, "L2": False}) is True
    assert scoring.is_retest_target({"L1": True, "L2": False}) is True   # ← PoC가 놓치는 경우
    assert scoring.is_retest_target({"L1": True, "L2": True}) is False
    # L3 실패는 재시험 아님 — 보고서에만 상위 단계 미달로 남는다
    assert scoring.is_retest_target({"L1": True, "L2": True, "L3": False}) is False


def test_axis_intent_block_uses_poc_ids():
    """p04-4가 이 블록을 받고 모델이 그 ID로 답한다."""
    block = scoring.axis_intent_block()

    assert "L1_코드기술" in block
    assert "L4_반례한계" in block