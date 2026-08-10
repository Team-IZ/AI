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


def _stage(axis: str, *scores: int, status: str | None = None):
    """축 하나의 `problem_stage` 행. 점수를 준 순서대로 질문·힌트1·힌트2 슬롯에 넣는다.

    🔴 2026-08-10 모양 변경: 예전엔 턴 목록을 넘기면 엔진이 접었다. 이제 백엔드가
    이미 접힌 행을 보낸다 — `_stage("L1", 2, 2, 4)`가 "질문 2점 → 힌트1 후 2점 →
    힌트2 후 4점"이고, 예전 `_turn` 3개와 같은 뜻이다.
    """
    slots = ("question", "first_hint", "second_hint")
    row: dict = {"axis_code": axis, "question_text": f"{axis} 질문"}
    if status:
        row["status"] = status
    for i, score in enumerate(scores):
        row[f"{slots[i]}_score"] = score
        row[f"{slots[i]}_passed"] = score >= 3
        row[f"{slots[i]}_answer_text"] = "답변"
        if i:
            row[f"{slots[i]}_text"] = f"힌트{i}"
    return row


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

def test_each_attempt_lands_in_its_own_slot():
    """한 축의 답변 3개가 problem_stage 한 행의 서로 다른 슬롯에 들어간다.

    마지막 시도만 남기면 "힌트 없이 몇 점이었나"가 사라져 자력 판정을 못 한다.
    """
    rows = report.summarize_stages([_stage("L1", 2, 2, 4)])

    l1 = rows[0]
    assert (l1["question_score"], l1["question_passed"]) == (2, False)
    assert (l1["first_hint_score"], l1["first_hint_passed"]) == (2, False)
    assert (l1["second_hint_score"], l1["second_hint_passed"]) == (4, True)
    assert l1["status"] == "PASSED"


def test_backend_status_wins_over_the_computed_one():
    """🔴 `NOT_ANSWERED`는 AI가 만들 수 없다 — 점수 기록만 보면 "안 물어봤다"와 구분이 안 된다.

    세션 진행 사실을 아는 쪽은 백엔드다. 보내주면 그대로 쓴다(2026-08-10 확정).
    """
    rows = report.summarize_stages([_stage("L2", status="NOT_ANSWERED")])

    l2 = next(r for r in rows if r["axis_code"] == "L2")
    assert l2["status"] == "NOT_ANSWERED"


def test_status_falls_back_to_the_computed_one():
    """백엔드가 status를 안 보내면 점수 유무로 계산한다 — 빈 값이 NOT_REACHED로 굳지 않게."""
    rows = report.summarize_stages([_stage("L1", 4), _stage("L2", 2)])

    assert rows[0]["status"] == "PASSED"        # 통과 슬롯이 있다
    assert rows[1]["status"] == "NOT_PASSED"    # 답은 했는데 미달
    assert rows[2]["status"] == "NOT_REACHED"   # 아예 안 물어봄


def test_unreached_axes_are_still_four_rows():
    """DB problem_stage가 문제당 4행이다. 빼고 보내면 Spring이 순서로 짐작한다."""
    rows = report.summarize_stages([_stage("L1", 2)])

    assert [r["axis_code"] for r in rows] == ["L1", "L2", "L3", "L4"]
    for r in rows[1:]:
        assert r["status"] == "NOT_REACHED"
        assert report.stage_passed(r) is False
        assert "question_score" not in r        # 점수를 지어내지 않는다


def test_reached_stage_counts_consecutive_passes():
    """계단이라 건너뛴 통과는 없다."""
    assert report.reached_stage(report.summarize_stages([])) == 0
    assert report.reached_stage(report.summarize_stages([_stage("L1", 4)])) == 1
    assert report.reached_stage(report.summarize_stages(
        [_stage("L1", 4), _stage("L2", 4), _stage("L3", 2)])) == 2


def test_retest_needs_l1_and_l2(monkeypatch):
    """재시험은 모델에게 묻지 않는다 — L1·L2 통과 여부로 결정된다."""
    _fake(monkeypatch, NARRATIVE)

    l2_failed = report.build("p-1", 1, [_stage("L1", 4), _stage("L2", 2)], model_code="m")
    l3_failed = report.build("p-1", 1,
                             [_stage("L1", 4), _stage("L2", 4), _stage("L3", 2)], model_code="m")

    assert l2_failed.retest is True
    assert l3_failed.retest is False      # 상위 단계 미달은 재시험이 아니다


# ── ② 서술 (LLM) ──────────────────────────────────────────────────────────────

def test_markdown_has_no_numeric_scores(monkeypatch):
    """화면에 숫자 점수가 없다(PM 설계 v2 §10-3). 도달 단계와 서술만 쓴다."""
    _fake(monkeypatch, NARRATIVE)

    md = report.build("p-1", 1, [_stage("L1", 4), _stage("L2", 3)], model_code="m",
                      teaches=[{"id": "t1", "label": "흐름",
                                "unit_id": "u1", "source_pages": [36, 46]}]).report_markdown

    assert "2단 / 4단" in md
    assert "대안을 대지 못했습니다" in md
    # 복습 위치는 요청 teaches에서 만든다 — 모델의 study_pointer를 그대로 쓰지 않는다.
    assert "단원 u1" in md and "p.36, 46" in md
    assert "36~46쪽" not in md
    for forbidden in ("4점", "3점", "총점", "평균"):
        assert forbidden not in md


def test_unreached_axes_are_explained_not_scored(monkeypatch):
    """'못한 것'과 '안 물어본 것'을 섞으면 안 된다(§5-1 ②)."""
    _fake(monkeypatch, NARRATIVE)

    md = report.build("p-1", 1, [_stage("L1", 2)], model_code="m").report_markdown

    assert "물어보지 않은 것" in md


def test_invented_teach_id_is_dropped(monkeypatch):
    """없는 교안을 복습하라고 가리키면 학생이 못 찾는 페이지를 뒤진다."""
    _fake(monkeypatch, {**NARRATIVE,
                        "gaps": [{"axis": "L3", "detail": "d", "teach_id": "t-nope"}]})

    built = report.build("p-1", 1, [_stage("L1", 4)], model_code="m", teaches=TEACHES)

    assert built.curriculum_refs == []
    # 구조화 필드에도 새면 안 된다 — 두 필드가 다른 교안을 가리키게 된다.
    assert built.narrative["gaps"][0]["teach_id"] is None


def test_narrative_mirrors_the_markdown(monkeypatch):
    """마크다운과 구조화 필드는 같은 내용이다. 프론트가 헤딩을 다시 파싱하지 않도록."""
    _fake(monkeypatch, NARRATIVE)

    built = report.build("p-1", 1, [_stage("L1", 4)], model_code="m", teaches=TEACHES)

    assert built.narrative["summary"] == NARRATIVE["summary"]
    assert built.narrative["gaps"][0]["teach_id"] == "t1"
    # 앞 단계에서 끝나 안 물어본 축. "못한 것"이 아니라 "안 물어본 것"이다.
    assert built.narrative["unreached_axes"] == ["L2", "L3", "L4"]


def test_failed_narrative_is_empty_not_an_apology(monkeypatch):
    """실패 안내 문장을 summary로 내보내면 백엔드가 '요약이 있다'로 읽는다."""
    def _boom(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        raise stages.StageError("p04-6: 터짐", [{"status": "FAILED"}])

    monkeypatch.setattr(report.stages, "call", _boom)

    built = report.build("p-1", 1, [_stage("L1", 4)], model_code="m", teaches=TEACHES)

    assert built.narrative_failed is True
    assert built.narrative["summary"] is None
    assert built.narrative["strengths"] == built.narrative["gaps"] == []
    assert "서술 생성에 실패" in built.report_markdown    # 마크다운에는 사유가 남는다


def test_curriculum_refs_come_from_gaps(monkeypatch):
    _fake(monkeypatch, NARRATIVE)

    refs = report.build("p-1", 1, [_stage("L1", 4)], model_code="m",
                        teaches=TEACHES).curriculum_refs

    assert refs == [{"teachId": "t1", "unitId": "u1", "sourcePages": [36, 46]}]


def test_llm_failure_still_produces_a_report(monkeypatch):
    """서술만 비는 것이라 통째로 실패시키면 확정된 판정까지 잃는다."""
    def _boom(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        raise stages.StageError("p04-6: 터짐", [{"status": "FAILED"}])

    monkeypatch.setattr(report.stages, "call", _boom)

    built = report.build("p-1", 1, [_stage("L1", 4), _stage("L2", 2)], model_code="m")

    assert built.problem["reached_stage"] == 1     # 판정은 살아 있다
    assert built.retest is True
    assert "서술 생성에 실패" in built.report_markdown
    assert built.usages                            # 태운 토큰은 남는다


def test_transcript_block_shows_hint_usage(monkeypatch):
    """힌트를 몇 개 받고 답했는지가 보여야 모델이 자력을 서술한다."""
    call = _fake(monkeypatch, NARRATIVE)

    report.build("p-1", 1, [_stage("L1", 2, 4)],
                 model_code="m")

    block = call.values["transcript_block"]
    # 접힌 행 하나가 시도 2개로 펼쳐진다 — 접힌 채로 주면 모델이 "바로 답했다"와
    # "힌트 받고 답했다"를 같은 줄에서 읽어야 해서 자력 서술이 뭉개진다.
    assert "힌트 없이 답함" in block
    assert "힌트 1개 받고 답함" in block
    assert "직전 힌트: 힌트1" in block

def test_markdown_pointer_never_disagrees_with_curriculum_refs():
    """🔴 화면의 "교안: …"과 `curriculumRefs`가 서로 다른 말을 하면 안 된다.

    2026-08-04 실측: 모델이 teach_id를 라벨로 되돌려줘 필터에 걸렸는데, 마크다운은
    검증 안 된 `study_pointer`를 그대로 찍어 **참조는 비었는데 화면엔 교안이 떴다.**
    """
    teaches = [{"id": "t1", "label": "도구", "unit_id": "u6", "source_pages": [9]}]
    stage_rows = [{"axis_code": "L1", "status": "PASSED"}]

    # ① 모델이 모르는 teach를 지목하면 교안 줄이 아예 안 나간다.
    made_up = {"gaps": [{"axis": "도구", "detail": "부족",
                         "teach_id": "없는-teach", "study_pointer": "Unit u99 · p.404"}]}
    md = report._render_markdown(made_up, stage_rows, 1, teaches)
    assert "u99" not in md and "p.404" not in md
    assert report._curriculum_refs(made_up, teaches) == []

    # ② 라벨로 되돌려줘도 되살려 같은 teach를 가리킨다.
    echoed = {"gaps": [{"axis": "도구", "detail": "부족", "teach_id": "t1: 도구"}]}
    md = report._render_markdown(echoed, stage_rows, 1, teaches)
    assert "단원 u6" in md and "p.9" in md
    assert report._curriculum_refs(echoed, teaches)[0]["teachId"] == "t1"
