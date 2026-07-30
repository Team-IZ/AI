""" tools/lint_llm_calls.py 단위 테스트 (D3-1 PROMPT001 / D4 LLM001)

_Visitor를 임시 파일에 실제로 파싱시켜 검증한다 -- 문자열 매칭이 아니라
진짜 ast.parse를 거치는 게 핵심(그래야 "AST 기반"이라는 주장이 테스트로도 증명됨).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from lint_llm_calls import ALLOWLIST_LLM001, lint_file  # noqa: E402


def _lint_source(tmp_path: Path, relpath: str, source: str):
    full = tmp_path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source, encoding="utf-8")
    return lint_file(full, tmp_path)


def test_flags_raw_requests_post(tmp_path):
    findings, _ = _lint_source(
        tmp_path, "app/engines/codemap/bad.py",
        "import requests\ndef f():\n    return requests.post('http://x', json={})\n",
    )
    rules = {f.rule for f in findings}
    assert "LLM001" in rules


def test_flags_openai_sdk_create(tmp_path):
    findings, _ = _lint_source(
        tmp_path, "app/engines/codemap/bad2.py",
        "def f(client):\n    return client.chat.completions.create(model='x', messages=[])\n",
    )
    assert any(f.rule == "LLM001" for f in findings)


def test_allows_wrapper_module(tmp_path):
    """ D4 허용목록에 실제로 등록된 경로 하나 (app/engines/shared/llm.py)만 예외 """
    assert "app/engines/shared/llm.py" in ALLOWLIST_LLM001
    findings, _ = _lint_source(
        tmp_path, "app/engines/shared/llm.py",
        "import httpx\ndef chat():\n    return httpx.post('http://x')\n",
    )
    assert findings == []


def test_flags_inline_prompt_literal(tmp_path):
    source = (
        "def call(llm):\n"
        "    return llm.chat(messages=[{'role': 'user', "
        "'content': 'You are a helpful assistant that reviews code carefully.'}])\n"
    )
    findings, _ = _lint_source(tmp_path, "app/engines/codemap/bad3.py", source)
    assert any(f.rule == "PROMPT001" for f in findings)


def test_flags_inline_prompt_via_string_concatenation(tmp_path):
    """ 암묵적 문자열 이어붙이기로 우회해도 AST는 여전히 하나의 Constant로 본다 """
    source = (
        "def call(llm):\n"
        "    return llm.chat(messages=[{'role': 'system', "
        "'content': 'You are a careful reviewer ' 'who reads every line of code.'}])\n"
    )
    findings, _ = _lint_source(tmp_path, "app/engines/codemap/bad4.py", source)
    assert any(f.rule == "PROMPT001" for f in findings)


def test_allows_role_field_short_literal(tmp_path):
    """ role: "system"/"user" 같은 짧은 필드 값은 프롬프트로 오인하지 않는다 """
    source = (
        "def call(llm, rendered):\n"
        "    return llm.chat(messages=[{'role': 'system', 'content': rendered}])\n"
    )
    findings, _ = _lint_source(tmp_path, "app/engines/codemap/ok1.py", source)
    assert findings == []


def test_allows_loaded_prompt_reference(tmp_path):
    source = (
        "from app.engines.shared.prompts import load_stage, render\n"
        "def call(llm):\n"
        "    stage = load_stage('p05', 'p05-1')\n"
        "    return llm.chat(messages=render(stage, {}))\n"
    )
    findings, _ = _lint_source(tmp_path, "app/engines/codemap/ok2.py", source)
    assert findings == []


def test_flags_file_read_at_call_site(tmp_path):
    source = (
        "def call(llm):\n"
        "    return llm.chat(system=open('secret_prompt.txt').read())\n"
    )
    findings, _ = _lint_source(tmp_path, "app/engines/codemap/bad5.py", source)
    assert any(f.rule == "PROMPT001" for f in findings)


def test_noqa_suppresses_finding(tmp_path):
    source = (
        "import requests  # noqa: LLM001\n"
        "def f():\n"
        "    return requests.post('http://x')  # noqa: LLM001\n"
    )
    findings, suppressed = _lint_source(tmp_path, "app/engines/codemap/bad6.py", source)
    assert findings == []
    assert suppressed == 2


def test_allowlist_entries_have_reasons():
    assert all(isinstance(reason, str) and reason.strip() for reason in ALLOWLIST_LLM001.values())


def test_app_tree_is_clean():
    """ 실제 app/ 트리 전체를 대상으로 CI가 돌리는 것과 동일한 검사를 재현한다 """
    from lint_llm_calls import lint_tree

    repo_root = Path(__file__).resolve().parents[1]
    findings, suppressed = lint_tree(repo_root / "app")
    assert findings == []
    assert suppressed == 0
