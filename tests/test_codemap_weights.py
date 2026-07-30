""" app/engines/codemap/weights.py -- JSON 파싱과 실패시 기본값 폴백 테스트 """
from app.engines.codemap.weights import DEFAULT_WEIGHTS, parse_weights


def test_none_returns_default():
    assert parse_weights(None) == DEFAULT_WEIGHTS


def test_empty_string_returns_default():
    assert parse_weights("") == DEFAULT_WEIGHTS


def test_malformed_json_returns_default():
    assert parse_weights("{not valid json") == DEFAULT_WEIGHTS


def test_valid_json_overrides_defaults():
    w = parse_weights('{"weights": {"fan_in": 2.0, "entry_point": 0.5, "path_depth": 1, "size": 1, "own_commit": 3}, "provenance": "test"}')
    assert w.fan_in == 2.0
    assert w.entry_point == 0.5
    assert w.own_commit == 3
    assert w.provenance == "test"


def test_partial_json_fills_missing_from_default():
    w = parse_weights('{"weights": {"fan_in": 5.0}}')
    assert w.fan_in == 5.0
    assert w.entry_point == DEFAULT_WEIGHTS.entry_point


def test_committed_weights_file_parses_cleanly():
    """ 실제로 커밋된 codemap_weights.json이 깨지지 않았는지 """
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app/engines/codemap/weights/codemap_weights.json"
    w = parse_weights(path.read_text(encoding="utf-8"))
    assert w.fan_in == 1.0
    assert "provisional" in w.provenance
