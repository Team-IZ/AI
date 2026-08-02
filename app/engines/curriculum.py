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
from dataclasses import dataclass, field
from typing import Any

from app.engines.analysis import stages

# 청크 하나에 담을 페이지 수. PoC 기본값과 같다 — 매니페스트의 chunk_text 상한
# (18,000자)이 이 크기를 전제로 잡혀 있다.
PAGES_PER_CHUNK = 10

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


def build_chunks(pages: list[str], pages_per_chunk: int = PAGES_PER_CHUNK
                 ) -> list[tuple[int, int, str]]:
    """(시작쪽, 끝쪽, 본문). 쪽 번호는 1부터다."""
    chunks = []
    for start in range(1, len(pages) + 1, pages_per_chunk):
        end = min(start + pages_per_chunk - 1, len(pages))
        text = "\n\n".join(pages[start - 1:end]).strip()
        if text:      # 그림만 있는 구간은 보낼 것이 없다
            chunks.append((start, end, text))
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

    results: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    failed: list[str] = []

    for start, end, text in chunks:
        try:
            result = analyse_chunk(start, end, text,
                                   model_code=model_code, course_label=course_label)
        except stages.StageError as exc:
            usages.extend(exc.usages)
            failed.append(f"{start}-{end}")
            continue
        usages.extend(result.usages)
        results.append({**result.data, "_range": (start, end)})

    return Curriculum(sections=_merge(results), usages=usages, failed_chunks=failed)
