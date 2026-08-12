""" stub 모드가 현재 스키마를 그대로 지키는지 (2026-08-12).

🔴 **스텁이 계약을 어기면 실제로 job이 FAILED가 된다** — `jobs.py`가 엔진 결과를
`AnalysisResult.model_validate`에 통과시키기 때문이다. 그런데 그 사실이 드러나는 곳은
백엔드가 처음 붙어보는 순간이라 늦다.

여기서 재는 것은 모델 품질이 아니라 **백엔드가 LLM 없이 계약 전부를 왕복할 수 있는가**다:
유형별 필수 필드가 다른 `references[]`, `―`(문항 없음)의 근거인 `unmatchedTeaches`,
teach 앵커 규칙, 그리고 세션·면담브리프가 stub 모드에서 LLM을 **안 부르는지**.
"""
import pytest
from fastapi.testclient import TestClient

from app import interview_brief as brief_service
from app import sessions as sessions_mod
from app.config import get_settings
from app.engines import interview_brief as brief_engine
from app.engines.stub import StubAnalysisEngine
from app.main import app
from app.schemas.analysis import AnalysisResult
from app.schemas.interview_brief import InterviewBriefRequest
from test_interview_brief import BRIEF_PATH, _request
from test_sessions import Backend

client = TestClient(app)
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}

TEACHES = [{"id": f"tch-{i}", "label": f"개념 {i}"} for i in (1, 2, 3)]


@pytest.fixture
def stub_mode(monkeypatch):
    """engine_mode=stub 고정. 기본값이지만 개인 `.env`가 real이면 조용히 다른 걸 잰다."""
    monkeypatch.setattr(get_settings(), "engine_mode", "stub")


def _team_result() -> AnalysisResult:
    """팀 모드 스텁 결과. **model_validate를 통과해야 한다** — 그게 계약이다."""
    raw = StubAnalysisEngine().analyze({
        "extraction_scope": "TOTAL",
        "question_budget": 3,
        "problem_scope": "TEAM_SHARED_PROBLEM",
        "teaches": TEACHES,
    })
    return AnalysisResult.model_validate(raw)


# ── ① references 유형별 필수 필드 + unmatchedTeaches ────────────────────────

def test_stub_references_satisfy_the_per_type_rules():
    """`ProblemReference._check_type_rules`가 강제하는 규칙을 스텁이 실제로 밟는지.

    옛 스텁은 `CALLER` 하나뿐이라 백엔드가 **코드 근거와 교안 근거가 한 테이블에
    섞여 있다**는 사실 자체를 못 봤다 — CURRICULUM_EVIDENCE는 path/line이 없고
    teachId로만 서고, QUESTION_HIGHLIGHT는 axisCode가 필수다.
    """
    problem = _team_result().problems[0]
    by_type = {}
    for ref in problem.references:
        by_type.setdefault(ref.reference_type, []).append(ref)

    assert len(by_type["PRIMARY_BLOCK"]) == 1
    assert [r.axis_code for r in by_type["QUESTION_HIGHLIGHT"]] == ["L1", "L2", "L3", "L4"]
    assert len(by_type["CURRICULUM_EVIDENCE"]) == 1
    assert by_type["CURRICULUM_EVIDENCE"][0].teach_id == "tch-1"
    # 교안 근거엔 코드 라인이 없다. 있으면 화면이 없는 위치를 그린다.
    assert by_type["CURRICULUM_EVIDENCE"][0].path is None
    assert 1 <= len(by_type["CALLER"]) <= 3

    # displayOrder는 1부터 빈틈없이(DB CHECK > 0, 화면 번호가 튀지 않게).
    assert [r.display_order for r in problem.references] == list(
        range(1, len(problem.references) + 1)
    )


def test_stub_reports_a_concept_it_could_not_anchor():
    """`―`(문항 없음) 분기를 백엔드가 실제로 밟아봐야 한다.

    🔴 0단(L1 미달)과 다른 값이다 — 도달 단계에 0을 박으면 "안 물어봤다"가
    "틀렸다"로 바뀐다. `problems` 길이 차이로 역산하지 않게 명시적으로 보낸다.
    """
    result = _team_result()

    assert [u.teach_id for u in result.unmatched_teaches] == ["tch-3"]
    assert all(u.reason for u in result.unmatched_teaches)
    # 예산은 요청 그대로고 문제 수만 줄어든다.
    assert result.question_count_planned == 3
    assert len(result.problems) == 2


def test_stub_hashes_the_file_and_the_fragment_separately():
    """contentHash(파일 전체)와 evidenceHash(파편)는 대상이 다르므로 값도 달라야 한다.

    같은 값으로 두면 백엔드가 하나만 저장해도 되는 줄 안다 — 파일 전체 기준으로
    근거 동일성을 판정하면 무관한 한 줄 수정에도 '근거가 바뀌었다'가 된다.
    """
    problem = _team_result().problems[0]

    assert problem.content_hash != problem.evidence_hash
    # lineStart~lineEnd는 **파일 기준 절대 줄 번호**다. codeSnippet은 파일 전체이므로
    # 그 안에 실제로 그 줄이 있어야 한다.
    assert len(problem.code_snippet.splitlines()) >= problem.line_end
    assert "def pay" in problem.code_snippet.splitlines()[problem.line_start - 1]


# ── ② 팀 모드 스텁의 모든 문제에 teachId ────────────────────────────────────

def test_team_mode_stub_anchors_every_problem_to_a_teach():
    """teach 앵커 없는 문제를 만들지 않는다(`isGeneral` 삭제, 2026-08-03 PM 결정).

    DB도 TEAM_SHARED_PROBLEM에 `project_verification_concept_id` NOT NULL을 요구해서
    teachId=null인 문제는 애초에 저장할 수 없다.
    """
    result = _team_result()

    assert result.problems                                  # 조용히 0개가 되면 안 된다
    assert all(p.teach_id for p in result.problems)
    assert [p.teach_id for p in result.problems] == ["tch-1", "tch-2"]


def test_individual_mode_stub_has_no_teach_anchor():
    """개인 모드는 teaches가 없다 — teachId=null이 정상인 유일한 분기다."""
    raw = StubAnalysisEngine().analyze({
        "extraction_scope": "OWN_COMMIT",
        "question_budget": 2,
        "problem_scope": "INDIVIDUAL_OWN_COMMIT",
    })
    result = AnalysisResult.model_validate(raw)

    assert result.problems
    assert all(p.teach_id is None for p in result.problems)
    assert result.unmatched_teaches == []


# ── ③ 세션 stub이 LLM 없이 끝까지 간다 ──────────────────────────────────────

@pytest.fixture
def no_llm(monkeypatch):
    """채점 LLM을 부르면 즉시 터진다. '안 부른다'가 이 테스트의 주장이다."""
    def _boom(*args, **kwargs):
        raise AssertionError("stub 모드인데 grading.grade()를 불렀다")

    monkeypatch.setattr(sessions_mod.grading, "grade", _boom)


def test_session_stub_completes_all_four_axes_without_an_llm(stub_mode, no_llm):
    """통과 답변 4번이면 L1~L4 완주. 진행 규칙은 실경로와 같은 코드를 쓴다."""
    session = Backend(session_id="stub-pass-1")

    for _ in range(3):
        body = session.answer("이건 통과하는 답변입니다")
        assert body["turn"]["passed"] is True
        assert body["terminationReason"] is None
    body = session.answer("이건 통과하는 답변입니다")

    assert body["turn"]["axisCode"] == "L4"
    assert body["terminationReason"] == "COMPLETED_L4"
    assert body["endedLevel"] == "L4"
    # 원장 1행. 빈 배열이면 백엔드가 ai_usage 저장 경로를 한 번도 안 밟는다.
    assert len(body["aiUsage"]) == 1
    assert body["aiUsage"][0]["featureCode"] == "ANSWER_EVALUATION"
    assert body["aiUsage"][0]["status"] == "SUCCEEDED"


def test_session_stub_terminates_after_two_hints(stub_mode, no_llm):
    """짧은 답변은 미달이다 — 힌트 2개를 다 쓰고도 미달이면 그 문제는 끝난다."""
    session = Backend(session_id="stub-fail-1")

    first = session.answer("짧다")
    assert first["turn"]["passed"] is False
    assert first["current"]["hintText"] == "L1 힌트 1"      # 힌트가 하나 열렸다

    second = session.answer("짧다")
    assert second["current"]["hintText"] == "L1 힌트 2"

    third = session.answer("짧다")
    assert third["terminationReason"] == "TERMINATED_AT_L1"
    assert third["endedLevel"] == "L1"
    # 다음 문제의 L1로 넘어간다. 남은 축은 미도달로 남는다.
    assert third["cursor"]["problemId"] == "prob-2"
    assert third["cursor"]["axisCode"] == "L1"


def test_session_stub_score_is_deterministic(stub_mode, no_llm):
    """같은 답변이면 항상 같은 점수다 — 랜덤이면 백엔드가 멱등을 시험할 수 없다."""
    a = Backend(session_id="stub-det-1").answer("길이가 충분한 보통 답변입니다")
    b = Backend(session_id="stub-det-2").answer("길이가 충분한 보통 답변입니다")

    assert a["turn"]["score"] == b["turn"]["score"]


# ── ④ 브리프 stub은 요청에 있던 interviewSourceId만 쓴다 ────────────────────

def test_brief_stub_only_uses_source_ids_from_the_request(stub_mode):
    """🔴 새 UUID를 지어내면 백엔드 INSERT가 깨진다.

    요청에 없는 값이면 저장할 대상이 없다(§5.1). 실엔진이 모델 출력을 검증하는
    이유와 같다. null은 별개다 -- 라포·일반 질문은 설계상 근거가 없어 null로 나가고,
    백엔드가 `source_type='MANUAL'`로 저장한다(테이블정의서 2026-08-06의 CHECK).
    """
    payload = _request()
    allowed = brief_engine._collect_allowed_source_ids(
        InterviewBriefRequest.model_validate(payload)
    )

    r = client.post(BRIEF_PATH, json=payload,
                    headers={**HEADERS, "Idempotency-Key": "stub-brief:ids"})

    assert r.status_code == 200
    body = r.json()
    ids = [i["interviewSourceId"] for i in body["items"]]
    assert any(i is not None for i in ids)                 # 전부 null이면 근거 배선이 죽은 것
    assert all(i is None or i in allowed for i in ids)     # 값을 댔으면 요청에 있던 값이어야
    # 1부터 중복 없는 연속 정수(백엔드가 display_order를 여기서 파생한다).
    assert [i["suggestedOrder"] for i in body["items"]] == list(range(1, len(body["items"]) + 1))
    assert 3 <= len(body["items"]) <= 8
    assert len(body["aiUsage"]) == 1
    assert body["aiUsage"][0]["featureCode"] == "INTERVIEW_BRIEF_GENERATION"
    assert body["aiUsage"][0]["contextType"] == "INTERVIEW_BRIEF"
    # contextId는 요청의 briefId를 그대로 에코한다.
    assert body["aiUsage"][0]["contextId"] == payload["briefId"]


def test_brief_stub_follows_the_same_composition_as_the_real_engine(stub_mode):
    """스텁 개수·순서는 `engine._compose()` 결과와 정확히 같아야 한다.

    2026-08-12에 규칙이 "4~8개(첫 면담 6~8)"에서 5-카테고리 고정 구성으로 바뀌었는데,
    스텁이 옛 규칙을 복제해 갖고 있어서 갈렸다 -- 규칙을 두 곳에 두지 않는다.
    첫 면담(priorInterviews 없음)이라 PRIOR_INTERVIEW 카테고리가 빠지는 것도 여기서 본다.
    """
    payload = _request()
    payload["briefContext"]["isFirstInterview"] = True
    req = InterviewBriefRequest.model_validate(payload)

    result = brief_service._stub_result(req)
    composition, _ = brief_engine._compose(req)

    assert [i.question_type for i in result.items] == composition.sequence()
    assert "PRIOR_INTERVIEW" not in composition.sequence()
    assert 3 <= len(result.items) <= 8


def test_brief_stub_attaches_the_right_kind_of_source_id_per_category(stub_mode):
    """스텁 id는 허용 집합에서 아무거나가 아니라 **카테고리에 맞는 종류**여야 한다.

    허용 집합에서 위치로 골라 쓰면 검증은 통과하지만(전부 요청에 있는 값이니) 백엔드가
    "위험 질문인데 근거가 문제 단위네?"를 보게 된다 -- 스텁의 존재 이유가 진짜 모양을
    보여주는 거라 그러면 안 된다. 라포·일반은 근거가 없어 null(=source_type MANUAL)이다.
    """
    payload = _request()
    payload["observationNotes"] = [{
        "occurredAt": "2026-08-01T09:00:00Z", "content": "메모",
        "interviewSourceId": "src-note-1", "visibility": "MANAGER_ONLY",
    }]
    req = InterviewBriefRequest.model_validate(payload)

    result = brief_service._stub_result(req)
    by_type = {i.question_type: i.interview_source_id for i in result.items}

    assert by_type["RAPPORT"] == "src-note-1"                       # 관찰 메모 근거
    assert by_type["RISK"] == payload["riskReasons"][0]["sourceInterviewSourceId"]
    assert by_type["QNA"] == "src-stage-1"                          # 막힌 단계 근거
    assert by_type["GENERAL"] is None                               # 근거 없음 -> MANUAL
