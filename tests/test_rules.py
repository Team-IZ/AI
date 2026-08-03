""" 룰 기반 후보 선별(T6). vendor/ 원본을 감싼 rules.py만 검증한다. """
import io
import zipfile

import pytest

from app.engines.analysis import rules


def _zip(files: dict[str, str]) -> bytes:
    """{경로: 내용} 을 ZIP 바이트로."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()


# 같은 이름의 정의가 2개 파일에 있으면 duplicate-definition 후보가 뜬다.
# (score_findings.REPEATED_PATTERN_MIN_FILES = 2)
_DUP = {
    "repo-main/billing.py": "def process_payment(order):\n    return order\n",
    "repo-main/checkout.py": "def process_payment(order):\n    return order\n",
}


def test_finds_candidates_from_zip():
    """ 룰 기반 후보 선별(T6). vendor/ 원본을 감싼 rules.py만 검증한다. """
    import io
    import zipfile

    import pytest

    from app.engines.analysis import rules


def _zip(files: dict[str, str]) -> bytes:
    """{경로: 내용} 을 ZIP 바이트로."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()
""" 룰 기반 후보 선별(T6). vendor/ 원본을 감싼 rules.py만 검증한다. """
import io
import zipfile

import pytest

from app.engines.analysis import rules


def _zip(files: dict[str, str]) -> bytes:
    """{경로: 내용} 을 ZIP 바이트로."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()


# 같은 이름의 정의가 2개 파일에 있으면 duplicate-definition 후보가 뜬다.
# (score_findings.REPEATED_PATTERN_MIN_FILES = 2)
_DUP = {
    "repo-main/billing.py": "def process_payment(order):\n    return order\n",
    "repo-main/checkout.py": "def process_payment(order):\n    return order\n",
}


def test_finds_candidates_from_zip():
    """ZIP → 후보 목록. 룰이 실제로 돌아 결과 모양이 나온다."""
    out = rules.find_candidates(_zip(_DUP))

    assert out["file_count"] == 2
    # DB assessment_problem.extractor_version이 INTEGER CHECK (> 0)다.
    # 문자열로 보내던 시절이 있었고 그대로 나가면 Spring INSERT가 깨진다.
    assert isinstance(out["extractor_version"], int)
    assert 0 < out["extractor_version"] <= 2_147_483_647
    assert any(c["finding_id"].startswith("repeated-pattern:") for c in out["candidates"])


def test_candidate_has_our_vocabulary():
    """PoC finding이 우리 어휘로 옮겨졌는지 — problemType은 5종, priority는 float."""
    out = rules.find_candidates(_zip(_DUP))
    c = out["candidates"][0]

    assert c["problem_type"] in {
        "DESIGN_CHOICE", "RISK_POINT", "COMPLEXITY_HOTSPOT",
        "REQUIREMENT_IMPL", "EXTERNAL_INTEGRATION",
    }
    assert isinstance(c["priority"], float)
    assert c["selection_evidence"]["subrubric"]  # 선정 근거가 비어 있지 않다


def test_top_level_folder_is_stripped():
    """GitHub ZIP의 감싸는 폴더를 벗겨야 경로가 학생이 보는 것과 같아진다."""
    out = rules.find_candidates(_zip(_DUP))

    paths = [c["source_path"] for c in out["candidates"] if c["source_path"]]
    assert not any(p.startswith("repo-main") for p in paths)


def test_source_dir_at_top_is_not_stripped():
    """`src/` 하나만 있는 ZIP에서 `src/`를 벗기면 백엔드가 파일을 못 찾는다.

    학생이 프로젝트 폴더 안에서 압축하면 이 모양이 나온다. 벗기면
    `src/main/java/A.java`가 `main/java/A.java`로 응답되는데 에러가 안 난다.
    """
    out = rules.find_candidates(_zip({
        "src/billing.py": "def process_payment(order):\n    return order\n",
        "src/checkout.py": "def process_payment(order):\n    return order\n",
    }))

    assert all(p.startswith("src/") for p in out["files"])


def test_zip_slip_is_blocked():
    """`../` 가 섞인 항목은 거부한다 — 통과하면 서버 파일을 덮어쓴다."""
    with pytest.raises(ValueError, match="벗어납니다"):
        rules.find_candidates(_zip({"../evil.txt": "pwned"}))


def test_extractor_version_is_stable():
    """같은 vendor면 같은 버전. 매번 달라지면 재현성 근거로 못 쓴다."""
    assert rules.extractor_version() == rules.extractor_version()