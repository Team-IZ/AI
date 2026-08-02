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
    return stages.call("p01-2", values, model_code=model_code)


def _merge(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """청크별 결과를 unit 단위로 합쳐 section 목록으로.

    **unit_id로 합친다.** 같은 unit이 여러 청크에 걸치면 페이지 범위가 이어지고
    개념이 한 곳에 모인다. `module_no`는 페이지 순서로 다시 매긴다 — 모델이 준
    unit_id는 문자열이고 교안 안에서 연속이라는 보장이 없다.
    """
    units: dict[str, dict[str, Any]] = {}

    for chunk in results:
        lo, hi = chunk["_range"]
        by_unit_title = {}
        for unit in chunk.get("units") or []:
            if not isinstance(unit, dict):
                continue
            uid = str(unit.get("unit_id") or "").strip()
            title = str(unit.get("unit_title") or "").strip()
            if not uid or not title:
                continue
            page_start, page_end = _pages(unit.get("source_pages"), lo, hi)
            entry = units.setdefault(uid, {
                "title": title, "page_start": page_start or lo,
                "page_end": page_end or hi, "teaches": {},
            })
            entry["page_start"] = min(entry["page_start"], page_start or lo)
            entry["page_end"] = max(entry["page_end"], page_end or hi)
            by_unit_title[uid] = title

        for concept in chunk.get("concepts") or []:
            if not isinstance(concept, dict):
                continue
            name = str(concept.get("name") or "").strip()
            uid = str(concept.get("unit_id") or "").strip()
            if not name or uid not in units:
                continue      # 어느 unit에 속하는지 모르면 화면에 놓을 자리가 없다
            key = _normalize(name)
            if not key or key in units[uid]["teaches"]:
                continue      # 먼저 온 것을 남긴다 — 청크 순서가 곧 교안 순서다
            page_start, page_end = _pages(concept.get("source_pages"), lo, hi)
            units[uid]["teaches"][key] = {
                "canonical_name": name[:200],
                "normalized_name": key[:200],
                "canonical_description": (str(concept.get("summary") or "").strip() or None),
                "description_page_start": page_start,
                "description_page_end": page_end,
            }

    ordered = sorted(units.values(), key=lambda u: (u["page_start"], u["page_end"]))
    return [
        {
            "module_no": no,
            "title": unit["title"][:200],
            "page_start": unit["page_start"],
            "page_end": max(unit["page_end"], unit["page_start"]),
            "teaches": list(unit["teaches"].values()),
        }
        for no, unit in enumerate(ordered, start=1)
    ]


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
