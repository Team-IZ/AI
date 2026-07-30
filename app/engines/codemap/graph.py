""" import 그래프 구성 -- 순수 함수만. 파일시스템/네트워크 없음

cognition/two_tier_scan.py(origin/feat/poc_full)의 다국어 import 추출 로직을
그대로 가져온다 -- 특히 D12(같은 모듈을 여러 줄로 나눠 import해도 fan-in이
한 번만 올라가도록 (src,dst)를 set으로 dedupe한 뒤 집계) 버그 수정을 재도입하지
않도록 그 순서(엣지 set 구성 -> 집계)를 그대로 지킨다.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from app.engines.codemap.models import ImportGraph, RepoFile

JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
C_LIKE_EXTS = (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp")

JS_IMPORT_RE = re.compile(r"import\s+.*?from\s+['\"](.+?)['\"]")
PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+))", re.MULTILINE)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w\.]+)\s*;", re.MULTILINE)
C_INCLUDE_RE = re.compile(r'#include\s*"([^"]+)"')

# D18(원본): '@/' alias import도 로컬 import로 인식
LOCAL_IMPORT_PREFIXES = (".", "@/")

_JAVA_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_JAVA_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JAVA_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _strip_java_comments_and_strings(text: str) -> str:
    text = _JAVA_BLOCK_COMMENT_RE.sub(" ", text)
    text = _JAVA_LINE_COMMENT_RE.sub(" ", text)
    text = _JAVA_STRING_RE.sub(" ", text)
    return text


def _extract_js_targets(text: str) -> list[str]:
    return [m.group(1) for m in JS_IMPORT_RE.finditer(text) if m.group(1).startswith(LOCAL_IMPORT_PREFIXES)]


def _extract_py_targets(text: str) -> list[str]:
    targets = []
    for m in PY_IMPORT_RE.finditer(text):
        mod = m.group(1) or m.group(2)
        if mod:
            targets.append(mod.split(".")[-1])
    return targets


def _extract_java_targets(text: str) -> list[str]:
    return [m.group(1).split(".")[-1] for m in JAVA_IMPORT_RE.finditer(text)]


def _extract_java_same_package_targets(
    text: str, sibling_class_stems: Sequence[str], own_stem: str
) -> list[str]:
    """ import 없이 참조되는 형제 클래스(default package) 탐지 -- D122/D-fix16 이식.

    호출 전에 주석/문자열을 이미 걷어낸 text를 받는다고 가정한다(build_import_graph에서 처리).
    """
    targets = []
    for stem in sibling_class_stems:
        if stem == own_stem:
            continue
        if re.search(rf"\b{re.escape(stem)}\b", text):
            targets.append(stem)
    return targets


def _extract_c_targets(text: str) -> list[str]:
    return [m.group(1) for m in C_INCLUDE_RE.finditer(text)]


def _extract_targets(f: RepoFile) -> list[str]:
    if f.ext in JS_EXTS:
        return _extract_js_targets(f.text)
    if f.ext == ".py":
        return _extract_py_targets(f.text)
    if f.ext == ".java":
        return _extract_java_targets(f.text)
    if f.ext in C_LIKE_EXTS:
        return _extract_c_targets(f.text)
    return []  # Swift 등: 모듈 단위 가시성이라 파일간 로컬 import가 원천적으로 없음(문서화된 한계)


def _splitext(basename: str) -> tuple[str, str]:
    """ os.path.splitext와 동등한 문자열 전용 구현 -- 순수 모듈은 os를 import하지 않는다(D2).

    rfind(".")가 0 이하(점이 없거나, ".gitignore"처럼 선두 점 하나뿐)면 확장자 없음으로 본다 --
    os.path.splitext(".gitignore") == (".gitignore", "")와 동일한 동작.
    """
    dot = basename.rfind(".")
    if dot <= 0:
        return basename, ""
    return basename[:dot], basename[dot:]


def _resolve_matches(target: str, lookup: Mapping[str, list[str]]) -> list[str]:
    """ target에 확장자가 있으면(C의 #include "utils.h") 파일명 정확매치,
    없으면(JS/Py/Java) stem매치. lookup은 {basename_or_stem: [실제 path, ...]}. """
    target_base = target.rsplit("/", 1)[-1]
    stem, ext = _splitext(target_base)
    key = target_base if ext else stem
    return lookup.get(key, [])


def build_import_graph(files: Sequence[RepoFile]) -> ImportGraph:
    """ 파일 목록 -> (src,dst) 간선 dedupe 집합 -> in_degree/out_degree 집계 (순수 함수) """
    stem_to_paths: dict[str, list[str]] = {}
    basename_to_paths: dict[str, list[str]] = {}
    java_stems_by_dir: dict[str, list[tuple[str, str]]] = {}  # dir -> [(stem, path), ...]

    for f in files:
        basename = f.path.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        stem_to_paths.setdefault(stem, []).append(f.path)
        basename_to_paths.setdefault(basename, []).append(f.path)
        if f.ext == ".java":
            dirname = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            java_stems_by_dir.setdefault(dirname, []).append((stem, f.path))

    # target(확장자 있으면 basename, 없으면 stem)으로 조회할 통합 맵
    lookup: dict[str, list[str]] = {**basename_to_paths}
    for stem, paths in stem_to_paths.items():
        lookup.setdefault(stem, paths)

    edge_set: set[tuple[str, str]] = set()
    for f in files:
        for target in _extract_targets(f):
            for dst in _resolve_matches(target, lookup):
                if dst != f.path:
                    edge_set.add((f.path, dst))

        if f.ext == ".java":
            dirname = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            own_stem = f.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            siblings = java_stems_by_dir.get(dirname, [])
            cleaned = _strip_java_comments_and_strings(f.text)
            for target in _extract_java_same_package_targets(cleaned, [s for s, _ in siblings], own_stem):
                for dst in _resolve_matches(target, lookup):
                    if dst != f.path:
                        edge_set.add((f.path, dst))

    in_degree: dict[str, int] = {f.path: 0 for f in files}
    out_degree: dict[str, int] = {f.path: 0 for f in files}
    for src, dst in edge_set:
        out_degree[src] = out_degree.get(src, 0) + 1
        in_degree[dst] = in_degree.get(dst, 0) + 1

    return ImportGraph(edges=tuple(sorted(edge_set)), in_degree=in_degree, out_degree=out_degree)
