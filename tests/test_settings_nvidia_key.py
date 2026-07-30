""" app/config.py -- NVIDIA_API_KEY 관련 기동 검증(D9) + app.engines.shared.secrets 테스트

internal_api_key와 완전히 같은 원칙: engine_mode=codemap인 production이 키 없이
조용히 반쪽만 뜨는 것을 기동 시점에 막는다.
"""
import pytest

from app.config import Settings, get_settings
from app.engines.shared.secrets import nvidia_api_key


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_local_mode_allows_empty_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ENGINE_MODE", "codemap")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    settings = get_settings()  # 예외 없이 떠야 한다
    assert settings.nvidia_api_key == ""


def test_production_stub_mode_allows_empty_key(monkeypatch):
    """ codemap 모드가 아니면 실제로 LLM을 안 부르므로 키가 없어도 기동해야 한다 """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENGINE_MODE", "stub")
    monkeypatch.setenv("INTERNAL_API_KEY", "some-key")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    get_settings()  # 예외 없어야 함


def test_production_codemap_mode_without_key_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENGINE_MODE", "codemap")
    monkeypatch.setenv("INTERNAL_API_KEY", "some-key")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        get_settings()


def test_production_codemap_mode_with_key_boots(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENGINE_MODE", "codemap")
    monkeypatch.setenv("INTERNAL_API_KEY", "some-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-real-key")
    get_settings()  # 예외 없어야 함


def test_nvidia_api_key_accessor_raises_when_empty(monkeypatch):
    settings = Settings(nvidia_api_key="", internal_api_key="x")
    monkeypatch.setattr("app.engines.shared.secrets.get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        nvidia_api_key()


def test_nvidia_api_key_accessor_returns_value_when_set(monkeypatch):
    settings = Settings(nvidia_api_key="nvapi-abc", internal_api_key="x")
    monkeypatch.setattr("app.engines.shared.secrets.get_settings", lambda: settings)
    assert nvidia_api_key() == "nvapi-abc"
