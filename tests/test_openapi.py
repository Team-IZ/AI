""" openapi.json이 코드와 어긋나지 않는지.

**백엔드가 이 파일 하나로 구현한다.** 스펙이 낡으면 백엔드는 옛 계약대로 짜고,
그 사실은 통합 시점에야 드러난다 — 그때는 이미 양쪽 다 고쳐야 한다.
지금까지 재생성이 사람 손이었고 실제로 세 번 낡았다(T5 1차·3차·오늘).
"""
import json
from pathlib import Path

from app.main import app

SPEC = Path(__file__).resolve().parent.parent / "openapi.json"


def _spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_spec_matches_the_code():
    """계약을 바꾸고 재생성을 안 하면 여기서 깨진다.

    고치는 법: `python tools/dump_openapi.py`
    """
    assert json.dumps(_spec(), sort_keys=True) == json.dumps(app.openapi(), sort_keys=True), (
        "openapi.json이 낡았습니다. `python tools/dump_openapi.py`를 돌리세요."
    )


def test_every_endpoint_is_published():
    """스펙에 없는 엔드포인트는 백엔드에게 존재하지 않는 것과 같다."""
    published = set(_spec()["paths"])

    assert "/api/health" in published
    for path in ("/api/v0/analyses", "/api/v0/sessions/{session_id}/answers",
                 "/api/v0/reports", "/api/v0/curricula", "/api/v0/analysis-inputs",
                 "/internal/v1/interview-brief:generate"):
        assert path in published, path

    # 세션은 무상태다(§T11 B) — 시작·조회·복원 3개가 사라져 8개다.
    assert "/api/v0/sessions" not in published
    assert "/api/v0/sessions/{session_id}/restore" not in published
    # analysis-inputs 분리(M2, 2026-08-06)와 면담 브리프(명세서 v08)가 하나씩 늘어 10개다.
    assert len(published) == 10


def test_frozen_contract_is_visible_in_the_spec():
    """2026-08-02 계약 변경 4건이 스펙 표면에 나와야 한다.

    OpenAPI로 표현 안 되는 규칙(축별 힌트 2개 강제 등)은 산문으로 따로 전달한다
    — tools/dump_openapi.py 주석에 목록이 있다.
    """
    schemas = _spec()["components"]["schemas"]

    def props(name):
        return schemas[name]["properties"]

    # ① 보고서는 문제 단위다
    assert "problemId" in props("ReportRequest")
    assert "problem" in props("ReportResult") and "problems" not in props("ReportResult")

    # ② 총점을 만들지 않는다 (자리를 만들면 누군가 채운다)
    assert "totalScore" not in props("ProblemResult")
    assert "maxScore" not in props("ProblemResult")

    # ③ 도달 단계가 판정값이다
    assert props("ProblemResult")["reachedStage"]["maximum"] == 4

    # ④ teach 앵커 없는 문제는 표기된다
    assert "isGeneral" not in props("Problem")   # 2026-08-03 삭제

    # 전면 동결 — 세션은 저장분을 받아 쓰기만 한다
    assert "problems" in props("AnswerSubmit")
    assert "hintText" in props("Question")

    # 무상태(§T11 B) — 커서가 요청·응답 양쪽에 있어야 왕복이 성립한다
    assert "cursor" in props("AnswerSubmit") and "transcript" in props("AnswerSubmit")
    assert "cursor" in props("AnswerResult") and "turn" in props("AnswerResult")


def test_2026_08_03_contract_changes_are_visible():
    """계약 변경 6건이 스펙 표면에 나와야 백엔드가 코드 없이 읽는다."""
    schemas = _spec()["components"]["schemas"]

    def props(name):
        return schemas[name]["properties"]

    # ① 채점도 모델을 고를 수 있다 (operator의 GradingPolicy)
    assert "providerModelCode" in props("AnswerSubmit")

    # ② 요청은 provider 식별자, 응답 원장은 화면 코드
    assert "providerModelCode" in props("ReportRequest")
    assert "modelCode" in props("AiUsage")

    # ③ extractorVersion은 INTEGER CHECK (> 0)
    assert props("Problem")["extractorVersion"]["type"] == "integer"
    assert props("Problem")["extractorVersion"]["exclusiveMinimum"] == 0

    # ④ 문제↔개념 연결
    assert "teachId" in props("Problem")

    # ⑤ 종료 사유는 AI가 말한다 — 백엔드가 커서로 역추론하지 않는다
    assert "terminationReason" in props("AnswerResult")
    assert "endedLevel" in props("AnswerResult")

    # ⑥ 채점기에 주는 맥락은 분석 문서 전체가 아니라 두 필드다
    assert "analysisContext" in props("AnswerSubmit")


def test_grading_failure_is_documented():
    """503을 문서화하지 않으면 백엔드가 재전송 규칙을 모른다."""
    answers = _spec()["paths"]["/api/v0/sessions/{session_id}/answers"]["post"]

    assert "503" in answers["responses"]
    assert "clientRequestId" in answers["responses"]["503"]["description"]


def test_multipart_request_fields_are_readable():
    """multipart 엔드포인트의 요청 필드가 스펙에 드러나야 한다.

    `/analyses`·`/curricula`는 JSON 문자열(payload) + 파일을 받아 자동 바인딩을
    안 쓴다 — 그래서 FastAPI가 요청 모델을 못 보고 **components에 안 실린다.**
    설명 문장만 두면 백엔드는 versionId·questionBudget 같은 필드를 알 수 없다
    (2026-08-02 발견). OpenAPI 3.1의 contentSchema로 구조를 싣는다.
    """
    spec = _spec()
    assert spec["openapi"].startswith("3.1")

    def payload_fields(path):
        part = (spec["paths"][path]["post"]["requestBody"]["content"]
                ["multipart/form-data"]["schema"]["properties"]["payload"])
        assert part["contentMediaType"] == "application/json"
        return set(part["contentSchema"]["properties"])

    analyses = payload_fields("/api/v0/analyses")
    assert {"method", "extractionScope", "questionBudget", "teaches",
            "requirements", "focusItems", "providerModelCode"} <= analyses

    curricula = payload_fields("/api/v0/curricula")
    assert {"versionId", "courseLabel", "providerModelCode"} <= curricula

    # 요청은 provider 식별자다(벤더 접두어 포함). 화면 선택값인 modelCode를 그대로
    # 넘기면 공급자가 모르는 이름이라 호출이 깨진다 — 이름이 되살아나면 여기서 잡는다.
    assert "modelCode" not in analyses and "modelCode" not in curricula

    # 202 + 폴링으로 확정(§T11 D-3) — 콜백 자리를 두면 누군가 구현한다
    assert "callbackUrl" not in analyses and "callbackUrl" not in curricula
