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


def test_score_cap_is_gone():
    """2026-08-03 폐기. problem_stage 가 슬롯별 점수를 따로 저장하므로 눌러 담지 않는다."""
    assert not hasattr(scoring, "cap_for")
    assert not hasattr(scoring, "HINT_CAPS")


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


def test_hint_ladder_forbids_narrowing():
    """힌트는 재진술이다. 범위를 좁히면 측정 대상이 바뀌어 비교가 깨진다(PM v2 §4-2).

    2차가 "분해이지 축소가 아니다"라는 것이 가장 흔한 위반 지점이라 문구로 못 박는다.
    """
    specs = {level: spec["spec"] for level, spec in scoring.HINT_LADDER.items()}

    assert "축소가 아니다" in specs[2]
    assert "범위를 줄이거나" in specs[2]
    for spec in specs.values():
        # 정보 추가 0 — 위치·선택지·답의 방향은 힌트가 아니라 답의 일부다
        assert "코드 위치를 짚어주지 말고" in spec
        assert "정답의 집합이 바뀌면 안 된다" in spec


def test_rubric_block_carries_reach_criterion():
    """채점기가 점수와 함께 도달 여부를 판단하려면 3점의 행동 정의가 프롬프트에 있어야 한다.

    매니페스트는 vendor라 못 고친다 — rubric_block은 우리가 만드는 문자열이라 여기로 넣는다.
    """
    block = scoring.rubric_block("L3")

    assert "도달 경계는 3점" in block
    assert "대안 하나를 구체적으로 말했는가" in block


def test_every_axis_has_a_reach_criterion():
    """축 하나라도 기준이 비면 그 축만 다른 잣대로 채점된다."""
    assert set(scoring.REACH_CRITERIA) == set(scoring.AXIS_CODES)