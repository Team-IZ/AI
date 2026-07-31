""" Tier 2(크루)의 원시 응답을 검증한다 -- 순수 함수, closed-vocabulary 강제

크루가 자유 서술로 지어낼 수 있는 여지를 구조적으로 없앤다: path는 반드시
Tier 1이 이미 보여준 후보 목록(allowed_paths) 안에 있어야 하고, role/reason_code는
고정된 열거값 중 하나여야 한다. 이 셋 중 하나라도 어긋나면 그 항목 전체를
버린다(rejected에 이유 코드로 남긴다) -- 모델의 자유 텍스트가 CodeMap까지
도달하는 경로는 이 파일 어디에도 없다.

D13 (2026-07-31): ALLOWED_REASON_CODES에 MATCHES_TEACH/MATCHES_REQUIREMENT 두
  값을 추가한다 -- Tier 2가 curriculum_block(crew.py D13)을 받게 되면서, 모델이
  "이 파일이 교안/요구사항과 관련 있어서 올린다"고 말할 자리가 필요해졌다.
  WHY 열거값을 늘리는가(자유 텍스트가 아니라): 이유를 표현할 자리가 없으면 모델은
    기존 5개 중 아무거나(대개 BUSINESS_LOGIC) 골라 붙인다 -- 그러면 사후에
    "이 재랭킹이 교안 때문인지 로직 때문인지"를 구분할 수 없어 PR-3 실측에서
    Tier 2의 기여를 분해할 수 없다. 열거값을 늘리는 건 closed-vocabulary 원칙의
    예외가 아니라 그 원칙 안에서 어휘를 정의하는 정상 경로다.
  COST: 어휘가 5개 -> 7개. 모델이 고를 수 있는 오답이 2개 늘어난다.
  불변식(약화 금지): 검증 실패 시 **항목 전체를 버린다**는 규칙은 그대로다 --
    reason_code만 모르는 값이라고 나머지 필드를 살려 쓰지 않는다. teach id나
    requirement id를 claim에 담게 하지도 않는다: 그건 새 closed-vocabulary
    (allowed_teach_ids)를 이 함수에 들여야 한다는 뜻이고, 그럴 필요가 생기면
    analysis_doc.py::parse_analysis_doc_response()가 이미 쓰는 검증 방식을
    그대로 가져오는 게 맞다(지금은 그 요구가 없어 안 만든다).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.codemap.models import CodeMapEntry, CrewClaim, RankedFile

ALLOWED_ROLES = frozenset({
    "ENTRY_POINT", "ROUTING", "DOMAIN_LOGIC", "DATA_ACCESS",
    "INTEGRATION", "CONFIG", "UI", "TEST", "UTIL",
})

ALLOWED_REASON_CODES = frozenset({
    "IMPORT_HUB", "BUSINESS_LOGIC", "RISK_SURFACE",
    "ENTRY_POINT_CONFIRMED", "REDUNDANT_WITH_HIGHER_RANK",
    "MATCHES_TEACH", "MATCHES_REQUIREMENT",  # D13
})


def parse_rerank(
    raw: Mapping[str, Any],
    allowed_paths: frozenset[str],
    allowed_roles: frozenset[str] = ALLOWED_ROLES,
) -> tuple[tuple[CrewClaim, ...], tuple[str, ...]]:
    """ raw = extract_json_object()가 파싱한 {"changes": [...]}.

    유효성 조건(전부 만족해야 채택):
      - path가 문자열이고 allowed_paths(Tier 1이 보여준 후보 목록)에 있음
      - role이 allowed_roles 중 하나
      - reason_code가 ALLOWED_REASON_CODES 중 하나
      - delta_rank가 정수로 변환 가능
      - 같은 path를 두 번 주장하지 않음(첫 번째만 채택, 두 번째부터는 거부)

    반환: (채택된 CrewClaim 튜플, 거부 사유 문자열 튜플). 거부 사유는 고정된 사유
    코드뿐이다 -- path가 이미 검증돼 allowed_paths 안에 있는 경우(DUPLICATE_PATH)만
    그 값을 같이 남기고, 그 외에는 모델이 보낸 원문 값(role/reason_code/delta_rank나
    검증 실패한 path 자체)을 절대 문자열에 끼워 넣지 않는다 -- 자유 서술이 rejected를
    통해서라도 새어나가지 않게 하기 위함이다.
    """
    changes = raw.get("changes")
    if not isinstance(changes, list):
        return (), ("INVALID_CHANGES_SHAPE",)

    claims: list[CrewClaim] = []
    rejected: list[str] = []
    seen_paths: set[str] = set()

    for item in changes:
        if not isinstance(item, Mapping):
            rejected.append("INVALID_ITEM_SHAPE")
            continue

        path = item.get("path")
        role = item.get("role")
        reason_code = item.get("reason_code")
        delta_rank_raw = item.get("delta_rank")

        if not isinstance(path, str) or path not in allowed_paths:
            rejected.append("UNKNOWN_PATH")
            continue
        if path in seen_paths:
            rejected.append(f"DUPLICATE_PATH:{path}")  # path는 이미 allowed_paths로 검증된 값
            continue
        if role not in allowed_roles:
            rejected.append("UNKNOWN_ROLE")
            continue
        if reason_code not in ALLOWED_REASON_CODES:
            rejected.append("UNKNOWN_REASON_CODE")
            continue
        try:
            delta_rank = int(delta_rank_raw)
        except (TypeError, ValueError):
            rejected.append("INVALID_DELTA_RANK")
            continue

        seen_paths.add(path)
        claims.append(CrewClaim(path=path, role=role, delta_rank=delta_rank, reason_code=reason_code))

    return tuple(claims), tuple(rejected)


def merge_rerank(
    tier1: Sequence[RankedFile], claims: Sequence[CrewClaim], *, max_rank_shift: int
) -> tuple[CodeMapEntry, ...]:
    """ Tier 1 순위에 클램프된 delta_rank를 적용해 최종 순서를 만든다 (순수 함수)

    claims가 비어 있으면(크루를 안 쓰거나 전부 거부됐으면) tier1 순서를 그대로
    보존한다 -- target_position이 전부 rf.rank와 같아지므로 정렬 결과가 동일하다.

    target_position이 같은 두 항목이 충돌하면(예: claim이 상위 항목의 자리로 밀고
    들어오는 경우), claim이 있는 쪽을 우선한다 -- 그러지 않으면 "원래 순위가 더
    높았다"는 이유로 밀려나야 할 쪽이 오히려 타이브레이크를 이겨서 delta_rank가
    조용히 무효화된다(클램프와는 다른 문제 -- 클램프는 의도적 제한이지만 이건
    "이동 자체가 아예 안 먹히는" 버그였다).
    """
    claim_by_path = {c.path: c for c in claims}

    scored = []
    for rf in tier1:
        claim = claim_by_path.get(rf.path)
        if claim is not None:
            clamped = max(-max_rank_shift, min(max_rank_shift, claim.delta_rank))
            target = rf.rank - clamped
            has_claim = 0  # 정렬 시 0이 1보다 먼저 오므로, claim 있는 쪽이 동점에서 이긴다
            role, reason_code = claim.role, claim.reason_code
        else:
            target = rf.rank
            has_claim = 1
            role, reason_code = None, None
        scored.append((target, has_claim, rf.rank, rf.path, rf.rank_score, role, reason_code))

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    return tuple(
        CodeMapEntry(
            path=path, rank=i, tier1_rank=tier1_rank, tier1_score=tier1_score,
            role=role, selected=True, reason_code=reason_code,
        )
        for i, (_target, _has_claim, tier1_rank, path, tier1_score, role, reason_code) in enumerate(scored, start=1)
    )
