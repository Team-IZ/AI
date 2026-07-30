""" tools/check_no_secrets.py 단위 테스트 (D9)

git 명령을 거치지 않고 check(relpaths, repo_root) 핵심 로직만 직접 검증한다 --
git add/commit을 매 테스트마다 하는 것보다 빠르고, "이 함수가 뭘 판단하는지"에
집중할 수 있다. CLI 진입점(--staged/--tracked)은 git 호출 한 줄뿐이라 별도
테스트로 얻는 게 적다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_no_secrets import check  # noqa: E402


def _write(root: Path, relpath: str, content: str) -> None:
    full = root / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def test_detects_nvapi_literal(tmp_path):
    _write(tmp_path, "app/config.py", 'FALLBACK = "nvapi-abcdefghijklmnopqrstuvwxyz012345"\n')
    findings = check(["app/config.py"], tmp_path)
    assert any("NVAPI_KEY_LITERAL" in f for f in findings)


def test_rejects_keypool_env_names(tmp_path):
    _write(tmp_path, "notes.txt", "NVIDIA_API_KEY_3=nvapi-abcdefghijklmnopqrstuvwxyz\n")
    findings = check(["notes.txt"], tmp_path)
    assert any("NVIDIA_KEYPOOL_NAMING" in f for f in findings)


def test_rejects_nvidia_api_key_assignment_with_real_value(tmp_path):
    _write(tmp_path, "notes.txt", "NVIDIA_API_KEY=some-real-looking-secret-value\n")
    findings = check(["notes.txt"], tmp_path)
    assert any("NVIDIA_API_KEY_ASSIGNMENT" in f for f in findings)


def test_allows_empty_env_example(tmp_path):
    _write(tmp_path, ".env.example", "APP_ENV=local\nNVIDIA_API_KEY=\n")
    findings = check([".env.example"], tmp_path)
    assert findings == []


def test_env_example_with_real_value_is_rejected(tmp_path):
    _write(tmp_path, ".env.example", "NVIDIA_API_KEY=nvapi-abcdefghijklmnopqrstuvwxyz\n")
    findings = check([".env.example"], tmp_path)
    assert any("ENV_EXAMPLE_HAS_REAL_VALUE" in f for f in findings)


def test_rejects_env_filename_regardless_of_content(tmp_path):
    _write(tmp_path, ".env", "APP_ENV=local\n")
    findings = check([".env"], tmp_path)
    assert any("BANNED_FILENAME" in f for f in findings)


def test_rejects_keypool_script_filename(tmp_path):
    _write(tmp_path, "scripts/nvidia_key_pool.py", "# harmless content\n")
    findings = check(["scripts/nvidia_key_pool.py"], tmp_path)
    assert any("BANNED_FILENAME" in f for f in findings)


def test_rejects_scripts_local_directory(tmp_path):
    _write(tmp_path, "scripts/local/run.py", "print('hi')\n")
    findings = check(["scripts/local/run.py"], tmp_path)
    assert any("BANNED_FILENAME" in f for f in findings)


def test_clean_file_passes(tmp_path):
    _write(tmp_path, "app/engines/shared/evidence.py", "def f():\n    return 1\n")
    findings = check(["app/engines/shared/evidence.py"], tmp_path)
    assert findings == []
