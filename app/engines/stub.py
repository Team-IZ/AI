""" 테스트용 가짜 엔진(스텁) """

from typing import Any

from app.schemas.analysis import FROZEN_AXES


def _stub_stages() -> list[dict[str, Any]]:
    """단계 4개. 전면 동결이라 4축 전부 질문 1개 + 힌트 2개를 채운다."""
    return [
        {
            "axis_code": axis,
            "question_text": f"[stub] {axis} 질문",
            "flagged": False,
            "hints": [
                {"hint_level": 1, "hint_text": f"[stub] {axis} 힌트 1"},
                {"hint_level": 2, "hint_text": f"[stub] {axis} 힌트 2"},
            ],
        }
        # 진행 순서 = AxisCode 순서. analysis.py의 Problem validator가 검사한다.
        for axis in FROZEN_AXES
    ]


def _stub_problem(problem_no: int, focus_item_id: str | None = None,
                  teach_id: str | None = None) -> dict[str, Any]:
    """문제 하나. DB assessment_problem 컬럼명을 그대로 쓴다."""
    return {
        "problem_id": f"00000000-0000-0000-0000-{problem_no:012d}",
        "problem_no": problem_no,
        "status": "READY",
        "problem_type": "RISK_POINT",
        "priority": 1.0,
        # 요청 focusItems[].id를 그대로 돌려준다. 후보가 없으면 자율 선정(None).
        "question_focus_item_id": focus_item_id,
        # 요청 teaches[].id를 순서대로 물린다. 후보가 없으면 일반 문제(None).
        "teach_id": teach_id,
        # teach 앵커가 없으면 일반 문제다. 둘은 항상 짝이다.
        "is_general": teach_id is None,
        "source_path": "app/main.py",
        "line_start": 12,
        "line_end": 14,
        # evidence_hash는 이 문자열 기준 해시다. Spring이 다시 자르면 어긋난다.
        "code_snippet": f"# stub problem {problem_no}\napi_key = 'hardcoded'\n",
        "evidence_hash": str(problem_no) * 64,
        # DB가 INTEGER CHECK (> 0)이라 정수다. 스텁은 1로 고정한다.
        "extractor_version": 1,
        "references": [
            {
                "path": "app/services/auth.py",
                "line_start": 40,
                "line_end": 44,
                "evidence_hash": "a" * 64,
                "reference_type": "CALLER",
            }
        ],
        "stages": _stub_stages(),
    }


class StubAnalysisEngine:

    def analyze(
        self, request: dict[str, Any], zip_bytes: bytes | None = None
    ) -> dict[str, Any]:
        # 요청 값을 결과에 반영해 연결되었는지 확인
        # 스텁이 요청 무시하고 고정값만 주면 실제값 확인 불가
        applied_scope = request["extraction_scope"]
        byte_count = len(zip_bytes) if zip_bytes is not None else 1024

        # 강사 지정 후보를 문제에 순서대로 물린다. 후보가 적으면 나머지는 자율 선정.
        focus_ids = [item["id"] for item in request.get("focus_items", [])]
        # teach 앵커도 같은 규칙이다. 부족분은 teach 없는 일반 문제가 된다.
        teach_ids = [t.get("id") for t in request.get("teaches", [])]

        return {
            "snapshot_id": "00000000-0000-0000-0000-000000000001",
            "snapshot_meta": {
                "content_hash": "0" * 64,  # sha256 hex 64자 자리
                "file_count": 3,
                "byte_count": byte_count,  # ZIP 실제 바이트 반영
            },
            "applied_scope": applied_scope,  # 요청 범위 그대로 에코
            "scope_fallback": False,
            "fallback_reason": None,
            "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            "analysis_document": {
                "overview": "[stub] 실제 문서는 엔진 이식 후 생성됩니다.",
                "structure": [
                    {"area": "진입점", "files": ["app/main.py"], "role": "요청을 받아 서비스로 넘긴다"},
                ],
                # 두 번째 항목은 근거 검증에 실패한 경우다. 백엔드가 이 모양을
                # 실제로 받아보고 렌더 분기를 짜도록 스텁에 일부러 섞어 둔다.
                "decision_points": [
                    {
                        "title": "결제 수단 분기를 함수 안에서 처리",
                        "source_path": "app/main.py",
                        "symbol": "def pay(order, method):",
                        "line_start": 12,
                        "line_end": 20,
                        "why_it_matters": "대안이 있었는데 이것을 택한 지점이라 설계 의도를 물을 수 있다",
                        "related_teach_id": None,
                        "evidence_valid": True,
                    },
                    {
                        "title": "[stub] 근거를 찾지 못한 지점",
                        "source_path": "app/unknown.py",
                        "symbol": "def vanished():",
                        "why_it_matters": "모델이 지목했으나 실제 소스에 없어 근거로 쓰지 않는다",
                        "related_teach_id": None,
                        "evidence_valid": False,
                    },
                ],
                "risks": ["[stub] 실제 위험 요소는 엔진 이식 후 채워집니다"],
            },
            # 요청 requirements와 1:1. 스텁은 전부 P로 판정한다.
            "requirement_results": [
                {
                    "requirement_id": req["requirementId"],
                    "verdict": "P",
                    "evidence": "app/main.py:12",
                    "note": None,
                }
                for req in request.get("requirements", [])
            ],
            "problems": [
                _stub_problem(no,
                              focus_ids[i] if i < len(focus_ids) else None,
                              teach_ids[i] if i < len(teach_ids) else None)
                for i, no in enumerate((1, 2, 3))
            ],
            "question_count_planned": request["question_budget"],  # 요청 예산 반영
        }