""" p04-6 보고서. 문제 하나가 끝날 때마다 하나씩 만든다.

**판정은 결정론이고 LLM은 서술만 쓴다.** 이 경계가 이 파일의 전부다.

    결정론 (여기 코드)   축별 점수 · 통과 여부 · 도달 단계 · 재시험 여부
    LLM (p04-6)          왜 그렇게 됐는지의 서술 · 교안 어디를 다시 볼지

**왜 판정을 안 맡기나**: 점수와 통과는 이미 세션에서 확정된 사실이다. 보고서를
쓰면서 모델에게 다시 물으면 같은 답변에 두 판정이 생기고, 어긋나면 어느 쪽이
맞는지 아무도 모른다. 화면에 뜨는 판정과 근거가 다른 말을 하는 상태가 최악이다.

**왜 서술은 맡기나**: 화면에 숫자가 없다(PM 설계 v2 §10-3). 매니저와 학생이 읽는
것은 축별 서술이고, 그건 답변 원문을 봐야 쓸 수 있다.

## 프롬프트가 세션 단위인 것에 대해

`p04-6`의 `user_template`은 "세션 전체 기록"을 전제로 쓰였다. 우리는 **문제 하나
분량만 넣어 부른다** — 프롬프트를 고치지 않고도 성립한다. 한 문제의 기록도
"기록 전체"의 유효한 부분집합이고, 모델이 보는 것은 문답 흐름이지 문제 개수가
아니기 때문이다.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import scoring, stages


@dataclass
class Report:
    """문제 하나 분량의 보고서."""

    problem: dict[str, Any]                 # ProblemResult 모양
    report_markdown: str
    # 마크다운을 만들기 전의 구조. **같은 내용이다** — 마크다운만 주면 프론트가
    # 헤딩을 다시 파싱해야 해서, 모델이 이미 낸 구조를 버리지 않고 같이 내보낸다.
    narrative: dict[str, Any]               # ReportNarrative 모양
    curriculum_refs: list[dict[str, Any]]
    retest: bool
    # 서술(p04-6)이 실패해 판정만 남았는지. 이 경우에도 job은 SUCCEEDED다 —
    # 확정된 점수·도달·재시험은 다 들어 있기 때문이다. 하지만 그러면 백엔드가
    # 마크다운 문구를 읽지 않고는 "서술이 비었다"를 알 수 없어 재시도 판단을
    # 못 한다(2026-08-03 실측: 보고서 3건 중 2건이 이 상태였다).
    narrative_failed: bool = False
    usages: list[dict[str, Any]] = field(default_factory=list)


def summarize_stages(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """문답 기록을 축별 최종 상태로 접는다.

    **같은 축의 마지막 턴이 그 축의 결과다.** 힌트 후 재질의도 한 턴이라 축 하나에
    턴이 최대 3개 나오는데, 기록에 남는 것은 마지막 시도의 점수다(힌트 상한이 이미
    거기에 걸려 있다).

    도달 못 한 축도 4개를 다 채워 보낸다 — DB `problem_stage`가 문제당 4행으로
    미리 만들어져 있어서, 빼고 보내면 Spring이 어느 행을 채울지 순서로 짐작하게 된다.
    """
    last: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for turn in transcript:
        axis = turn.get("axis_code")
        if axis not in scoring.AXES:
            continue
        last[axis] = turn
        counts[axis] = counts.get(axis, 0) + 1

    rows = []
    for axis in scoring.AXIS_CODES:
        turn = last.get(axis)
        if turn is None:
            rows.append({"axis_code": axis, "attempt_count": 0, "passed": False})
            continue
        confirmed = turn.get("confirmed_score")
        rows.append({
            "axis_code": axis,
            "attempt_count": counts[axis],
            "passed": (confirmed or 0) >= scoring.PASS_SCORE,
            "best_score": turn.get("best_score"),
            "confirmed_score": confirmed,
            "hints_used": max(counts[axis] - 1, 0),
            "autonomy": turn.get("autonomy"),
        })
    return rows


def reached_stage(stage_rows: list[dict[str, Any]]) -> int:
    """앞에서부터 연속으로 통과한 개수. 계단이라 건너뛴 통과는 없다."""
    reached = 0
    for row in stage_rows:
        if not row["passed"]:
            break
        reached += 1
    return reached


def _teaches_block(teaches: list[dict[str, Any]]) -> str:
    if not teaches:
        return "(연결된 teach 없음)"
    lines = []
    for t in teaches:
        pages = t.get("source_pages") or t.get("sourcePages") or []
        page_text = f"p.{', '.join(str(p) for p in pages)}" if pages else "페이지 미상"
        lines.append(f"- {t.get('id')}: {t.get('label', '')} "
                     f"(Unit {t.get('unit_id') or t.get('unitId') or '?'} · {page_text})")
    return "\n".join(lines)


def _transcript_block(transcript: list[dict[str, Any]]) -> str:
    """문답 기록. **힌트를 몇 개 받고 답했는지가 보여야** 모델이 자력을 서술한다."""
    blocks = []
    for i, turn in enumerate(transcript, start=1):
        hint = turn.get("hint_text")
        blocks.append(
            f"[{i}] {turn.get('axis_code')} · 점수 {turn.get('confirmed_score')}"
            f" (원점수 {turn.get('best_score')})\n"
            f"질문: {turn.get('question_text', '')}\n"
            f"{'직전 힌트: ' + hint if hint else '힌트 없이 답함'}\n"
            f"답변: {turn.get('answer_text', '')}"
        )
    return "\n\n".join(blocks) or "(기록 없음)"


def _analysis_block(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return "(분석 문서 없음)"
    return json.dumps(documents, ensure_ascii=False)


def _requirements_block(results: list[dict[str, Any]]) -> str:
    if not results:
        return "(요구사항 판정 없음)"
    return "\n".join(
        f"- {r.get('requirement_id')}: {r.get('verdict')}"
        f"{' — ' + r['note'] if r.get('note') else ''}"
        for r in results
    )


def _render_markdown(data: dict[str, Any], stage_rows: list[dict[str, Any]],
                     reached: int) -> str:
    """모델 JSON을 사람이 읽는 문서로.

    **숫자 점수를 쓰지 않는다**(PM 설계 v2 §10-3). 총점을 만들지 않기로 한 이상
    화면에 남은 숫자는 합칠 수 없는 숫자이고, 본 사람은 반드시 머릿속에서 평균을
    낸다 — 그 순간 비보상 원칙이 무너진다. 도달 단계와 서술만 쓴다.
    """
    parts = [f"## 도달 단계: {reached}단 / 4단", ""]

    if data.get("summary"):
        parts += [str(data["summary"]).strip(), ""]

    strengths = [s for s in (data.get("strengths") or []) if isinstance(s, dict)]
    if strengths:
        parts.append("### 잘한 것")
        parts += [f"- **{s.get('axis', '')}** {s.get('detail', '')}" for s in strengths]
        parts.append("")

    gaps = [g for g in (data.get("gaps") or []) if isinstance(g, dict)]
    if gaps:
        parts.append("### 더 볼 것")
        for g in gaps:
            line = f"- **{g.get('axis', '')}** {g.get('detail', '')}"
            if g.get("study_pointer"):
                line += f"\n  - 교안: {g['study_pointer']}"
            parts.append(line)
        parts.append("")

    if data.get("autonomy_note"):
        # 힌트를 받고 통과한 것은 "통과"가 아니라 "보조를 받아 도달"이다.
        parts += ["### 스스로 한 것과 도움을 받은 것", str(data["autonomy_note"]).strip(), ""]

    unreached = [r["axis_code"] for r in stage_rows if r["attempt_count"] == 0]
    if unreached:
        parts.append(f"> {', '.join(unreached)}는 앞 단계에서 끝나 묻지 않았습니다 — "
                     f"못한 것이 아니라 물어보지 않은 것입니다.")

    return "\n".join(parts).strip()


def _note(entry: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """모델이 낸 strengths/gaps 항목 하나를 응답 모양으로.

    `teach_id`는 **요청 teaches에 있는 것만 남긴다** — `_curriculum_refs()`와 같은
    필터다. 여기서 안 거르면 구조화 필드로는 지어낸 교안이 나가고 `curriculumRefs`
    에는 없는, 두 필드가 다른 말을 하는 상태가 된다.
    """
    teach_id = entry.get("teach_id")
    return {
        "axis": str(entry.get("axis") or ""),
        "detail": str(entry.get("detail") or ""),
        "teach_id": teach_id if teach_id in by_id else None,
        "study_pointer": entry.get("study_pointer"),
    }


def _narrative(data: dict[str, Any], teaches: list[dict[str, Any]],
               stage_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {t["id"]: t for t in teaches if t.get("id")}
    take = lambda key: [_note(e, by_id) for e in (data.get(key) or []) if isinstance(e, dict)]
    return {
        "summary": data.get("summary"),
        "strengths": take("strengths"),
        "gaps": take("gaps"),
        "autonomy_note": data.get("autonomy_note"),
        "unreached_axes": [r["axis_code"] for r in stage_rows if r["attempt_count"] == 0],
    }


def _curriculum_refs(data: dict[str, Any],
                     teaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """gaps가 지목한 teach만 참조로 올린다.

    **모델이 지어낸 teach id는 버린다.** 남기면 보고서가 없는 교안을 복습하라고
    가리키고, 학생은 찾을 수 없는 페이지를 뒤진다.
    """
    by_id = {t["id"]: t for t in teaches if t.get("id")}
    refs, seen = [], set()
    for gap in data.get("gaps") or []:
        if not isinstance(gap, dict):
            continue
        teach = by_id.get(gap.get("teach_id"))
        if teach is None or teach["id"] in seen:
            continue
        seen.add(teach["id"])
        refs.append({
            "teachId": teach["id"],
            "unitId": teach.get("unit_id") or teach.get("unitId"),
            "sourcePages": teach.get("source_pages") or teach.get("sourcePages") or [],
        })
    return refs


def build(problem_id: str, problem_no: int, transcript: list[dict[str, Any]], *,
          model_code: str, teaches: list[dict[str, Any]] | None = None,
          analysis_documents: list[dict[str, Any]] | None = None,
          requirement_results: list[dict[str, Any]] | None = None) -> Report:
    """문제 하나의 보고서를 만든다.

    **LLM이 실패해도 보고서는 나온다.** 판정(점수·도달·재시험)은 이미 결정론으로
    계산돼 있고 서술만 비는 것이라, 통째로 실패시키면 확정된 사실까지 잃는다.
    """
    teaches = teaches or []
    stage_rows = summarize_stages(transcript)
    reached = reached_stage(stage_rows)
    passed_by_axis = {r["axis_code"]: r["passed"] for r in stage_rows}

    usages: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    narrative_failed = False
    try:
        result = stages.call("p04-6", {
            "teaches_block": _teaches_block(teaches),
            "analysis_block": _analysis_block(analysis_documents or []),
            "requirements_block": _requirements_block(requirement_results or []),
            "transcript_block": _transcript_block(transcript),
        }, model_code=model_code,
           # 보고서는 202 + 폴링이라 **아무도 화면 앞에서 기다리지 않는다.** 기본 2회로는
           # 529 Overloaded 한 번만 겹쳐도 서술이 통째로 빈다(2026-08-03 실측: 3건 중
           # 3건). 시도 사이 백오프가 있으므로 늘리는 비용은 시간뿐이다.
           max_attempts=6)
        data = result.data
        usages = result.usages
    except stages.StageError as exc:
        usages = exc.usages
        narrative_failed = True
        data = {"summary": f"서술 생성에 실패했습니다({exc}). 아래 판정은 확정된 값입니다."}

    return Report(
        problem={
            "problem_no": problem_no,
            "problem_id": problem_id,
            "reached_stage": reached,
            "stages": stage_rows,
        },
        report_markdown=_render_markdown(data, stage_rows, reached),
        # 서술이 실패하면 data에 남는 것은 실패 안내 문장뿐이다. 그걸 summary로
        # 내보내면 백엔드가 "요약이 있다"로 읽는다 — narrativeFailed와 같이 비운다.
        narrative=({"summary": None, "strengths": [], "gaps": [], "autonomy_note": None,
                    "unreached_axes": [r["axis_code"] for r in stage_rows
                                       if r["attempt_count"] == 0]}
                   if narrative_failed else _narrative(data, teaches, stage_rows)),
        curriculum_refs=_curriculum_refs(data, teaches),
        # 재시험은 모델에게 묻지 않는다 — L1·L2 통과 여부로 결정된다(scoring).
        retest=scoring.is_retest_target(passed_by_axis),
        narrative_failed=narrative_failed,
        usages=usages,
    )
