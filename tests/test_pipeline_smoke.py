"""Phase 1 스모크 테스트 (PLAN §3 Phase 1-4).

목표: Pyodide 없이 서버 CPython에서 내재화된 파이프라인
(two_tier_scan.scan → score_findings.score)이 pipeline_runner를 통해
에러 없이 완주하고, 결과 JSON에 scan/judgment 구조가 존재하는지 검증.
finding 내용의 정확성 검증은 목표가 아니다.
"""
import json

import pytest

from app.core import pipeline_runner


@pytest.fixture
def sample_repo(tmp_path):
    """작은 샘플 코드 트리: Python 허브 구조 + Tier-B 트리거 + JS 파일."""
    (tmp_path / "utils.py").write_text(
        "def helper(x):\n    return x * 2\n",
        encoding="utf-8",
    )
    (tmp_path / "core.py").write_text(
        "import utils\n\n\ndef run(v):\n    return utils.helper(v)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "import utils\nimport core\n\n\nif __name__ == '__main__':\n"
        "    print(core.run(21), utils.helper(1))\n",
        encoding="utf-8",
    )
    # Tier-B 위험 트리거: eval + 하드코딩 시크릿 패턴
    (tmp_path / "danger.py").write_text(
        "import utils\n\n"
        'API_KEY = "sk-abcdef1234567890abcdef1234567890"\n\n\n'
        "def unsafe(expr):\n    return eval(expr)\n",
        encoding="utf-8",
    )
    (tmp_path / "web.js").write_text(
        "function show(el, s) {\n  el.innerHTML = s;\n}\n"
        "const token = 'ghp_0123456789abcdef0123456789abcdef';\n",
        encoding="utf-8",
    )
    return tmp_path


def test_run_scan_completes_with_expected_structure(sample_repo):
    result = pipeline_runner.run_scan(str(sample_repo))

    # 최상위 구조
    assert set(result.keys()) == {"scan", "judgment", "overrides_applied"}
    assert result["overrides_applied"] == []

    # scan 구조 (two_tier_scan.scan 반환 스키마)
    scan = result["scan"]
    assert scan["total_source_files"] >= 4  # py 4개 + js 1개 중 SRC_EXTS 대상
    assert "tier_a_structural" in scan
    assert "fan_in" in scan["tier_a_structural"]
    assert "tier_b_risk_triggered" in scan
    assert "flagged_files" in scan["tier_b_risk_triggered"]

    # judgment 구조 (score_findings.score 반환 스키마)
    judgment = result["judgment"]
    assert "findings" in judgment
    assert isinstance(judgment["findings"], list)
    for finding in judgment["findings"]:
        assert "id" in finding
        assert "file" in finding

    # 결과 전체가 JSON 직렬화 가능해야 한다 (API 응답으로 나갈 데이터)
    json.dumps(result, ensure_ascii=False)


def test_apply_overrides_tuple_coercion(sample_repo):
    """webtool_driver에서 이관한 오버라이드 로직: list → tuple 강제 변환."""
    pipeline_runner.setup_pipeline_paths()
    import two_tier_scan

    original = two_tier_scan.SRC_EXTS
    try:
        applied = pipeline_runner.apply_overrides(
            {"two_tier_scan": {"SRC_EXTS": [".py"]}}
        )
        assert applied == ["two_tier_scan.SRC_EXTS"]
        assert two_tier_scan.SRC_EXTS == (".py",)

        result = pipeline_runner.run_scan(str(sample_repo))
        # .py만 스캔하므로 web.js는 집계에서 빠져야 한다
        assert result["scan"]["total_source_files"] == 4
        assert result["overrides_applied"] == []  # run_scan에 overrides 미전달
    finally:
        # 모듈 전역 원복 (다른 테스트 오염 방지)
        two_tier_scan.SRC_EXTS = original


def test_health_endpoint_reports_mode_and_pipeline():
    """앱 팩토리 + /api/health 스모크 (TestClient, 서버 기동 없이)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["mode"] in ("standalone", "integrated")
    assert body["pipeline_loaded"] is True
