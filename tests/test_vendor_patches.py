""" vendor/ 패치가 살아 있는지 검사한다.

**갱신 절차의 안전장치다.** vendor 갱신은 덮어쓰기 복사라(SOURCE.md §갱신 방법)
복사하면 우리 수정이 전부 사라진다. 사람 기억에 맡기면 반드시 놓치므로,
패치 하나당 테스트 하나를 둬서 **없어지면 여기서 깨지게** 한다.

깨졌다면 둘 중 하나다:
  ① vendor를 갱신하고 PATCHES.md 재적용을 안 했다  → 재적용한다
  ② 팀원이 상류에 반영했다                          → PATCHES.md 항목과 이 테스트를 지운다

②가 목표다. 우리 패치가 영원히 유지되는 것보다 원본이 고쳐지는 쪽이 낫다.
"""
from app.engines.analysis import stages

PATCH_DOC = "app/engines/analysis/vendor/PATCHES.md"


def test_ledger_and_tests_cover_the_same_patches():
    """원장과 테스트가 같은 패치 집합을 다뤄야 한다.

    원장에만 있으면 검사되지 않는 패치이고(갱신 때 조용히 사라진다),
    테스트에만 있으면 근거가 기록되지 않은 수정이다(왜 고쳤는지 아무도 모른다).

    각 패치 테스트는 docstring을 `P-N —`으로 시작한다. 그게 연결 고리다.
    """
    import re
    from pathlib import Path

    doc = Path(PATCH_DOC).read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| (P-\d+) \|", doc, re.M))
    tested = set(re.findall(r'"""(P-\d+) —', Path(__file__).read_text(encoding="utf-8")))

    assert listed == tested, f"원장 {sorted(listed)} vs 테스트 {sorted(tested)}"


def test_p1_grading_returns_reached():
    """P-1 — p04-5가 점수와 도달 여부를 따로 내야 한다 (PM 설계 v2 §5-4).

    사라지면 `Grade.model_reached`가 항상 None이 되고 교차 검증이 조용히 꺼진다.
    채점은 계속 돌기 때문에 **테스트 없이는 알아채지 못한다.**
    """
    template = stages.get_stage("p04-5")["user_template"]

    assert '"reached": true 또는 false' in template
    assert "점수와 따로 판단하라" in template


def test_p2_no_stale_score_cap():
    """P-2 — p04-5가 폐기된 "점수 상한"을 말하면 안 된다.

    상한은 2026-08-03에 폐기됐고 적용하는 코드가 없다. 프롬프트만 남으면
    읽는 사람이 없는 후처리를 찾게 된다. **모델 동작은 안 바뀌므로 테스트가 유일한 감시다.**
    """
    template = stages.get_stage("p04-5")["user_template"]

    assert "상한" not in template
    assert "힌트를 받았다는 이유로 점수를 깎지 마라" in template
