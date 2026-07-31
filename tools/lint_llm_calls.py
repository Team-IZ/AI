#!/usr/bin/env python3
""" AST 기반 LLM 호출 린터 -- 두 규칙을 한 패스에서 검사한다

D4 (LLM001): app/engines/shared/llm.py 하나만 실제 HTTP/SDK 원시 호출을 할 수 있다.
  다른 곳에서 requests.post(...)/client.chat.completions.create(...) 같은 우회
  호출이 생기면, shared.llm.chat() 하나로 계측(LlmCallTimer/AiUsage)하려던
  전제가 조용히 깨진다 -- 그 우회 호출은 latency_ms도, ai_usage 기록도 안 남는다.

D3-1 (PROMPT001): 프롬프트는 app/engines/shared/prompts.py::load_stage/render를
  통해서만 LLM 호출부에 도달해야 한다(파일 하나=스테이지 하나 모듈화, D3). 호출
  지점에 프롬프트 문자열을 직접 박아 넣으면(인라인) 그 프롬프트는 YAML/manifest
  추적 밖에 있게 되고, 나중에 "이 프롬프트가 실제로 어디서 쓰이는지" 찾을 수 없다.

왜 grep이 아니라 AST인가: 정규식은 문자열 리터럴의 겉모습(긴 문자열, 트리플쿼트 등)을
찾지만, 암묵적 문자열 이어붙이기나 join, textwrap.dedent, chr()+연결 같은
변형으로 쉽게 우회된다. AST는 호출 인자의 서브트리 안에 있는
모든 문자열 Constant 노드를 spelling과 무관하게 찾아낸다 -- 이어붙이든 join하든
결국 리터럴 leaf 노드는 트리 안에 그대로 있다. 유일한 진짜 사각지대는 리터럴이
전혀 없이 계산으로만 만든 프롬프트(예: base64 디코드)인데, 이건 open/read 호출
차단 규칙이 별도로 잡는다(파일을 지정 로더 밖에서 직접 읽는 것 자체를 금지).

실행 위치: .githooks/pre-commit(선택), .github/workflows/ci.yml의 lint job(필수),
pytest(tests/test_lint_llm_calls.py::test_app_tree_is_clean) 세 곳 모두 이 스크립트를
공유한다 -- 로직이 하나뿐이라 세 실행 경로가 서로 어긋날 일이 없다.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# --- LLM001: 원시 HTTP/SDK 호출 우회 차단 --------------------------------

ALLOWLIST_LLM001: dict[str, str] = {
    "app/engines/shared/llm.py": "the wrapper itself -- the only module allowed to touch HTTP/SDK primitives",
}

# D1 (2026-07-31, crew.py 모듈 docstring): crewai를 제거하면서 app/engines/codemap/crew.py의
# ALLOWLIST_LLM001 예외도 같이 없앴다 -- 이제 그 파일도 shared.llm.chat()만 거친다
# (analysis_doc.py와 동일). "crewai"는 BANNED_IMPORT_MODULES에 그대로 남겨둔다: 나중에
# 누군가 실수로 다시 import하면 이 린터가 즉시 잡아야 한다(회귀 방지).

BANNED_IMPORT_MODULES = {
    "httpx", "requests", "openai", "litellm", "crewai",
    "urllib.request", "http.client",
}

BANNED_CALL_SUFFIXES = (
    "requests.post", "requests.request", "requests.get",
    "httpx.post", "httpx.stream", "httpx.get",
    "Client.post", "AsyncClient.post",
    "chat.completions.create", "completions.create",
    "litellm.completion", "litellm.acompletion",
)

BANNED_CONSTRUCTOR_NAMES = {"OpenAI", "AsyncOpenAI", "LLM"}

PROMPT_CALLEE_SUFFIXES = (".chat", ".kickoff")
PROMPT_CALLEE_BARE_NAMES = {"chat", "kickoff"}
PROMPT_ARG_KEYWORDS = {"messages", "system", "user", "prompt", "inputs"}
ALLOWED_PROMPT_PRODUCERS = {"load_stage", "render", "render_stage", "build_messages"}
MIN_INLINE_PROMPT_LEN = 40

SELF_EXCLUDE_BASENAMES = {"lint_llm_calls.py"}


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.rule}  {self.message}"


def _dotted(node) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, relpath: str, source_lines: list[str]):
        self.relpath = relpath
        self.source_lines = source_lines
        self.findings: list[Finding] = []
        self.suppressed = 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in BANNED_IMPORT_MODULES:
                self._flag_llm001(node, f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in BANNED_IMPORT_MODULES:
            self._flag_llm001(node, f"from {node.module} import ...")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        dotted = _dotted(node.func)
        bare_name = dotted.rsplit(".", 1)[-1] if dotted else None

        if dotted and any(dotted == suf or dotted.endswith("." + suf) for suf in BANNED_CALL_SUFFIXES):
            self._flag_llm001(node, f"call {dotted}(...)")
        if bare_name in BANNED_CONSTRUCTOR_NAMES:
            self._flag_llm001(node, f"construct {bare_name}(...)")

        if dotted and (
            dotted in PROMPT_CALLEE_BARE_NAMES
            or any(dotted.endswith(suf) for suf in PROMPT_CALLEE_SUFFIXES)
        ):
            self._check_prompt_call(node)

        self.generic_visit(node)

    def _flag_llm001(self, node: ast.AST, detail: str) -> None:
        if self.relpath in ALLOWLIST_LLM001:
            return
        if self._has_noqa(node.lineno, "LLM001"):
            self.suppressed += 1
            return
        self.findings.append(Finding(self.relpath, node.lineno, "LLM001", detail))

    def _check_prompt_call(self, node: ast.Call) -> None:
        args_to_check: list = list(node.args[:1])
        for kw in node.keywords:
            if kw.arg in PROMPT_ARG_KEYWORDS:
                args_to_check.append(kw.value)

        for arg in args_to_check:
            if self._is_accepted_producer(arg):
                continue
            violation = self._scan_for_inline_literal(arg)
            if violation:
                if self._has_noqa(node.lineno, "PROMPT001"):
                    self.suppressed += 1
                    return
                self.findings.append(Finding(self.relpath, node.lineno, "PROMPT001", violation))
                return

    def _is_accepted_producer(self, node) -> bool:
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return True
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func) or ""
            bare = dotted.rsplit(".", 1)[-1]
            if bare in ALLOWED_PROMPT_PRODUCERS:
                return True
            if dotted.endswith(".format"):
                base = node.func.value if isinstance(node.func, ast.Attribute) else None
                if base is not None and (
                    isinstance(base, (ast.Name, ast.Attribute, ast.Subscript))
                    or self._is_accepted_producer(base)
                ):
                    return True
        if isinstance(node, ast.JoinedStr):
            return all(
                not isinstance(v, ast.FormattedValue)
                or isinstance(v.value, (ast.Name, ast.Attribute, ast.Subscript))
                or self._is_accepted_producer(v.value)
                for v in node.values
            )
        return False

    def _scan_for_inline_literal(self, node, *, suppressed: bool = False):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    found = self._scan_for_inline_literal(value, suppressed=suppressed)
                    if found:
                        return found
                    continue
                key_name = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None
                child_suppressed = suppressed or (key_name is not None and key_name != "content")
                found = self._scan_for_inline_literal(value, suppressed=child_suppressed)
                if found:
                    return found
            return None

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not suppressed and (len(node.value) >= MIN_INLINE_PROMPT_LEN or "\n" in node.value):
                return f"inline literal string ({len(node.value)} chars) at an LLM call argument"
            return None

        if isinstance(node, ast.Call):
            callee = _dotted(node.func) or ""
            if callee == "open" or callee.endswith(".read_text") or callee.endswith(".read"):
                return f"reads a file directly via {callee}(...) -- use app.engines.shared.prompts instead"

        for child in ast.iter_child_nodes(node):
            found = self._scan_for_inline_literal(child, suppressed=suppressed)
            if found:
                return found
        return None

    def _has_noqa(self, lineno: int, rule: str) -> bool:
        if 1 <= lineno <= len(self.source_lines):
            line = self.source_lines[lineno - 1]
            return f"noqa: {rule}" in line or ("noqa" in line and rule in line)
        return False


def lint_file(path: Path, root: Path):
    relpath = str(path.relative_to(root))
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = _Visitor(relpath, source.splitlines())
    visitor.visit(tree)
    return visitor.findings, visitor.suppressed


def lint_tree(root: Path):
    all_findings = []
    total_suppressed = 0
    for path in sorted(root.rglob("*.py")):
        if path.name in SELF_EXCLUDE_BASENAMES:
            continue
        if "__pycache__" in path.parts:
            continue
        findings, suppressed = lint_file(path, root.parent if root.name == "app" else root)
        all_findings.extend(findings)
        total_suppressed += suppressed
    return all_findings, total_suppressed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="검사할 디렉토리 (보통 'app')")
    args = parser.parse_args(argv)

    target = Path(args.target).resolve()

    findings, suppressed = lint_tree(target)
    for f in findings:
        print(f)
    print(f"suppressed: {suppressed}")
    if findings:
        print(f"FAIL: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("OK: no LLM001/PROMPT001 violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
