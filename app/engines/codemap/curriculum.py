""" 교안(teaches)/요구사항(requirements) <-> 코드의 결정론적 겹침 계산 -- 순수 함수만

D13 (2026-07-31, 사용자 결정 "teaches/requirements가 랭킹 자체에 반영돼야 한다"):
  이 모듈은 그 결정의 Tier 1 쪽 절반이다 -- 나머지 절반은 Tier 2(crew.py의 D13,
  ground.py의 D13). 여기서 만든 값은 rank.py의 6번째 신호(curriculum)가 된다.

  WHY 이 신호가 "poc_full의 Tier B(auth/eval/secret 정규식 위험 트리거)"와 다른가:
    Tier B가 폐기된 이유는 "정규식이라서"가 아니라 **고정 어휘**였기 때문이다 --
    auth/eval/secret 세 단어는 어느 제출물에나 똑같이 적용되고, 그 단어가 그
    저장소에서 변별력이 있는지 없는지를 아무도 확인하지 않는다(모든 파일이
    'auth'를 포함하는 저장소에서도 똑같이 발화한다). 이 모듈에는 **하드코딩된
    도메인 어휘가 하나도 없다**:
      1) 어휘는 전부 호출자가 준 강사 작성 데이터(teach.label, requirement.text)다
         -- 우리가 "무엇이 중요한 단어인가"를 추측하지 않는다.
      2) 그 어휘 중 무엇이 변별력 없는(=generic) 단어인지는 **그 제출물 코퍼스에서
         실측한 document frequency로 판정한다**(_GENERIC_DF_RATIO). 전 저장소
         공통 stopword 목록이 아니라 제출물마다 다시 계산되는 값이다.
    즉 Tier B의 실패 원인(고정 어휘 + 변별력 미확인)을 둘 다 제거한 형태다.

  COST: 그래도 이건 의미 이해가 아니라 토큰 겹침이다. "예외 처리를 구현한다"는
    teach가 `try`/`catch`를 쓴 파일을 못 잡는다(단어가 안 겹침) -- 재현율은
    구조적으로 낮다. 이 신호는 **재현율이 아니라 정밀도를 위해 설계**됐다:
    확신이 없으면 0.0으로 기권하고, 기권한 파일은 오늘과 정확히 같은 순위를
    받는다(rank.py D13의 "입력 없으면 분모에서도 빠진다" 규칙과 짝).
  EXIT: 이 신호의 재현율이 부족하다고 판명되면(PR-3 실측), 의미 판단은 Tier 2가
    맡는 것이 원래 설계다(crew.py D13) -- 이 모듈을 억지로 똑똑하게 만들지 말 것.
    임베딩/동의어 사전을 여기 넣는 순간 D2(순수성: 무작위성/시계/네트워크 없음)와
    "가중치 재보정 = 파일 교체 하나"라는 weights.py의 롤백 경로가 둘 다 깨진다.

D14(강사 입력 에코 vs 모델 자유 서술): dropped_generic/dropped_short에는 강사가
  작성한 원문 토큰이 그대로 담긴다. 이건 ground.py의 "모델 자유 서술 절대 미노출"
  원칙과 충돌하지 않는다 -- 그 원칙이 막는 것은 **LLM이 만들어낸 문자열**이
  검증 없이 결과에 도달하는 경로이고, 여기 담기는 값은 요청자가 직접 보낸 입력을
  되돌려주는 것뿐이다(analysis_doc.py::build_problems()의 ungrounded 목록이
  이미 같은 근거로 검증된 file 값을 담고 있는 것과 같은 판단).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.engines.codemap.models import CurriculumMatch, RepoFile

# 교안 문구/코드 양쪽에서 "단어"로 인정할 것: ASCII 식별자 또는 한글 음절 덩어리.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[가-힣]+")

# camelCase/snake_case/PascalCase 분해용. verifyJwtToken -> verify, jwt, token
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

_HANGUL_RE = re.compile(r"^[가-힣]+$")

# 길이 게이트: 짧은 토큰은 우연 일치율이 급격히 올라간다("id", "api", "for").
# 한글은 2음절이면 이미 실질 개념어("예외", "상속")라 별도 기준을 쓴다.
_MIN_ASCII_TERM_LEN = 4
_MIN_HANGUL_TERM_LEN = 2

# 변별력 게이트: 후보 파일 중 이 비율을 넘는 파일에 나타나는 토큰은 그 제출물에서
# 변별력이 없다고 보고 버린다.
#
# 값 근거(실측, 2026-07-31, 이 저장소 app/ 49개 파일 · teaches만 · requirements 없음):
#   ratio   specific(진짜관련)  generic(코드일반어)  nonsense(무관대조군)
#   0.10    발화  4.1%          발화 20.4%           ABSTAIN
#   0.30    발화 34.7%          발화 20.4%           ABSTAIN
# 0.30을 고른 이유: 0.10 -> 0.30으로 완화해도 "무의미한 교안"(generic)의 발화율은
# 20.4%로 **전혀 늘지 않는데**(그 토큰들은 이미 df 게이트에 걸려 있다) "진짜 관련
# 있는 교안"(specific)의 발화율만 4.1% -> 34.7%로 오른다 -- 이 데이터에서 0.30은
# 0.10을 지배(dominate)하는 운영점이다. 무관한 교안(nonsense)은 두 값 모두에서
# 기권하므로 이 완화가 오탐을 새로 만들지도 않는다.
# 재보정 조건: 실제 학생 제출물(PR-3)에서 nonsense 대조군이 더 이상 기권하지
# 않거나 generic 발화율이 specific을 넘어서면 다시 조인다.
_GENERIC_DF_RATIO = 0.30

# 변별력 게이트의 절대 하한. 비율만 쓰면 작은 저장소에서 게이트가 무너진다:
# 파일이 2개인 제출물에서는 **한 파일에만** 있는 토큰도 df 비율 50% > 30%라
# "너무 흔하다"로 버려져 신호가 항상 기권한다(tests/test_codemap_curriculum.py의
# 매칭 테스트들이 이걸 처음 잡아냈다 -- 비율 게이트만으로는 소규모 코퍼스에서
# 의미가 정반대로 뒤집힌다). "3개 파일 미만에 나타나는 토큰은 흔한 게 아니다"는
# 하한을 둬서 게이트가 코퍼스 크기와 무관하게 같은 뜻을 갖게 한다.
_MIN_GENERIC_DF_FILES = 3


@dataclass(frozen=True)
class _Item:
    """ 매칭 단위 하나(teach 1건 또는 requirement 1건)와 그것이 남긴 유효 토큰들 """

    item_id: str
    kind: str  # "TEACH" | "REQUIREMENT"
    terms: frozenset[str]


def _subtokens(token: str) -> set[str]:
    """ 식별자 하나 -> 소문자 부분토큰 집합(자기 자신 포함).

    verifyJwtToken -> {verifyjwttoken, verify, jwt, token}
    user_repository -> {user_repository, user, repository}
    한글 토큰은 분해하지 않고 그대로 둔다.
    """
    if _HANGUL_RE.match(token):
        return {token}
    out = {token.lower()}
    for part in token.split("_"):
        if not part:
            continue
        out.add(part.lower())
        for m in _CAMEL_SPLIT_RE.finditer(part):
            out.add(m.group(0).lower())
    return out


def _is_usable_term(term: str) -> bool:
    if _HANGUL_RE.match(term):
        return len(term) >= _MIN_HANGUL_TERM_LEN
    return len(term) >= _MIN_ASCII_TERM_LEN


def _terms_of(text: str) -> set[str]:
    """ 자유 문구 -> 매칭에 쓸 토큰 집합(길이 게이트까지 적용) """
    terms: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        for sub in _subtokens(m.group(0)):
            if _is_usable_term(sub):
                terms.add(sub)
    return terms


def _all_terms_of(text: str) -> set[str]:
    """ 길이 게이트 이전의 전체 토큰 -- dropped_short를 정직하게 보고하기 위해 필요 """
    terms: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        terms |= _subtokens(m.group(0))
    return terms


def file_tokens(f: RepoFile) -> frozenset[str]:
    """ 파일 하나의 토큰 집합. 경로와 본문을 같은 방식으로 분해한다 --
    'src/auth/JwtFilter.java'의 경로 조각도 코드 식별자와 동등하게 취급한다. """
    tokens: set[str] = set()
    for chunk in (f.path.replace("/", " ").replace(".", " "), f.text):
        for m in _TOKEN_RE.finditer(chunk):
            tokens |= _subtokens(m.group(0))
    return frozenset(tokens)


def _collect_items(
    teaches: Sequence[Mapping[str, Any]], requirements: Sequence[Mapping[str, Any]]
) -> tuple[list[_Item], set[str]]:
    """ 요청의 teaches/requirements -> (_Item 목록, 길이 게이트로 버려진 토큰들)

    teach의 개념명 필드는 두 이름을 모두 받는다: AnalysisRequest.teaches는
    `label`(analysis_doc.py::_build_teaches_block과 동일)이지만, 같은 openapi.json의
    Teach 스키마(교안 파이프라인)는 `canonicalName`을 쓴다 -- 어느 쪽이 오든
    조용히 0건으로 빠지는 것보다 둘 다 읽는 편이 낫다.
    """
    items: list[_Item] = []
    dropped_short: set[str] = set()

    for i, t in enumerate(teaches or []):
        if not isinstance(t, Mapping):
            continue
        label = t.get("label") or t.get("canonicalName") or ""
        terms = _terms_of(str(label))
        dropped_short |= _all_terms_of(str(label)) - terms
        if terms:
            items.append(_Item(item_id=str(t.get("id") or f"teach#{i}"), kind="TEACH", terms=frozenset(terms)))

    for i, r in enumerate(requirements or []):
        if not isinstance(r, Mapping):
            continue
        text = r.get("text") or ""
        terms = _terms_of(str(text))
        dropped_short |= _all_terms_of(str(text)) - terms
        if terms:
            rid = str(r.get("requirementId") or r.get("requirement_id") or f"req#{i}")
            items.append(_Item(item_id=rid, kind="REQUIREMENT", terms=frozenset(terms)))

    return items, dropped_short


def match_curriculum(
    files: Sequence[RepoFile],
    teaches: Sequence[Mapping[str, Any]] | None,
    requirements: Sequence[Mapping[str, Any]] | None,
    *,
    generic_df_ratio: float = _GENERIC_DF_RATIO,
) -> CurriculumMatch | None:
    """ 파일 목록 + 교안/요구사항 -> CurriculumMatch. 쓸 게 없으면 None(순수 함수).

    None을 돌려주는 경우(= "이 신호는 이번 실행에서 아무 말도 하지 않는다"):
      - 파일이 없거나
      - teaches/requirements가 비었거나
      - 모든 토큰이 길이 게이트/변별력 게이트에서 걸러졌을 때
    None은 rank.py에서 **분모에서도 빠지는 것**으로 처리되어, 이 신호가 없던
    시절과 바이트 단위로 같은 랭킹이 나온다(rank.py D13, 테스트로 고정).
    """
    if not files:
        return None

    items, dropped_short = _collect_items(teaches or (), requirements or ())
    if not items:
        return None

    tokens_by_path = {f.path: file_tokens(f) for f in files}
    total = len(tokens_by_path)

    candidate_terms: set[str] = set()
    for item in items:
        candidate_terms |= item.terms

    # 변별력 게이트: 이 저장소에서 너무 흔한 토큰은 버린다(제출물마다 재계산).
    document_frequency = {
        term: sum(1 for tokens in tokens_by_path.values() if term in tokens) for term in candidate_terms
    }
    dropped_generic = {
        t for t, df in document_frequency.items()
        if df >= _MIN_GENERIC_DF_FILES and df > total * generic_df_ratio
    }
    surviving = {t for t in candidate_terms if t not in dropped_generic and document_frequency[t] > 0}
    if not surviving:
        return None

    matches: dict[str, int] = {}
    for path, tokens in tokens_by_path.items():
        hit = sum(1 for item in items if item.terms & surviving & tokens)
        if hit:
            matches[path] = hit
    if not matches:
        return None

    return CurriculumMatch(
        matches=dict(sorted(matches.items())),
        item_count=len(items),
        matched_terms=tuple(sorted(surviving)),
        dropped_generic=tuple(sorted(dropped_generic)),
        dropped_short=tuple(sorted(dropped_short)),
    )
