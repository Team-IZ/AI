""" 테스트 스위트 전역 fixture. """
import pytest

import app.jobs as jobs_mod


@pytest.fixture(autouse=True)
def _isolate_pr3_measurements(tmp_path, monkeypatch):
    """ D-pr3 안전장치: run_analysis()를 호출하는 그 어떤 테스트도 실제
    docs/code-importance-map/measurements/*.jsonl을 건드리면 안 된다 -- 그 파일은
    PR-3의 실제 운영 세션 실측용이라 테스트 실행(로컬/CI 무관하게 매번)마다
    가짜 job 기록이 섞여 들어가면 데이터가 오염된다(2026-07-31 발견: 전체 스위트
    실행 한 번으로 무관한 테스트들이 실제 파일에 24건을 남겼다). autouse라
    개별 테스트가 매번 챙길 필요 없이 전부 자동으로 격리된다. """
    monkeypatch.setattr(jobs_mod, "_MEASUREMENTS_DIR", tmp_path / "measurements")
