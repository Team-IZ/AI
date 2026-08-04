""" p01 교안 분석. PDF → 모듈(section) + 개념(teach).

**다른 넷과 완전히 별개 흐름이다.** 교사가 교안을 올릴 때 백그라운드로 돌고,
결과는 나중에 코드 분석이 `teaches`로 받아 쓴다. 세션·채점과는 연결이 없다.

## PoC와 다른 점 둘

**① PDF 추출을 CLI가 아니라 라이브러리로 한다.** PoC(`java_curriculum_nvidia_pipeline.py`)는
`pdftotext`/`pdfinfo`(poppler)를 `subprocess`로 부른다. **배포 환경에 그 바이너리가 없다** —
App Runner 관리형 런타임은 시스템 패키지를 못 깐다. `pypdf`는 순수 파이썬이라 pip으로 끝난다.

**② 파이프라인을 p01-2에서 끊는다.** PoC는 청크 분석 뒤에 refine 루프(p01-3)·그래프
빌드·질문 생성(p01-4)까지 간다. 우리 계약(`CurriculumResult`)이 요구하는 것은
**section + teach 두 계층뿐**이라 나머지는 만들 자리가 없다. 필요해지면 그때 잇는다.

## 청크가 독립이라 병렬이 가능하다

청크 하나가 실패해도 나머지는 살린다 — 251페이지 실측에서 26청크 중 2건이 깨졌다.
**한 청크를 잃는 것은 그 페이지 범위의 개념을 잃는 것**이고, 전체를 버리는 것보다 낫다.
`fallback_used`로 그 사실을 알린다.
"""

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import stages
from app.engines.analysis.hints import MAX_PARALLEL

# 청크 하나의 상한. **쪽 수와 글자 수 둘 다 본다 — 먼저 걸리는 쪽이 이긴다.**
#
# 🔴 쪽 수만 보면 깨진다 (2026-08-02 실측). PoC 기본값 10쪽은 **슬라이드 교안**
# 기준이다(쪽당 200~400자). 텍스트가 빽빽한 PDF는 쪽당 1,600자여서 10쪽이면
# 9,800자가 되고, `p01-2`의 `max_tokens=3600`으로는 그 안의 개념을 다 못 쓴다.
# 실제로 응답 JSON이 중간에서 잘렸고(`INVALID_JSON`, 출력 3600 소진),
# 예산을 2배로 올린 재시도는 302초를 태우고 `PROVIDER_ERROR`로 끝났다.
#
# **출력 예산을 늘리는 방향은 답이 아니다.** 긴 응답일수록 지연이 그대로 늘고,
# 실패율 32%인 채널에서 잘릴 확률도 같이 커진다. 입력을 줄이는 쪽이 맞다.
PAGES_PER_CHUNK = 10
CHARS_PER_CHUNK = 4000

# 분석 파이프라인 버전. 결과 재현성의 근거라 로직이 바뀌면 올린다.
ANALYSIS_VERSION = 1


@dataclass
class Curriculum:
    sections: list[dict[str, Any]]
    usages: list[dict[str, Any]] = field(default_factory=list)
    failed_chunks: list[str] = field(default_factory=list)


def extract_pages(pdf_bytes: bytes) -> list[str]:
    """PDF를 페이지별 텍스트로. 인덱스 0이 1쪽이다.

    추출 실패한 페이지는 빈 문자열로 남긴다 — 건너뛰면 뒤 페이지 번호가 밀리고,
    그러면 개념의 `sourcePages`가 통째로 어긋난다.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception:
            pages.append("")
    return pages


def build_chunks(pages: list[str], pages_per_chunk: int = PAGES_PER_CHUNK,
                 chars_per_chunk: int = CHARS_PER_CHUNK) -> list[tuple[int, int, str]]:
    """(시작쪽, 끝쪽, 본문). 쪽 번호는 1부터다.

    쪽 수와 글자 수 중 **먼저 걸리는 쪽**에서 끊는다. 쪽을 쪼개지는 않는다 —
    쪽 경계를 넘어 자르면 개념 하나가 두 청크에 걸쳐 양쪽 다 반쪽만 보게 된다.

    한 쪽이 혼자 상한을 넘으면 그 쪽만 담아 보낸다. 더 잘게 나눌 수단이 없고,
    매니페스트의 `chunk_text` 상한(18,000자)이 최종 방어선이다.
    """
    chunks: list[tuple[int, int, str]] = []
    start: int | None = None
    buffer: list[str] = []
    size = 0

    def flush(end: int) -> None:
        text = "\n\n".join(buffer).strip()
        if text:      # 그림만 있는 구간은 보낼 것이 없다
            chunks.append((start, end, text))

    for no, text in enumerate(pages, start=1):
        too_many_pages = len(buffer) >= pages_per_chunk
        too_many_chars = buffer and size + len(text) > chars_per_chunk
        if too_many_pages or too_many_chars:
            flush(no - 1)
            start, buffer, size = None, [], 0

        if start is None:
            start = no
        buffer.append(text)
        size += len(text)

    if buffer:
        flush(len(pages))
    return chunks


def _normalize(name: str) -> str:
    """중복 판정용 이름. 공백·대소문자·구두점 차이를 지운다.

    같은 개념이 청크마다 다른 표기로 오면(`"try-catch"` / `"try catch"`) 별개
    teach가 되고, 강사가 3건을 고를 때 같은 것이 두 번 뜬다.
    """
    return re.sub(r"[^0-9a-z가-힣]+", " ", name.lower()).strip()


def _pages(value: Any, lo: int, hi: int) -> tuple[int | None, int | None]:
    """모델이 준 페이지 목록을 (시작, 끝)으로. 청크 범위를 벗어난 값은 버린다.

    범위 밖 번호는 모델이 지어낸 것이다 — 그대로 두면 학생·강사가 없는 쪽을 편다.
    """
    nums = sorted({p for p in (value or []) if isinstance(p, int) and lo <= p <= hi})
    if not nums:
        return None, None
    return nums[0], nums[-1]


# 사람이 읽는 필드를 한국어로 고정한다.
#
# 매니페스트(p01-2)는 영어로 쓰였고 언어 지시가 없다. 영어 교안을 넣으면 `unit_title`·
# `summary`·`evidence`가 전부 영어로 나오는데, **교육생과 강사가 읽는 화면이라 한국어여야
# 한다**(2026-08-03). 교안 언어가 무엇이든 결과는 한국어다.
#
# vendor를 고치지 않고 우리 소유 경로(`stages.call(extra_user=...)`)로 붙인다 —
# 매니페스트를 건드리면 팀원 갱신(덮어쓰기 복사)마다 재적용해야 한다.
#
# 🔴 **번역하면 안 되는 것을 명시한다.** JSON 키·`unit_id`·`kind` 값은 계약이고,
# 기술 용어를 억지로 옮기면(예: "handoff" → "인계") 나중에 그 개념으로 문제를 낼 때
# 교안 원문과 대조가 안 된다.
_KOREAN_OUTPUT = (
    "\n\n## 출력 언어\n"
    "사람이 읽는 값은 **한국어**로 써라 — `unit_title`, `summary`, `evidence`.\n"
    "교안이 영어로 쓰여 있어도 마찬가지다.\n"
    "- JSON 키와 `unit_id`·`kind`의 값은 그대로 둔다(계약이다).\n"
    "- 기술 용어·API 이름·코드 식별자는 원문 그대로 쓴다"
    "(예: guardrail, handoff, function calling). 억지로 옮기지 마라.\n"
    "- 원문 용어를 처음 쓸 때만 괄호로 짧게 풀어도 된다 — 예: guardrail(안전장치)."
)


def analyse_chunk(start: int, end: int, text: str, *, model_code: str,
                  course_label: str = "") -> dict[str, Any]:
    """청크 하나 → {units, concepts}. p01-2 호출."""
    values = {
        "chunk_range": f"{start}-{end}",
        "chunk_start": start,
        "chunk_end": end,
        "chunk_text": text,
    }
    if course_label:
        values["course_label"] = course_label
    return stages.call("p01-2", values, model_code=model_code,
                       extra_user=_KOREAN_OUTPUT)


# p01-2가 주는 kind를 우리 계약 값으로. 모르는 값은 CONCEPT로 떨어뜨린다 —
# 알 수 없는 문자열을 그대로 내보내면 Spring CHECK에 걸린다.
_KIND_MAP = {"concept": "CONCEPT", "code_example": "CODE_EXAMPLE", "caution": "CAUTION"}


def _kind(raw: Any) -> str:
    return _KIND_MAP.get(str(raw or "").strip().lower(), "CONCEPT")


def _with_siblings(teaches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 unit의 다른 개념 이름을 각 teach에 달아준다.

    **교안이 대안을 가르쳤다는 신호**다(PM 설계 v2 §7-3의 `siblings`). 형제가 있으면
    "학생이 그중 하나를 골랐다"가 확실해지므로 문제로 내기 좋은 지점이 된다.

    새로 받을 것이 없다 — unit 묶음이 이미 있으므로 계산만 하면 된다.
    """
    names = [t["normalized_name"] for t in teaches]
    for teach in teaches:
        teach["sibling_names"] = [n for n in names if n != teach["normalized_name"]]
    return teaches


# 두 청크의 같은 단원을 이어 붙일 때 허용하는 페이지 간격.
#
# 청크 경계에 단원이 걸치면 앞 청크가 p.10까지, 뒤 청크가 p.11부터를 보고한다 —
# 그 둘은 이어야 한다. 하지만 p.4의 "에이전트란"과 p.30의 "에이전트란"은 다른 단원이다
# (교안이 같은 제목을 다시 쓴 것이거나 모델이 요약을 재사용한 것).
_ADJACENT_PAGES = 2


def _merge(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """청크별 결과를 단원으로 합쳐 section 목록으로.

    🔴 **`unit_id`로 합치지 않는다** (2026-08-04 수정). 모델은 `unit_id`를 **청크마다
    독립적으로** `"01"`, `"02"`로 매긴다 — 청크 1의 `"02"`와 청크 5의 `"02"`는 완전히 다른
    주제인데 같은 단원으로 합쳐졌다. 실측(34쪽 교안)에서 그 결과가

        [1] p.4–6    teaches  7
        [2] p.5–31   teaches 48      ← 27쪽짜리 "단원". 범위가 서로 겹친다
        [3] p.8–34   teaches 15

    이었고, 그래서 `siblingNames`가 평균 31개가 됐다 — **"교안이 대안을 가르쳤다"는 신호로
    못 쓴다.** 전부가 형제면 아무것도 구분하지 못한다.

    **제목이 같고 페이지가 이어질 때만 합친다.** 청크 경계에 걸친 단원은 이어지고,
    멀리 떨어진 동명 단원은 따로 남는다.
    """
    units: list[dict[str, Any]] = []

    for chunk in results:
        lo, hi = chunk["_range"]
        # 이 청크 안에서 unit_id → 방금 만든(또는 이어붙인) 단원. 개념을 붙일 때 쓴다.
        local: dict[str, dict[str, Any]] = {}

        for unit in chunk.get("units") or []:
            if not isinstance(unit, dict):
                continue
            uid = str(unit.get("unit_id") or "").strip()
            title = str(unit.get("unit_title") or "").strip()
            if not uid or not title:
                continue
            page_start, page_end = _pages(unit.get("source_pages"), lo, hi)
            page_start = page_start or lo
            page_end = page_end or hi

            merged = _find_continuation(units, title, page_start)
            if merged is None:
                merged = {"title": title, "normalized_title": _normalize(title),
                          "page_start": page_start, "page_end": page_end, "teaches": {}}
                units.append(merged)
            else:
                merged["page_start"] = min(merged["page_start"], page_start)
                merged["page_end"] = max(merged["page_end"], page_end)
            local[uid] = merged

        for concept in chunk.get("concepts") or []:
            if not isinstance(concept, dict):
                continue
            name = str(concept.get("name") or "").strip()
            uid = str(concept.get("unit_id") or "").strip()
            unit = local.get(uid)
            if not name or unit is None:
                continue      # 어느 단원에 속하는지 모르면 화면에 놓을 자리가 없다
            key = _normalize(name)
            if not key or key in unit["teaches"]:
                continue      # 먼저 온 것을 남긴다 — 청크 순서가 곧 교안 순서다
            page_start, page_end = _pages(concept.get("source_pages"), lo, hi)
            unit["teaches"][key] = {
                "canonical_name": name[:200],
                "normalized_name": key[:200],
                "canonical_description": (str(concept.get("summary") or "").strip() or None),
                "description_page_start": page_start,
                "description_page_end": page_end,
                # 🔴 예전엔 버렸다. p01-2가 이미 답에 담아 보내는 값이라
                # **LLM 호출이 늘지 않는다** — 문제 선정의 재료다(PM 설계 v2 §7).
                "kind": _kind(concept.get("kind")),
                "evidence": (str(concept.get("evidence") or "").strip() or None),
            }

    ordered = sorted(units, key=lambda u: (u["page_start"], u["page_end"]))
    return [
        {
            "module_no": no,
            "title": unit["title"][:200],
            "page_start": unit["page_start"],
            "page_end": max(unit["page_end"], unit["page_start"]),
            "teaches": _with_siblings(list(unit["teaches"].values())),
        }
        for no, unit in enumerate(ordered, start=1)
    ]


def _find_continuation(units: list[dict[str, Any]], title: str,
                       page_start: int) -> dict[str, Any] | None:
    """이 단원이 앞서 만든 단원의 **연장**인가.

    같은 제목이고 페이지가 바로 이어질 때만 그렇다. 제목만 같고 멀리 떨어져 있으면
    다른 단원이다 — 합치면 페이지 범위가 교안 절반을 덮는다.
    """
    key = _normalize(title)
    for unit in reversed(units):        # 최근 것부터. 교안은 앞에서 뒤로 흐른다
        if unit["normalized_title"] != key:
            continue
        if page_start - unit["page_end"] <= _ADJACENT_PAGES:
            return unit
    return None


def analyse(pdf_bytes: bytes, *, model_code: str, course_label: str = "") -> Curriculum:
    """PDF 하나를 분석한다.

    **청크 실패는 그 범위만 잃는다.** 251페이지 실측에서 26청크 중 2건이 깨졌는데,
    전체를 버리면 나머지 24청크의 토큰까지 헛돈다.
    """
    chunks = build_chunks(extract_pages(pdf_bytes))
    if not chunks:
        return Curriculum(sections=[])

    # 청크는 서로를 모른 채 독립으로 돈다 — 순차로 돌 이유가 없다.
    # 251쪽 교안이면 26청크이고, 콜당 1~2분이면 순차는 30분을 넘는다.
    ordered: list[Any] = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {
            pool.submit(analyse_chunk, start, end, text,
                        model_code=model_code, course_label=course_label): i
            for i, (start, end, text) in enumerate(chunks)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                ordered[i] = future.result()
            except stages.StageError as exc:
                ordered[i] = exc

    results: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    failed: list[str] = []

    # **병합 순서는 청크 순서여야 한다.** 개념 중복은 "먼저 온 것"을 남기는데,
    # 완료 순서로 합치면 그 "먼저"가 실행마다 달라져 결과가 재현되지 않는다.
    for (start, end, _), outcome in zip(chunks, ordered):
        if isinstance(outcome, stages.StageError):
            usages.extend(outcome.usages)
            failed.append(f"{start}-{end}")
            continue
        usages.extend(outcome.usages)
        results.append({**outcome.data, "_range": (start, end)})

    return Curriculum(sections=_merge(results), usages=usages, failed_chunks=failed)
