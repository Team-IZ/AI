import json
import os
import re

# D5: 관용 패턴 목록을 코드가 아니라 별도 상태 파일(idioms/<lang>/idiom_patterns.json)로 분리
#   WHY: 관용 패턴 목록은 계속 늘어나는 "데이터"이지 "로직"이 아님 —
#        코드와 분리해야 재귀 업데이트(idiom_hook.py)가 score_findings.py 재배포 없이 가능
#   COST: 상태 파일이 stale하면 같은 코드에서도 실행 결과가 달라짐(재현성 저하)
#   EXIT: idiom_patterns.json을 git에 커밋해 버전관리 → 이상 동작 시 git revert로 특정 시점 상태 복원
#
# D7: 관용 패턴 저장소를 언어별로 분리(judgment/idioms/<lang>/)
#   WHY: "관용 패턴"은 언어/프레임워크에 강하게 종속됨 — JS의 Context, Java의 static
#        Singleton, C의 전역 변수는 서로 다른 위험/설계의도 프로파일을 가짐. 하나로 합치면
#        한 언어에서 학습한 관용패턴이 다른 언어의 진짜 위험 신호까지 걸러버릴 수 있음
#   COST: 언어 수만큼 상태 파일/로그가 늘어나 관리 포인트 증가. 언어 판별(확장자 기반)이
#        틀리면(예: .h가 C인지 C++인지 애매) 잘못된 저장소를 참조할 위험
#   EXIT: 새 언어는 LANG_EXT_MAP에 확장자 매핑만 추가하면 됨. 언어 판별을 확장자가 아니라
#        AST/툴체인 기반으로 바꾸고 싶으면 resolve_lang()만 교체
#
# D15: .h 확장자의 C/C++ 모호성을 파일 내용 스니핑으로 개선(repo_root가 주어질 때만)
#   WHY: .h는 C/C++ 양쪽에서 다 쓰여 확장자만으론 확정 불가 — class/namespace/template/std::
#        같은 C++ 전용 토큰이 있으면 cpp로, 없으면 기존처럼 c로 판정하면 오분류가 줄어듦
#   COST: 완벽하지 않음 — C++ 스타일 헤더인데 이 토큰들을 하나도 안 쓰면 여전히 c로 오판정.
#        repo_root 없이 파일명만으론 여전히 기존처럼 c로 고정(하위호환)
#   EXIT: 이 휴리스틱도 부족하면 실제 컴파일러/AST 판별(예: clang -x c++ 파싱 성공 여부)로 교체
#
# D31: idiom_filter가 question_value를 덮어쓸 때 subrubric 감사 트레일도 함께 갱신
#   WHY: 실측 발견 — subrubric.py가 계산한 원점수(예: total=9 → "상")와 idiom_filter가
#        최종적으로 덮어쓴 값("하")이 서로 다른 채로 finding에 함께 남아있었음. 팀의 B안
#        POC 문서가 지적한 "Signals After Filter가 Downgrade Log와 안 맞는다"는 문제와
#        정확히 같은 클래스의 결함 — 감사 트레일(subrubric)과 최종 판정이 불일치하면
#        "왜 이 등급인가"를 설명할 수 없어 Evidence 기반 설계 취지가 무너짐
#   COST: idiom_filter가 subrubric.py의 내부 스키마(sub/total 키)를 알아야 해서 두 모듈
#        간 결합이 약간 늘어남
#   EXIT: subrubric.py가 스키마를 바꾸면 이 덮어쓰기 블록도 같이 바꿔야 함 — 스키마를
#        공용 상수/타입으로 뽑으면 결합도를 낮출 수 있음

CPP_HINT_RE = re.compile(r"\bclass\s+\w+|\bnamespace\s+\w+|\btemplate\s*<|std::|\bpublic:|\bprivate:")

IDIOMS_ROOT = os.path.join(os.path.dirname(__file__), "idioms")

LANG_EXT_MAP = {
    ".ts": "javascript", ".tsx": "javascript", ".js": "javascript", ".jsx": "javascript",
    ".py": "python",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".swift": "swift",
}
SUPPORTED_LANGS = sorted(set(LANG_EXT_MAP.values()))

DOWNGRADE_MAP = {"상": "하", "중": "하", "하": "하"}


def resolve_lang(filename, content=None):
    """content가 주어지고 확장자가 .h면 C++ 전용 토큰 유무로 c/cpp를 재판정한다(D15)."""
    _, ext = os.path.splitext(filename)
    if ext == ".h" and content is not None:
        return "cpp" if CPP_HINT_RE.search(content) else "c"
    return LANG_EXT_MAP.get(ext)


# D195: _find_file_content가 갖고 있던 자체 하드코딩 스킵셋이 score_findings.py/
#   cognition/two_tier_scan.py의 SKIP_DIRS보다 훨씬 짧아(.venv/venv/static/vendor/vendored조차
#   없음) 같은 개념의 세 번째 불일치 지점이었음 — 전체 목록+접두/접미어 매칭으로 정렬.
#   WHY: 스킵셋이 파일마다 다르면 같은 파이프라인 안에서 "score_findings가 무시한 산출물을
#        .h c/cpp 재판정(D15)은 읽어버리는" 비대칭이 생김. 빌드 산출물 스킵 누락 보강(D195,
#        score_findings.py의 같은 번호 주석 참고)과 같은 라운드에 세 곳을 동일 목록으로 맞춘다.
#   COST: score_findings.py와 동일 상수의 의도적 중복 — `from score_findings import SKIP_DIRS,
#        _is_skip_dir`는 score_findings.py:7이 이미 이 파일을 import하므로 순환 import가 되어
#        불가(실측: 양방향 모두 "partially initialized module" ImportError). D13/D76이 같은
#        이유로 상수 중복을 유지해온 이 저장소의 기존 컨벤션을 따른다.
#   EXIT: 사용처가 더 늘거나 목록 갱신이 잦아져 세 사본이 어긋나기 시작하면 공용 constants
#        모듈로 추출(D13 EXIT와 동일 조건).
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv",
    "static", "vendor", "vendored",
    "target",
    ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
    ".next", ".nuxt", ".output", ".nitro", ".svelte-kit", ".turbo", ".parcel-cache",
    "coverage", "storybook-static",
    ".vs",
    "DerivedData",
}
SKIP_DIR_PREFIXES = ("cmake-build-",)
SKIP_DIR_SUFFIXES = (".egg-info",)


def _is_skip_dir(name):
    return name in SKIP_DIRS or name.startswith(SKIP_DIR_PREFIXES) or name.endswith(SKIP_DIR_SUFFIXES)


def _find_file_content(repo_root, filename):
    for root, dirs, fnames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not _is_skip_dir(d)]
        if filename in fnames:
            return open(os.path.join(root, filename), encoding="utf-8", errors="ignore").read()
    return None


def patterns_path_for(lang):
    return os.path.join(IDIOMS_ROOT, lang, "idiom_patterns.json")


def log_path_for(lang):
    return os.path.join(IDIOMS_ROOT, lang, "idiom_feedback_log.jsonl")


def load_patterns(lang):
    path = patterns_path_for(lang)
    if not os.path.exists(path):
        return {"promotion_threshold": 3, "patterns": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def apply_idiom_filter(findings, repo_root=None):
    """finding["file"]의 확장자로 언어를 판별해 그 언어 저장소의 confirmed 패턴만 적용한다.

    candidate 상태인 패턴은 적용하지 않는다 — threshold 미달 신호를 신뢰하지 않는다는
    판단 블록의 원칙(D6) 그대로. 언어를 판별 못하면(확장자 미매핑) 필터를 건너뛴다.
    repo_root가 주어지면 .h 파일은 내용을 읽어 c/cpp를 재판정한다(D15).
    """
    cache = {}
    for finding in findings:
        pattern_key = finding.get("pattern_key")
        file = finding.get("file")
        if not pattern_key or not file:
            continue

        content = None
        if repo_root and file.endswith(".h"):
            content = _find_file_content(repo_root, file)
        lang = resolve_lang(file, content)
        if lang is None:
            finding["idiom_note"] = f"언어 미판별(확장자 미지원) — {file} 관용 패턴 필터 건너뜀"
            continue

        if lang not in cache:
            cache[lang] = load_patterns(lang)
        confirmed_lookup = {p["id"]: p for p in cache[lang]["patterns"] if p["status"] == "confirmed"}

        if pattern_key in confirmed_lookup:
            p = confirmed_lookup[pattern_key]
            original = finding["question_value"]
            finding["question_value"] = DOWNGRADE_MAP.get(original, original)
            finding["idiom_filtered"] = True
            finding["idiom_lang"] = lang
            finding["idiom_note"] = (
                f"[{lang}] 관용 패턴으로 판단(pattern={pattern_key}, "
                f"confirmations={p['confirmations']}) — 질문가치 {original}→{finding['question_value']}"
            )
            if "priority" in finding:
                finding["priority"] = f"{finding['priority']} → 관용 패턴 필터 적용됨(우선순위 낮음)"
            # D31: subrubric 감사 트레일이 최종 판정과 어긋나지 않도록 override 기록을 남긴다
            #   (원래 서브축 점수는 지우지 않는다 — "왜 원래 상이었는지"도 감사 대상이므로)
            if "subrubric" in finding and "question_value" in finding["subrubric"]:
                finding["subrubric"]["question_value"]["overridden_by"] = "idiom_filter"
                finding["subrubric"]["question_value"]["overridden_reason"] = finding["idiom_note"]
                finding["subrubric"]["question_value"]["final_bucket"] = finding["question_value"]
    return findings
