""" p04-6 보고서 — 판정은 결정론, 서술만 LLM.

이 경계가 이 모듈의 전부다. 그래서 테스트도 두 갈래다:
  ① transcript → 축별 결과·도달 단계·재시험이 정확한가 (LLM 무관)
  ② LLM이 뭘 주든/못 주든 판정이 흔들리지 않는가
"""
from app.engines.analysis import report, stages

TEACHES = [
    {"id": "t1", "label": "예외 처리", "unit_id": "u1", "source_pages": [36, 46]},
    {"id": "t2", "label": "결제 흐름", "unit_id": "u2", "source_pages": [12]},
]


def _turn(axis: str, confirmed: int, best: int | None = None, hint: str | None = None):
    return {
        "problem_id": "p-1", "axis_code": axis, "question_text": f"{axis} 질문",
        "answer_text": "답변", "answered_at": "2026-08-02T00:00:00Z",
        "best_score": best if best is not None else confirmed,
        "confirmed_score": confirmed, "attempt_count": 1,
        "hint_text": hint, "autonomy": "SELF",
    }


def _fake(monkeypatch, data):
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        _call.values = values
        return stages.StageResult(data=data, usages=[{"status": "SUCCEEDED"}])

    monkeypatch.setattr(report.stages, "call", _call)
    return _call


NARRATIVE = {
    "summary": "코드가 하는 일은 설명했으나 대안 비교에서 막혔습니다.",
    "strengths": [{"axis": "L1", "detail": "데이터 흐름을 정확히 짚었습니다"}],
    "gaps": [{"axis": "L3", "detail": "대안을 대지 못했습니다",
              "teach_id": "t1", "study_pointer": "Unit u1 · 36~46쪽"}],
    "retest": [{"topic_title": "결제 분기", "reason": "L3에서 막힘"}],
    "autonomy_note": "L2는 힌트를 받고서야 도달했습니다.",
}


# ── ① 판정 (결정론) ───────────────────────────────────────────────────────────

def test_last_turn_of_an_axis_is_the_recorded_result():
    """힌트 후 재질의도 한 턴이다. 축의 결과는 **마지막 시도**의 점수다."""
    rows = report.summarize_stages([
        _turn("L1", 2), _turn("L1", 2, hint="힌트1"), _turn("L1", 4, best=5, hint="힌트2"),
    ])

    l1 = rows[0]
    assert l1["confirmed_score"] == 4
    assert l1["best_score"] == 5
    assert l1["attempt_count"] == 3
    assert l1["hints_used"] == 2
    assert l1["passed"] is True


def test_unreached_axes_are_still_four_rows():
    """DB problem_stage가 문제당 4행이다. 빼고 보내면 Spring이 순서로 짐작한다."""
    rows = report.summarize_stages([_turn("L1", 2)])

    assert [r["axis_code"] for r in rows] == ["L1", "L2", "L3", "L4"]
    for r in rows[1:]:
        assert r["attempt_count"] == 0
        assert r["passed"] is False
        assert "confirmed_score" not in r      # 점수를 지어내지 않는다


def test_reached_stage_counts_consecutive_passes():
    """계단이라 건너뛴 통과는 없다."""
    assert report.reached_stage(report.summarize_stages([])) == 0
    assert report.reached_stage(report.summarize_stages([_turn("L1", 4)])) == 1
    assert report.reached_stage(report.summarize_stages(
        [_turn("L1", 4), _turn("L2", 4), _turn("L3", 2)])) == 2


def test_retest_needs_l1_and_l2(monkeypatch):
    """재시험은 모델에게 묻지 않는다 — L1·L2 통과 여부로 결정된다."""
    _fake(monkeypatch, NARRATIVE)

    l2_failed = report.build("p-1", 1, [_turn("L1", 4), _turn("L2", 2)], model_code="m")
    l3_failed = report.build("p-1", 1,
                             [_turn("L1", 4), _turn("L2", 4), _turn("L3", 2)], model_code="m")

    assert l2_failed.retest is True
    assert l3_failed.retest is False      # 상위 단계 미달은 재시험이 아니다


# ── ② 서술 (LLM) ──────────────────────────────────────────────────────────────

def test_markdown_has_no_numeric_scores(monkeypatch):
    """화면에 숫자 점수가 없다(PM 설계 v2 §10-3). 도달 단계와 서술만 쓴다."""
    _fake(monkeypatch, NARRATIVE)

    md = report.build("p-1", 1, [_turn("L1", 4), _turn("L2", 3)], model_code="m").report_markdown

    assert "2단 / 4단" in md
    assert "대안을 대지 못했습니다" in md
    assert "Unit u1 · 36~46쪽" in md
    for forbidden in ("4점", "3점", "총점", "평균"):
        assert forbidden not in md


def test_unreached_axes_are_explained_not_scored(monkeypatch):
    """'못한 것'과 '안 물어본 것'을 섞으면 안 된다(§5-1 ②)."""
    _fake(monkeypatch, NARRATIVE)

    md = report.build("p-1", 1, [_turn("L1", 2)], model_code="m").report_markdown

    assert "물어보지 않은 것" in md


def test_invented_teach_id_is_dropped(monkeypatch):
    """없는 교안을 복습하라고 가리키면 학생이 못 찾는 페이지를 뒤진다."""
    _fake(monkeypatch, {**NARRATIVE,
                        "gaps": [{"axis": "L3", "detail": "d", "teach_id": "t-nope"}]})

    built = report.build("p-1", 1, [_turn("L1", 4)], model_code="m", teaches=TEACHES)

    assert built.curriculum_refs == []


def test_curriculum_refs_come_from_gaps(monkeypatch):
    _fake(monkeypatch, NARRATIVE)

    refs = report.build("p-1", 1, [_turn("L1", 4)], model_code="m",
                        teaches=TEACHES).curriculum_refs

    assert refs == [{"teachId": "t1", "unitId": "u1", "sourcePages": [36, 46]}]


def test_llm_failure_still_produces_a_report(monkeypatch):
    """서술만 비는 것이라 통째로 실패시키면 확정된 판정까지 잃는다."""
    def _boom(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        raise stages.StageError("p04-6: 터짐", [{"status": "FAILED"}])

    monkeypatch.setattr(report.stages, "call", _boom)

    built = report.build("p-1", 1, [_turn("L1", 4), _turn("L2", 2)], model_code="m")

    assert built.problem["reached_stage"] == 1     # 판정은 살아 있다
    assert built.retest is True
    assert "서술 생성에 실패" in built.report_markdown
    assert built.usages                            # 태운 토큰은 남는다


def test_transcript_block_shows_hint_usage(monkeypatch):
    """힌트를 몇 개 받고 답했는지가 보여야 모델이 자력을 서술한다."""
    call = _fake(monkeypatch, NARRATIVE)

    report.build("p-1", 1, [_turn("L1", 2), _turn("L1", 4, hint="L1 힌트 1")],
                 model_code="m")

    block = call.values["transcript_block"]
    assert "힌트 없이 답함" in block
    assert "직전 힌트: L1 힌트 1" in block
