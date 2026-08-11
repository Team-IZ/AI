""" p04-6 보고서. 문제 하나당 하나씩 만든다.

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


SLOTS = ("question", "first_hint", "second_hint")

# "아직 진행 중"을 뜻하는 problem_stage 상태. 점수가 이미 있으면 이 값과 모순이다.
PROVISIONAL = ("PREPARED", "IN_PROGRESS")


def summarize_stages(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """요청의 축별 행을 정규화한다. **DB `problem_stage` 한 행과 1:1이다.**

    🔴 2026-08-10 입력 모양 변경(백엔드 합의). 예전엔 **턴 목록**(축당 최대 3개,
    `hints_used`로 슬롯을 고름)을 받아 여기서 접었다. 이제 백엔드가 `problem_stage`에서
    읽은 **이미 접힌 행**을 그대로 보낸다 — 축당 1개, 슬롯 3개가 평평하게 실려 온다.
      WHY: AI가 내보내는 `StageScore`도 같은 모양이라 입출력이 대칭이 된다. 양쪽에서
      변환이 사라지고, 변환이 없으면 2026-08-02식 조용한 소실(턴을 넘겼는데 "기록
      없음"으로 계산)이 성립할 자리가 없다.
      COST: 옛 턴 모양은 더는 안 받는다. 보내면 슬롯 필드가 없어 NOT_REACHED가 된다.

    도달 못 한 축도 4개를 다 채워 보낸다 — `problem_stage`가 문제당 4행으로 미리
    만들어져 있어서, 빼고 보내면 Spring이 어느 행을 채울지 순서로 짐작하게 된다.
    """
    by_axis: dict[str, dict[str, Any]] = {
        axis: {"axis_code": axis, "status": "NOT_REACHED"} for axis in scoring.AXIS_CODES
    }

    for given in transcript:
        row = by_axis.get(given.get("axis_code"))
        if row is None:
            continue
        for slot in SLOTS:
            for suffix in ("text", "answer_text", "score", "passed"):
                value = given.get(f"{slot}_{suffix}")
                if value is not None:
                    row[f"{slot}_{suffix}"] = value
        # 🔴 **백엔드 status를 믿고, 없으면 계산으로 폴백한다**(2026-08-10 확정).
        # `NOT_ANSWERED`(물었는데 답이 없다)는 AI가 만들 수 없다 — 점수 기록만 보면
        # "안 물어봤다"와 구분이 안 된다. 세션 진행 사실을 아는 쪽은 백엔드다.
        if given.get("status"):
            row["status"] = given["status"]

    for row in by_axis.values():
        answered = any(row.get(f"{p}_score") is not None for p in SLOTS)
        status = row.get("status")
        # 백엔드가 준 값을 덮지 않는다. 단 **모순일 때만** 예외다 --
        # PREPARED/IN_PROGRESS는 "아직 진행 중"인데 점수가 있으면 이미 채점된 것이다.
        # 세션 도메인이 종료 시 stage 정리를 못 한 채로 리포트가 요청되면 그 값이
        # 그대로 보고서에 찍힌다(백엔드도 dispatch 게이트로 막기로 했지만, Swagger
        # 수동 호출·재생성 등 다른 경로가 있어 여기서도 막는다).
        # 점수가 없는 PREPARED·NOT_ANSWERED는 모순이 아니라 그대로 둔다.
        if status not in (None, "NOT_REACHED") and not (status in PROVISIONAL and answered):
            continue
        passed = any(row.get(f"{p}_passed") for p in SLOTS)
        row["status"] = "PASSED" if passed else ("NOT_PASSED" if answered else "NOT_REACHED")

    return [by_axis[axis] for axis in scoring.AXIS_CODES]


def stage_passed(row: dict[str, Any]) -> bool:
    """세 슬롯 중 하나라도 통과면 그 축은 통과다."""
    return bool(row.get("question_passed") or row.get("first_hint_passed")
                or row.get("second_hint_passed"))


def reached_stage(stage_rows: list[dict[str, Any]]) -> int:
    """앞에서부터 연속으로 통과한 개수. 계단이라 건너뛴 통과는 없다."""
    reached = 0
    for row in stage_rows:
        if not stage_passed(row):
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


def _transcript_block(stage_rows: list[dict[str, Any]]) -> str:
    """문답 기록. **힌트를 몇 개 받고 답했는지가 보여야** 모델이 자력을 서술한다.

    입력은 축별 접힌 행이지만 프롬프트에는 **시도 단위로 펼쳐** 넣는다. 접힌 채로
    주면 모델이 "질문에 바로 답했다"와 "힌트 둘 받고 답했다"를 같은 줄에서 읽어야 해서
    자력 서술이 뭉개진다. 답이 없는 슬롯은 건너뛴다 — 안 물어본 자리다.
    """
    labels = {"question": "힌트 없이 답함", "first_hint": "힌트 1개 받고 답함",
              "second_hint": "힌트 2개 받고 답함"}
    blocks = []
    for row in stage_rows:
        for slot in SLOTS:
            answer = row.get(f"{slot}_answer_text")
            score = row.get(f"{slot}_score")
            if answer is None and score is None:
                continue
            hint = row.get(f"{slot}_text") if slot != "question" else None
            blocks.append(
                f"[{len(blocks) + 1}] {row.get('axis_code')} · 점수 {score}"
                f" ({labels[slot]})\n"
                f"질문: {row.get('question_text', '')}\n"
                f"{'직전 힌트: ' + hint if hint else '힌트 없이 답함'}\n"
                f"답변: {answer or ''}"
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
                     reached: int, teaches: list[dict[str, Any]] | None = None) -> str:
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
        by_id = {t["id"]: t for t in (teaches or []) if t.get("id")}
        for g in gaps:
            line = f"- **{g.get('axis', '')}** {g.get('detail', '')}"
            # 🔴 **복습 위치는 요청 teaches에서 만든다 — 모델의 study_pointer를 쓰지 않는다.**
            # 그 값은 검증되지 않은 자유 텍스트라 없는 페이지를 가리킬 수 있고, teach_id가
            # 걸러진 경우 `curriculumRefs`는 비었는데 화면에는 교안이 뜬다 — 두 필드가
            # 서로 다른 말을 한다(2026-08-04 실측: 문제 2가 그 상태로 나갔다).
            teach = by_id.get(stages.resolve_choice(g.get("teach_id"), set(by_id)))
            if teach:
                pages = ", ".join(str(x) for x in (teach.get("source_pages")
                                                   or teach.get("sourcePages") or []))
                unit = teach.get("unit_id") or teach.get("unitId") or "-"
                line += f"\n  - 교안: 단원 {unit}" + (f" · p.{pages}" if pages else "")
            parts.append(line)
        parts.append("")

    if data.get("autonomy_note"):
        # 힌트를 받고 통과한 것은 "통과"가 아니라 "보조를 받아 도달"이다.
        parts += ["### 스스로 한 것과 도움을 받은 것", str(data["autonomy_note"]).strip(), ""]

    unreached = [r["axis_code"] for r in stage_rows if r["status"] == "NOT_REACHED"]
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
    teach_id = stages.resolve_choice(entry.get("teach_id"), set(by_id))
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
        "unreached_axes": [r["axis_code"] for r in stage_rows if r["status"] == "NOT_REACHED"],
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
        teach = by_id.get(stages.resolve_choice(gap.get("teach_id"), set(by_id)))
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
    passed_by_axis = {r["axis_code"]: stage_passed(r) for r in stage_rows}

    usages: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    narrative_failed = False
    try:
        result = stages.call("p04-6", {
            "teaches_block": _teaches_block(teaches),
            "analysis_block": _analysis_block(analysis_documents or []),
            "requirements_block": _requirements_block(requirement_results or []),
            "transcript_block": _transcript_block(stage_rows),
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
        report_markdown=_render_markdown(data, stage_rows, reached, teaches),
        # 서술이 실패하면 data에 남는 것은 실패 안내 문장뿐이다. 그걸 summary로
        # 내보내면 백엔드가 "요약이 있다"로 읽는다 — narrativeFailed와 같이 비운다.
        narrative=({"summary": None, "strengths": [], "gaps": [], "autonomy_note": None,
                    "unreached_axes": [r["axis_code"] for r in stage_rows
                                       if r["status"] == "NOT_REACHED"]}
                   if narrative_failed else _narrative(data, teaches, stage_rows)),
        curriculum_refs=_curriculum_refs(data, teaches),
        # 재시험은 모델에게 묻지 않는다 — L1·L2 통과 여부로 결정된다(scoring).
        retest=scoring.is_retest_target(passed_by_axis),
        narrative_failed=narrative_failed,
        usages=usages,
    )
