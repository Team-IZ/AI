""" app/engines/codemap/graph.py -- import 그래프 구성 테스트

D12 회귀 테스트가 핵심: 같은 모듈을 여러 줄로 나눠 import해도 fan-in이 한 번만
올라가야 한다(이중계산 버그 재발 방지).
"""
from app.engines.codemap.graph import build_import_graph
from app.engines.codemap.models import RepoFile


def _f(path, text):
    ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    return RepoFile(path=path, ext=ext, size_bytes=len(text), line_count=text.count("\n") + 1, text=text)


def test_js_relative_and_alias_imports():
    files = [
        _f("src/routes.tsx", "import { helper } from './helper';\nimport { z } from '@/lib/z';\n"),
        _f("src/helper.ts", "export const helper = 1;\n"),
        _f("lib/z.ts", "export const z = 1;\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["src/helper.ts"] == 1
    assert graph.in_degree["lib/z.ts"] == 1
    assert ("src/routes.tsx", "src/helper.ts") in graph.edges
    assert ("src/routes.tsx", "lib/z.ts") in graph.edges


def test_fan_in_dedups_repeated_edges():
    """ D12: firebase.ts를 두 줄로 나눠 import해도 fan_in은 1만 올라간다 """
    files = [
        _f("src/a.ts", "import { x } from './firebase';\nimport { y } from './firebase';\n"),
        _f("src/firebase.ts", "export const x = 1; export const y = 2;\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["src/firebase.ts"] == 1
    assert len(graph.edges) == 1


def test_python_from_imports():
    files = [
        _f("app/main.py", "from app.jobs import run_analysis\nimport app.config\n"),
        _f("app/jobs.py", "x = 1\n"),
        _f("app/config.py", "y = 1\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["app/jobs.py"] == 1
    assert graph.in_degree["app/config.py"] == 1


def test_java_default_package_siblings():
    """ package 선언 없는 Java 과제 -- import문 없이 형제 클래스를 참조 """
    files = [
        _f("src/Main.java", "public class Main {\n    Student s = new Student();\n}\n"),
        _f("src/Student.java", "public class Student {\n}\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["src/Student.java"] == 1


def test_java_default_package_ignores_comments_and_strings():
    """ D-fix16: 주석/문자열 안에 우연히 등장하는 형제 클래스명은 매치하지 않는다 """
    files = [
        _f("src/Main.java", '// Student is unrelated here\npublic class Main {\n    String s = "Student";\n}\n'),
        _f("src/Student.java", "public class Student {\n}\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["src/Student.java"] == 0


def test_c_include_local_only():
    files = [
        _f("src/main.c", '#include "utils.h"\n#include <stdio.h>\n'),
        _f("src/utils.h", "void f();\n"),
    ]
    graph = build_import_graph(files)
    assert graph.in_degree["src/utils.h"] == 1


def test_isolated_file_has_zero_in_and_out_degree():
    files = [_f("src/orphan.py", "x = 1\n")]
    graph = build_import_graph(files)
    assert graph.in_degree["src/orphan.py"] == 0
    assert graph.out_degree["src/orphan.py"] == 0
