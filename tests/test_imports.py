""" import 그래프 — references[].CALLER 의 재료.

**중요도 점수가 아니다.** fan_in 이 높은 파일은 공용 모듈이고 공용 모듈은 판단이 빠져
있다(PM 설계 v2 §7-1). 여기서는 "누가 이 파일을 부르는가"만 쓴다.
"""
from app.engines.analysis import imports


def test_python_importers_are_found():
    files = {
        "app/auth.py": "def issue():\n    pass\n",
        "app/api.py": "from app.auth import issue\n",
        "app/cli.py": "import auth\n",
    }

    assert imports.build(files)["app/auth.py"] == ["app/api.py", "app/cli.py"]


def test_java_import_inside_a_comment_is_ignored():
    """주석 안의 import 가 잡히면 없는 의존이 생긴다."""
    files = {
        "Member.java": "class Member {}\n",
        "Library.java": "// import com.x.Member;\nclass Library {}\n",
        "Main.java": "import com.x.Member;\nclass Main {}\n",
    }

    assert imports.build(files)["Member.java"] == ["Main.java"]


def test_a_file_is_never_its_own_importer():
    """자기가 자기를 부르는 것으로 잡히면 화면에 자기 코드가 근거로 뜬다."""
    files = {"app/auth.py": "import auth\n"}

    assert imports.build(files) == {}


def test_importers_are_capped():
    """공용 모듈은 수십 개가 잡힌다. 다 보여주면 학생이 읽을 코드가 폭발한다."""
    files = {"util.py": "x = 1\n"}
    files.update({f"m{i}.py": "import util\n" for i in range(10)})

    assert len(imports.build(files, limit=3)["util.py"]) == 3
