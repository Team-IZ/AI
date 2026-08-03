""" import 그래프 — "이 파일을 누가 부르는가".

**출처: 팀원 브랜치 `origin/feature/code-importance-map`의 `app/engines/codemap/graph.py`**
(기준 커밋 `f2db763`). 다국어 import 추출 정규식과 그 주석의 실측 근거를 그대로 가져왔다.
우리는 fan-in/fan-out 점수가 필요 없어서 **역방향 색인(누가 나를 import 하나)만** 남겼다.

🔴 **중요도 점수로 쓰지 않는다.** `fan_in`이 높은 파일은 공용 모듈이고, 공용이 되려면
범용적이어야 하고, 범용적이려면 특정 판단이 빠져 있다 — **결정 밀도와 음의 상관**이다
(PM 설계 v2 §7-1). 여기서는 `references[].CALLER`를 채우는 데만 쓴다.

LLM을 부르지 않는다. 정규식과 파일명 매칭뿐이다.
"""
from __future__ import annotations

import re
from collections import defaultdict

JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
C_LIKE_EXTS = (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp")

JS_IMPORT_RE = re.compile(r"import\s+.*?from\s+['\"](.+?)['\"]")
PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w\.]+)\s*;", re.MULTILINE)
C_INCLUDE_RE = re.compile(r'#include\s*"([^"]+)"')

# '@/' alias import도 로컬 import로 인식한다(팀원 D18).
LOCAL_IMPORT_PREFIXES = (".", "@/")

_JAVA_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_JAVA_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JAVA_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _strip_java_noise(text: str) -> str:
    """주석·문자열 안의 `import`가 잡히면 없는 의존이 생긴다."""
    text = _JAVA_BLOCK_COMMENT_RE.sub(" ", text)
    text = _JAVA_LINE_COMMENT_RE.sub(" ", text)
    return _JAVA_STRING_RE.sub(" ", text)


def _targets(path: str, text: str) -> list[str]:
    """이 파일이 import 하는 대상의 **마지막 이름 조각**들."""
    if path.endswith(JS_EXTS):
        return [m.group(1).rsplit("/", 1)[-1]
                for m in JS_IMPORT_RE.finditer(text)
                if m.group(1).startswith(LOCAL_IMPORT_PREFIXES)]
    if path.endswith(".py"):
        out = []
        for m in PY_IMPORT_RE.finditer(text):
            mod = m.group(1) or m.group(2)
            if mod:
                out.append(mod.split(".")[-1])
        return out
    if path.endswith(".java"):
        return [m.group(1).split(".")[-1]
                for m in JAVA_IMPORT_RE.finditer(_strip_java_noise(text))]
    if path.endswith(C_LIKE_EXTS):
        return [m.group(1).rsplit("/", 1)[-1].rsplit(".", 1)[0]
                for m in C_INCLUDE_RE.finditer(text)]
    return []


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def importers_by_file(files: dict[str, str]) -> dict[str, list[str]]:
    """`{파일 경로: 그 파일을 import 하는 파일들}`.

    **파일명(확장자 뺀 것)으로 맞춘다.** 정규식 기반이라 심볼 테이블이 없고, 모듈 경로를
    실제 파일로 정확히 해석할 수단이 없다. 같은 이름의 파일이 여러 폴더에 있으면 양쪽 다
    잡히는데, **틀린 방향으로 안전하다** — 근거를 하나 더 보여줄 뿐 없는 것을 만들지 않는다.

    자기 자신은 제외한다(한 파일이 자기를 import 하는 것으로 잡히면 화면에 자기가 뜬다).
    """
    stem_to_paths: dict[str, list[str]] = defaultdict(list)
    for path in files:
        stem_to_paths[_stem(path)].append(path)

    result: dict[str, list[str]] = defaultdict(list)
    for path, text in files.items():
        for target in set(_targets(path, text)):
            for hit in stem_to_paths.get(target, []):
                if hit != path and path not in result[hit]:
                    result[hit].append(path)

    return {k: sorted(v) for k, v in result.items()}


def cap(importers: dict[str, list[str]], limit: int = 3) -> dict[str, list[str]]:
    """근거를 너무 많이 붙이지 않는다.

    공용 모듈은 수십 개가 잡히는데, 그걸 다 보여주면 학생이 읽어야 할 코드가 폭발한다.
    화면에 놓을 수 있는 만큼만 남긴다.
    """
    return {k: v[:limit] for k, v in importers.items()}


def build(files: dict[str, str], limit: int = 3) -> dict[str, list[str]]:
    return cap(importers_by_file(files), limit)
