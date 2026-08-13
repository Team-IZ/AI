""" 테스트용 가짜 엔진(스텁)

🔴 **스텁은 1급 시민이다.** 백엔드는 이 응답만 보고 파서를 짠다 — 무료 티어 529(실패율
64%)도, 5분짜리 분석 대기도 없이 계약 전체를 왕복해 볼 수 있어야 한다. 그래서 여기서
빼먹은 필드는 백엔드가 "그런 필드는 안 온다"로 배우고, 실엔진이 붙는 날 조용히 어긋난다.
2026-08-12에 `AnalysisResult`와 전수 대조해 맞췄다.

**값은 전부 결정적이다**(고정 문자열 + sha256). 같은 요청이면 같은 응답이라 백엔드가
멱등 재전송을 시험할 수 있다.
"""

import hashlib
from typing import Any

from app.config import get_settings
from app.schemas.analysis import FROZEN_AXES
from app.usage import stub_usage


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 🔴 `code_snippet`은 **문제를 낸 파일 전체다**(2026-08-03 확정). 파편만 주면 학생이
# 판단할 재료가 없다. 그래서 스텁도 여러 줄짜리 가짜 파일을 만들고, lineStart~lineEnd는
# **그 파일 기준 절대 줄 번호**로 강조 구간을 가리킨다(예전 스텁은 2줄짜리 파편에
# lineStart=12라 그 자체로 모순이었다 -- 백엔드가 "파편 안의 상대 번호"로 오해할 수 있다).
_HIGHLIGHT_START = 15
_HIGHLIGHT_END = 21


def _stub_file(problem_no: int) -> str:
    """문제 하나가 딸린 가짜 파일 전체. 15~21행이 `pay()` 정의다."""
    return "\n".join([
        f'"""결제 처리 (stub 샘플 파일 {problem_no})."""',
        "",
        "import os",
        "",
        "from app.services import ledger",
        "",
        "",
        'API_KEY = os.environ.get("PAY_API_KEY", "")',
        "",
        "",
        "def _fee(amount: int) -> int:",
        "    return amount // 100",
        "",
        "",
        "def pay(order, method):",
        '    """결제 수단에 따라 분기한다."""',
        '    if method == "card":',
        "        return ledger.charge(order, _fee(order.amount))",
        '    if method == "point":',
        "        return ledger.deduct(order, 0)",
        '    raise ValueError(f"unknown method: {method}")',
        "",
    ])


def _fragment(file_text: str) -> str:
    """강조 구간만. `evidence_hash`의 대상이다."""
    return "\n".join(file_text.splitlines()[_HIGHLIGHT_START - 1:_HIGHLIGHT_END])


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


def _stub_references(source_path: str, evidence_hash: str,
                     teach_id: str | None) -> list[dict[str, Any]]:
    """실엔진과 같은 구성으로 만든다(2026-08-04 이후).

        PRIMARY_BLOCK 1 + QUESTION_HIGHLIGHT 4(축별) + CURRICULUM_EVIDENCE 0~1 + CALLER 0~3

    예전 스텁은 `CALLER` 하나뿐이라 백엔드가 **유형별 필수 필드가 다르다**는 사실을
    못 봤다 — `CURRICULUM_EVIDENCE`는 path/line이 아예 없고 teachId로만 서고,
    `QUESTION_HIGHLIGHT`는 axisCode가 필수다(`ProblemReference._check_type_rules`).
    `displayOrder`는 1부터 빈틈없이 붙는다(DB CHECK > 0).
    """
    refs: list[dict[str, Any]] = [{
        "reference_type": "PRIMARY_BLOCK",
        "display_order": 1,
        "path": source_path,
        "line_start": _HIGHLIGHT_START,
        "line_end": _HIGHLIGHT_END,
        "evidence_hash": evidence_hash,
    }]
    refs += [{
        "reference_type": "QUESTION_HIGHLIGHT",
        "display_order": len(refs) + i + 1,
        "path": source_path,
        "line_start": _HIGHLIGHT_START,
        "line_end": _HIGHLIGHT_END,
        "axis_code": axis,
        "evidence_hash": evidence_hash,
    } for i, axis in enumerate(FROZEN_AXES)]

    if teach_id:
        # 교안 근거. **코드 라인이 없다** — 개념이 교안 어디서 왔는지만 가리킨다.
        refs.append({
            "reference_type": "CURRICULUM_EVIDENCE",
            "display_order": len(refs) + 1,
            "teach_id": teach_id,
            "evidence_hash": _sha(f"teach:{teach_id}"),
        })

    refs.append({
        "reference_type": "CALLER",
        "display_order": len(refs) + 1,
        "path": "app/api/orders.py",
        "line_start": 30,
        "line_end": 34,
        "evidence_hash": _sha(f"caller:{source_path}"),
    })
    return refs


def _stub_problem(problem_no: int, focus_item_id: str | None = None,
                  teach_id: str | None = None) -> dict[str, Any]:
    """문제 하나. DB assessment_problem 컬럼명을 그대로 쓴다."""
    source_path = f"app/services/payment_{problem_no}.py"
    file_text = _stub_file(problem_no)
    # 🔴 대상이 다르므로 값도 달라야 한다. contentHash는 **파일 전체**(무결성),
    # evidenceHash는 **파편**(출제 근거가 그대로인지)이다. 예전 스텁은 둘 다
    # `str(problem_no) * 64`라 백엔드가 "같은 값"으로 읽을 수 있었다.
    content_hash = _sha(file_text)
    evidence_hash = _sha(_fragment(file_text))
    return {
        "problem_id": f"00000000-0000-0000-0000-{problem_no:012d}",
        "problem_no": problem_no,
        "status": "READY",
        # GENERATED 문제에 NOT NULL인 3개(테이블정의서 v06).
        "title": f"stub 문제 {problem_no}",
        "snippet_key": f"p{problem_no}-{evidence_hash[:16]}",
        "code_language": "PYTHON",
        "problem_type": "RISK_POINT",
        "priority": 1.0,
        # 요청 focusItems[].id를 그대로 돌려준다. 후보가 없으면 자율 선정(None).
        "question_focus_item_id": focus_item_id,
        # 요청 teaches[].id를 순서대로 물린다. 팀 모드면 **항상 채워진다**.
        "teach_id": teach_id,
        "source_path": source_path,
        "line_start": _HIGHLIGHT_START,
        "line_end": _HIGHLIGHT_END,
        "code_snippet": file_text,
        "content_hash": content_hash,
        "evidence_hash": evidence_hash,
        # DB가 INTEGER CHECK (> 0)이라 정수다. 스텁은 1로 고정한다.
        "extractor_version": 1,
        "references": _stub_references(source_path, evidence_hash, teach_id),
        "stages": _stub_stages(),
    }


# 커밋 이력 2건. 프론트 "최근 커밋 이력" 화면이 commitHash·commitMessage·branchName·
# committedAt 4개로 그리므로 **커밋마다 넷 다 채운다**. 뒤쪽은 root 커밋이라
# `parent_sha`가 null이다 — 백엔드가 NOT NULL을 해제한 자리라(2026-08-07) 실제로
# null이 오는 모양을 한 번은 받아봐야 한다.
_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
_ROOT_SHA = "89abcdef0123456789abcdef0123456789abcdef"


def _stub_git_history(branch: str) -> list[dict[str, Any]]:
    return [
        {
            "commit_hash": _HEAD_SHA,
            "commit_message": "feat: add payment method branch",
            "author_name": "hong",
            "author_email": "hong@example.com",
            "committed_at": "2026-08-11T14:22:11Z",
            "changed_files": ["app/services/payment_1.py", "app/api/orders.py"],
            "additions": 74,
            "deletions": 12,
            "parent_sha": _ROOT_SHA,
            "authored_at": "2026-08-11T14:20:03Z",
            "branch_name": branch,
            "is_merge_commit": False,
            "is_revert_commit": False,
            "is_bot_commit": False,
            "changed_line_count": 86,
        },
        {
            "commit_hash": _ROOT_SHA,
            "commit_message": "chore: init repository",
            "author_name": "hong",
            "author_email": "hong@example.com",
            "committed_at": "2026-08-10T09:11:40Z",
            "changed_files": ["app/services/payment_1.py"],
            "additions": 58,
            "deletions": 0,
            # 부모 없는 root 커밋. 옛 "0"*40 sentinel은 폐기됐다.
            "parent_sha": None,
            "authored_at": "2026-08-10T09:11:40Z",
            "branch_name": branch,
            "is_merge_commit": False,
            "is_revert_commit": False,
            "is_bot_commit": False,
            "changed_line_count": 58,
        },
    ]


def _stub_ai_usage(model_code: str, problem_count: int) -> list[dict[str, Any]]:
    """분석 job의 원장. `jobs.py`가 결과에서 떼어 `to_ai_usage`로 넘긴다
    (contextType=ANALYSIS_JOB · contextId=jobId · 멱등키 조립까지 거기서 한다).

    real 모드는 여기에 20행 넘게 실린다(실측 23행 = `CODE_ANALYSIS` 3 + `CODE_SESSION` 20).
    스텁이 빈 배열이면 백엔드는 **원장 저장 경로도, 집계 분기도** 한 번을 못 밟는다.

    두 featureCode를 다 내고 **실패 행을 하나 섞는다** — 무료 티어 529 실패율이 64%라
    real에서 실제로 흔한 모양이고, `status`/`failureCode` 짝 CHECK를 백엔드가 받아봐야 한다.
    ponytail: 행 수는 real을 흉내내지 않는다(문제 수 비례). 늘릴 이유가 생기면 그때.
    """
    rows = [
        stub_usage(model_code, feature_code="CODE_ANALYSIS", input_tokens=6400, output_tokens=1800,
                   latency_ms=41),
        # 태운 토큰은 있는데 결과가 없는 행. 재시도로 넘어간 실패다.
        stub_usage(model_code, feature_code="CODE_ANALYSIS", status="FAILED",
                   failure_code="PROVIDER_ERROR", input_tokens=0, output_tokens=0, latency_ms=3),
        stub_usage(model_code, feature_code="CODE_ANALYSIS", input_tokens=5200, output_tokens=900,
                   latency_ms=27),
    ]
    # 질문·힌트 동결(p04-3·p04-4·p04-7)은 문제마다 돈다.
    rows += [
        stub_usage(model_code, feature_code="CODE_SESSION", input_tokens=2100, output_tokens=640,
                   latency_ms=19)
        for _ in range(max(1, problem_count) * 2)
    ]
    return rows


class StubAnalysisEngine:

    def analyze(
        self, request: dict[str, Any], zip_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        # 요청 값을 결과에 반영해 연결되었는지 확인
        # 스텁이 요청 무시하고 고정값만 주면 실제값 확인 불가
        applied_scope = request["extraction_scope"]
        byte_count = len(zip_bytes) if zip_bytes is not None else 1024
        branch = (request.get("source") or {}).get("branch") or "main"

        # 강사 지정 후보를 문제에 순서대로 물린다. 후보가 적으면 나머지는 자율 선정.
        focus_ids = [item["id"] for item in request.get("focus_items", [])]
        teach_ids = [t.get("id") for t in request.get("teaches", []) if t.get("id")]
        budget = request["question_budget"]

        # 🔴 **teach 앵커 없는 문제를 만들지 않는다**(2026-08-03 PM 결정, `isGeneral` 삭제).
        # 그래서 문제 개수를 정하는 규칙이 모드마다 다르다 — 요청의 `problemScope`를 본다
        # (`teaches`가 비었는지로 추론하면 팀 모드에서 교안 분석이 실패한 경우가 개인
        # 모드로 조용히 바뀐다). 직접 dict를 넘기는 호출(테스트)만 teaches 유무로 떨어진다.
        scope = request.get("problem_scope") or (
            "TEAM_SHARED_PROBLEM" if teach_ids else "INDIVIDUAL_OWN_COMMIT"
        )
        if scope == "TEAM_SHARED_PROBLEM":
            wanted = min(budget, len(teach_ids))
            # 개념 하나는 **일부러** 못 찾은 것으로 둔다. 백엔드 화면의 `―`(문항 없음)
            # 분기를 실제로 밟아봐야 하기 때문이다 — 그게 0단(L1 미달)과 다른 값이다.
            # 단 그 때문에 문제가 0개가 되면 안 되므로 2개 이상일 때만 뺀다.
            unmatched_ids = teach_ids[wanted - 1:wanted] if wanted >= 2 else []
            anchors: list[str | None] = list(teach_ids[:wanted - len(unmatched_ids)])
        else:
            # 개인 모드엔 teaches가 없다. teachId=None이 정상인 유일한 분기다.
            unmatched_ids = []
            anchors = [None] * max(1, min(budget, 3))

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
            "commit_sha": _HEAD_SHA,
            "analysis_document": {
                # DB `code_analysis.analysis_document` 제약이 이 키를 요구한다.
                # 기본값이 1이어도 계약 필드라 명시적으로 싣는다.
                "schema_version": 1,
                "overview": "[stub] 실제 문서는 엔진 이식 후 생성됩니다.",
                "structure": [
                    {"area": "진입점", "files": ["app/api/orders.py"],
                     "role": "요청을 받아 서비스로 넘긴다"},
                    {"area": "결제", "files": ["app/services/payment_1.py"],
                     "role": "결제 수단별 분기와 원장 기록"},
                ],
                # 두 번째 항목은 근거 검증에 실패한 경우다. 백엔드가 이 모양을
                # 실제로 받아보고 렌더 분기를 짜도록 스텁에 일부러 섞어 둔다.
                "decision_points": [
                    {
                        "title": "결제 수단 분기를 함수 안에서 처리",
                        "source_path": "app/services/payment_1.py",
                        "symbol": "def pay(order, method):",
                        "line_start": _HIGHLIGHT_START,
                        "line_end": _HIGHLIGHT_END,
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
                    "verdict": "PASS",
                    "evidence": f"app/services/payment_1.py:{_HIGHLIGHT_START}",
                    "note": None,
                }
                for req in request.get("requirements", [])
            ],
            "problems": [
                _stub_problem(i + 1,
                              focus_ids[i] if i < len(focus_ids) else None,
                              teach_id)
                for i, teach_id in enumerate(anchors)
            ],
            "unmatched_teaches": [
                {"teach_id": teach_id,
                 "reason": "[stub] 제출 코드에서 이 개념의 근거를 찾지 못했습니다"}
                for teach_id in unmatched_ids
            ],
            "question_count_planned": budget,  # 요청 예산 반영(문제 수와 별개다)
            # D-analysis-b1(2026-08-07)로 AnalysisResult에 생긴 5개. 스텁이 안 채우면
            # 백엔드는 "이 필드는 안 온다"로 배운다 -- 실엔진이 붙는 날 조용히 어긋난다.
            "resolved_branch": branch,
            "head_commit": {
                "commit_hash": _HEAD_SHA,
                "commit_message": "feat: add payment method branch",
                "committed_at": "2026-08-11T14:22:11Z",
            },
            "git_history": _stub_git_history(branch),
            "git_history_source": "EMBEDDED_GIT",
            "history_truncated": False,
            # jobs.py가 pop해서 원장으로 옮긴다(결과 본문에는 안 남는다).
            "ai_usage": _stub_ai_usage(
                request.get("provider_model_code") or get_settings().model_code_analysis,
                len(anchors),
            ),
        }
