""" p01 교안 분석 — PDF → 모듈 + 개념.

병합이 이 모듈의 어려운 부분이다. 청크는 서로를 모른 채 독립으로 돌기 때문에
같은 unit이 여러 청크에 걸치고, 같은 개념이 다른 표기로 두 번 온다.
"""
from app.engines import curriculum
from app.engines.analysis import stages
from app.schemas.curriculum import CurriculumResult


def _chunk(start, end, units, concepts):
    return {"units": units, "concepts": concepts, "_range": (start, end)}


def test_pages_are_one_indexed_and_gaps_are_kept():
    """추출 실패한 페이지를 건너뛰면 뒤 페이지 번호가 밀려 sourcePages가 어긋난다."""
    chunks = curriculum.build_chunks(["1쪽", "", "3쪽"], pages_per_chunk=1)

    assert [(s, e) for s, e, _ in chunks] == [(1, 1), (3, 3)]   # 빈 쪽은 보낼 것이 없다


def test_chunk_boundaries_cover_every_page():
    chunks = curriculum.build_chunks([f"p{i}" for i in range(1, 26)], pages_per_chunk=10)

    assert [(s, e) for s, e, _ in chunks] == [(1, 10), (11, 20), (21, 25)]


def test_same_unit_across_chunks_is_merged():
    """unit이 청크 경계에 걸치면 페이지 범위가 이어지고 개념이 한 곳에 모인다."""
    sections = curriculum._merge([
        _chunk(1, 10, [{"unit_id": "02", "unit_title": "예외 처리", "source_pages": [4, 10]}],
               [{"name": "try-catch", "unit_id": "02", "summary": "예외를 잡는다",
                 "source_pages": [5]}]),
        _chunk(11, 20, [{"unit_id": "02", "unit_title": "예외 처리", "source_pages": [11, 14]}],
               [{"name": "finally", "unit_id": "02", "summary": "항상 실행된다",
                 "source_pages": [12]}]),
    ])

    assert len(sections) == 1
    assert sections[0]["page_start"] == 4 and sections[0]["page_end"] == 14
    assert [t["canonical_name"] for t in sections[0]["teaches"]] == ["try-catch", "finally"]


def test_duplicate_concept_names_collapse():
    """같은 개념이 다른 표기로 오면 강사가 3건을 고를 때 같은 것이 두 번 뜬다."""
    sections = curriculum._merge([
        _chunk(1, 10, [{"unit_id": "01", "unit_title": "예외", "source_pages": [1]}],
               [{"name": "try-catch", "unit_id": "01", "source_pages": [2]},
                {"name": "Try Catch", "unit_id": "01", "source_pages": [3]}]),
    ])

    assert len(sections[0]["teaches"]) == 1
    assert sections[0]["teaches"][0]["canonical_name"] == "try-catch"   # 먼저 온 것


def test_pages_outside_the_chunk_are_dropped():
    """청크 범위 밖 번호는 모델이 지어낸 것이다. 두면 없는 쪽을 펴게 된다."""
    sections = curriculum._merge([
        _chunk(1, 10, [{"unit_id": "01", "unit_title": "예외", "source_pages": [1, 5]}],
               [{"name": "try", "unit_id": "01", "source_pages": [999]}]),
    ])

    teach = sections[0]["teaches"][0]
    assert teach["description_page_start"] is None
    assert teach["description_page_end"] is None


def test_concept_without_a_known_unit_is_dropped():
    """어느 unit에 속하는지 모르면 화면에 놓을 자리가 없다."""
    sections = curriculum._merge([
        _chunk(1, 10, [{"unit_id": "01", "unit_title": "예외", "source_pages": [1]}],
               [{"name": "떠도는 개념", "unit_id": "99", "source_pages": [2]}]),
    ])

    assert sections[0]["teaches"] == []


def test_module_no_follows_page_order():
    """모델이 준 unit_id는 문자열이고 교안 순서라는 보장이 없다."""
    sections = curriculum._merge([
        _chunk(1, 20, [
            {"unit_id": "07", "unit_title": "뒤", "source_pages": [15, 20]},
            {"unit_id": "02", "unit_title": "앞", "source_pages": [1, 5]},
        ], []),
    ])

    assert [(s["module_no"], s["title"]) for s in sections] == [(1, "앞"), (2, "뒤")]


def test_merged_sections_pass_the_contract():
    """DB CHECK(pageEnd >= pageStart 등)를 스키마가 먼저 검사한다."""
    sections = curriculum._merge([
        _chunk(1, 10, [{"unit_id": "01", "unit_title": "예외 처리", "source_pages": [1, 8]}],
               [{"name": "try-catch", "unit_id": "01", "summary": "예외를 잡는다",
                 "source_pages": [2, 4]}]),
    ])

    CurriculumResult.model_validate({
        "version_id": "v-1", "analysis_version": 1,
        "extraction_status": "EXTRACTED", "sections": sections,
    })


def test_failed_chunk_does_not_lose_the_others(monkeypatch):
    """251쪽 실측에서 26청크 중 2건이 깨졌다. 전체를 버리면 24청크 토큰이 헛돈다."""
    def _call(stage_id, values, *, model_code, max_attempts=2, timeout_s=None):
        if values["chunk_range"] == "1-10":
            raise stages.StageError("p01-2: 터짐", [{"status": "FAILED"}])
        return stages.StageResult(
            data={"units": [{"unit_id": "02", "unit_title": "예외", "source_pages": [11, 15]}],
                  "concepts": [{"name": "try", "unit_id": "02", "source_pages": [12]}]},
            usages=[{"status": "SUCCEEDED"}],
        )

    monkeypatch.setattr(curriculum.stages, "call", _call)
    monkeypatch.setattr(curriculum, "extract_pages",
                        lambda b: [f"{i}쪽" for i in range(1, 21)])   # 청크 2개

    built = curriculum.analyse(b"pdf", model_code="m")

    assert built.failed_chunks == ["1-10"]
    assert [s["title"] for s in built.sections] == ["예외"]   # 살아남은 청크는 나온다
    assert len(built.usages) == 2                             # 실패한 호출의 토큰도 남는다


def test_dense_pages_are_split_by_chars_not_pages():
    """쪽 수만 보면 깨진다 (2026-08-02 실측).

    PoC 기본값 10쪽은 슬라이드 교안(쪽당 200~400자) 기준이다. 텍스트가 빽빽한
    PDF는 쪽당 1,600자여서 10쪽이면 9,800자가 되고, p01-2의 max_tokens=3600으로는
    응답 JSON이 중간에서 잘린다 — 실제로 INVALID_JSON으로 끝났다.
    """
    dense = ["가" * 1600] * 6

    chunks = curriculum.build_chunks(dense)

    assert len(chunks) > 1
    for _, _, text in chunks:
        assert len(text) <= curriculum.CHARS_PER_CHUNK + 2   # 쪽 사이 "\n\n"

    # 쪽을 쪼개지 않는다 — 쪼개면 개념 하나가 두 청크에 반쪽씩 걸린다
    assert [(s, e) for s, e, _ in chunks] == [(1, 2), (3, 4), (5, 6)]


def test_page_limit_still_applies_to_sparse_pages():
    """짧은 쪽만 있으면 글자 상한에 안 걸리므로 쪽 수 상한이 이긴다."""
    chunks = curriculum.build_chunks(["짧음"] * 25, pages_per_chunk=10)

    assert [(s, e) for s, e, _ in chunks] == [(1, 10), (11, 20), (21, 25)]


def test_single_oversized_page_goes_alone():
    """한 쪽이 혼자 상한을 넘으면 더 나눌 수단이 없다. 그 쪽만 보낸다."""
    chunks = curriculum.build_chunks(["가" * 9000, "나" * 100])

    assert [(s, e) for s, e, _ in chunks] == [(1, 1), (2, 2)]
