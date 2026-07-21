import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from idiom_filter import apply_idiom_filter, resolve_lang, _find_file_content  # noqa: E402
from tier_b_suppression_filter import apply_tier_b_suppression  # noqa: E402
from subrubric import apply_subrubric, idiom_evidence, rationale_signal  # noqa: E402
from importance_rank import apply_rank  # noqa: E402

# D13: 반복 패턴 탐지 대상 확장자를 인지 블록(cognition/two_tier_scan.py)의 SRC_EXTS와 동일하게 유지
#   WHY: 인지 블록만 다국어로 확장하고 판단 블록이 JS/TS만 스캔하면 다시 불일치가 생김
#   COST: 두 파일에 같은 확장자 목록이 중복됨(cross-package import로 묶으면 경로가 더 취약해짐)
#   EXIT: 확장자 목록이 3번째로 필요해지면 그때 공용 constants 모듈로 추출
ALL_SRC_EXTS = (
    ".ts", ".tsx", ".js", ".jsx", ".py", ".java",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".swift",
)
# D76: cognition/two_tier_scan.py와 동일하게 static/vendor류 보강(D74/D75 실측 근거는 그쪽 파일 참고)
# D195: 빌드 산출물 스킵 누락 보강 — cognition/two_tier_scan.py와 완전 동일 목록/로직 유지
#   (D13/D76과 같은 이유의 의도적 중복)
#   WHY: 실측 — 프론트엔드 번들(webpack/vite 컨텐츠해시 산출물)이 스킵 디렉터리 밖에 복사돼
#        있으면 이름 고정 집합만으론 못 거름. 웹조사로 Java(Maven target/)/Python(.pytest_cache
#        등)/C·C++(CLion cmake-build-*)에도 같은 누락 확인. cmake-build-<profile>류 가변
#        디렉터리명과 <pkg>.egg-info류 접미어는 고정 집합으로 못 잡아 접두/접미어 매칭
#        (_is_skip_dir)을, 스킵 디렉터리 밖의 minified/컨텐츠해시 번들/TS 선언파일은
#        디렉터리명으로 못 잡아 파일명 정규식(GENERATED_FILENAME_RE)을 함께 추가.
#   COST: MSVC Debug/Release는 흔한 영단어라 실제 소스 디렉터리와 겹칠 위험이 있어 의도적으로
#        제외 — MSVC 산출물 트리는 여전히 못 거름. 디렉터리 판정이 집합 멤버십에서 함수 호출로
#        바뀌어 walk당 미미한 오버헤드 추가.
#   EXIT: MSVC류 누락이 실측되면 "루트 직하 + .sln/.vcxproj 동반" 같은 문맥 조건부 판정으로
#        확장. SKIP_DIRS 사용처가 더 늘거나 목록 갱신이 잦아지면 공용 constants 모듈로 추출.
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


# D195: 스킵 디렉터리 밖으로 나온 산출물 파일 필터(minified/컨텐츠해시 번들/TS 선언파일) —
#   근거/비용/출구는 위 D195 주석 참고
GENERATED_FILENAME_RE = re.compile(r"\.min\.jsx?$|\.[0-9a-f]{8,20}\.jsx?$|\.d\.ts$", re.I)

# D3: 판단 블록 = 규칙 기반 정성 채점(상/중/하), ML 아님
#   WHY: 사례가 적고(4건) 기준이 명확해 규칙 기반이 더 투명하고 디버깅 가능 —
#        "설계의도/질문가치/위험도" 3축은 2026-07-01 수동 분석에서 검증된 기준을 그대로 코드화
#   COST: 새로운 패턴이 생기면 사람이 직접 규칙을 추가해야 함(자동 일반화 안 됨)
#   EXIT: 규칙이 늘어나 유지보수가 안 되면 judgment_rules.yaml로 분리하거나,
#        hook 발동 로그 기반 자동 승격(WARN→BLOCK류)으로 대체
#
# D3-COST 해소: "관용 패턴 vs 진짜 설계 결정" 구분은 idiom_filter.py + idiom_hook.py로 분리 구현
#   (useAuth Context 같은 프레임워크 관례가 질문가치를 과대평가받던 문제, 아래 find_architecture_diffusion_point 참고)
#
# D3-COST 추가 해소(D29): "상/중/하가 근거 없이 한 줄 문자열로 확정된다"는 COST는
#   subrubric.py의 4서브축×3축 채점으로 해소 — 여전히 규칙 기반(ML/LLM 아님)이라 D3의
#   "투명하고 디버깅 가능"이라는 이유는 그대로 유지됨. 상세: SUBRUBRIC_DRAFT.md(D27/D28)

ENTRY_POINT_HINTS = ("main.", "index.")

# D122(gamma 실측 발견): ENTRY_POINT_HINTS 매치가 대소문자 구분(startswith)이라 Java의
#   표준 관례인 "Main.java"(대문자 M, 파일명이 public class명과 반드시 일치해야 하는 Java
#   언어 규칙상 클래스명 컨벤션인 PascalCase를 그대로 따름)를 절대 못 잡는다 -- "main."은
#   JS/TS 관례(main.tsx 등, 소문자)만 염두에 두고 만들어졌던 것으로 보인다. 결과: Java
#   프로젝트에서는 find_hub가 진짜 entry point를 후보에서 못 뺴고(Main이 fan_in=0이라
#   보통 hub로 안 뽑히긴 하지만), 더 크게는 find_routed_peers가 entry_srcs를 아예 못 찾아
#   cognition-isolation류 finding이 Java에서는 원천적으로 하나도 안 나왔을 가능성이 높다
#   (실측: gamma_s1 라운드1 4클래스 학생 과제 + M1 java 코퍼스 9개 repo 전부 대소문자
#   불일치로 무효화됐을 것으로 추정 -- M1 자체의 4축 집계 점수는 안 바뀌지만 개별
#   finding_id는 재스캔하면 바뀔 수 있음, README에 정직하게 캐비어트로 기록).
#   WHY: 대소문자 무시 매치로 바꾸면 Java(Main.java)/JS(main.tsx)/기타 모두 한 규칙으로 커버.
#   COST: 아주 드물게 클래스명이 우연히 "Main"으로 시작(예: MainMenu.java)하면 오탐 가능 --
#        기존 JS 쪽도 이미 같은 종류의 접두어-prefix 위험을 안고 있었으므로 새로운 종류의
#        리스크는 아님.
#   EXIT: 오탐이 실측되면 접두어 매치를 "정확히 그 이름"(Main.java/Index.java 등 전체일치)
#        으로 좁히면 됨 -- ENTRY_POINT_HINTS 상수만 바꾸면 두 사용처 다 반영됨.
def _matches_entry_hint(filename):
    lower = filename.lower()
    return any(lower.startswith(h) for h in ENTRY_POINT_HINTS)
REPEATED_PATTERN_MIN_FILES = 2
REPEATED_PATTERN_MIN_HITS = 2

# D130(LLMOps pilot 실측 발견): repeated-pattern이 "onSnapshot"(Firebase 전용 API명) 하나만
#   하드코딩돼 있어 그 문자열이 없는 언어/코드베이스(Python 등)에서는 절대 안 걸린다 -- 실측:
#   LangGraph Python 과제에서 app.py가 nodes.py/state.py의 analyzer_node/critic_node/
#   supervisor_node/ReviewState를 import 대신 거의 그대로 복붙했는데(진짜 유지보수성 이슈)
#   기존 탐지기로는 findings 0건이었음. "알려진 문자열 하나"가 아니라 "같은 이름의 함수/클래스
#   정의가 2개 이상 파일에 등장" 여부를 보는 일반 로직을 별도로 추가한다(onSnapshot 체크는
#   그대로 유지 -- 이미 검증된 Firebase 코퍼스 신호를 지우지 않음).
#   WHY: 언어별 "정의문" 자체는 단순 정규식으로 안정적으로 잡을 수 있다(Java 관례상 클래스명
#        추출과 동일한 종류의 패턴) -- 값비싼 AST/의미분석 없이도 강한 신호.
#   COST: 이름만 보고 본문(바디) 유사도는 안 본다 -- 우연히 같은 이름을 쓴 서로 무관한 함수도
#        걸릴 수 있음(1차 구현, 실측 오탐 나오면 본문 diff까지 확장 검토). dunder(__init__ 등)는
#        모든 클래스가 정의상 반복하므로 제외, 그 외 일반적인 이름(main/run/setup 등)은 최소
#        길이(5자)로만 대충 거름 -- 정교한 stopword 사전은 아직 없음(2026-07 시점, Python/
#        Java/JS만 정의문 패턴 보유, C/C++/Swift는 이 프로젝트에서 여전히 패턴 시드가 얕아
#        제외 -- cognition/two_tier_scan.py의 같은 한계와 동일선상).
#   EXIT: 오탐이 실측되면 (a) 본문 유사도(예: 정규화 후 difflib) 요구 추가, (b) 이름 stopword
#        사전 확장 중 택1. 새 언어 정의문 패턴은 DEFINITION_RE_BY_EXT에 확장자만 추가하면 됨.
DEFINITION_RE_BY_EXT = {
    ".py": re.compile(r"^\s*(?:def|class)\s+(\w+)", re.MULTILINE),
    ".java": re.compile(r"\bclass\s+(\w+)"),
    ".js": re.compile(r"^\s*(?:export\s+)?(?:function|class)\s+(\w+)", re.MULTILINE),
    ".jsx": re.compile(r"^\s*(?:export\s+)?(?:function|class)\s+(\w+)", re.MULTILINE),
    ".ts": re.compile(r"^\s*(?:export\s+)?(?:function|class)\s+(\w+)", re.MULTILINE),
    ".tsx": re.compile(r"^\s*(?:export\s+)?(?:function|class)\s+(\w+)", re.MULTILINE),
}
_DUNDER_RE = re.compile(r"^__\w+__$")
_TEST_NAME_RE = re.compile(r"^(?:test_|Test)\w+", re.IGNORECASE)
DUPLICATE_DEFINITION_MIN_NAME_LEN = 5

# D-web-lab: find_architecture_diffusion_point()의 fan_in>=2 임계값이 이름 없는 리터럴로
#   박혀 있어서 파이프라인 테스트 랩(docs/lab/)의 파라미터 편집기가 setattr(module, key, value)로
#   덮어쓸 대상을 못 찾는다 -- 이 파일의 다른 임계값(REPEATED_PATTERN_MIN_FILES 등)은 전부
#   이미 이름 붙은 모듈 상수라 똑같이 맞춘 것뿐, 동작은 완전히 동일(2 그대로).
#   WHY: 코드 자체를 편집 UI가 다루는 파라미터의 단일 진실 공급원으로 유지하려면(그래야 웹 랩이
#        런타임에 실제 값을 읽고 덮어쓸 수 있음), 상수는 전부 이름이 있어야 한다.
#   COST: 없음 -- 리터럴을 상수로 승격한 것뿐, 판정 로직/기본값 불변.
#   EXIT: 이 임계값이 더는 필요 없어지면 상수와 참조를 함께 제거.
DIFFUSION_FAN_IN_THRESHOLD = 2

# D130 회귀 실측(renamarr, M1 코퍼스): 위 필터만으로 돌리면 test_radarr_*.py/test_sonarr_*.py
#   같은 "같은 기능을 다른 백엔드용으로 미러링한 테스트 스위트"에서 test_no_series_returned,
#   TestAnalyzeFiles류 이름이 파일마다 반복 등장 -- 이건 나쁜 복붙이 아니라 대칭 구조를 의도적으로
#   맞춘 좋은 테스트 관례다(같은 코드가 아니라 "다른 대상을 같은 방식으로 검증"하는 것).
#   WHY: pytest/unittest 컨벤션상 test_ / Test 접두 이름은 이 검사의 "코드 복붙 의심" 취지와
#        다른 클래스의 반복이라 아예 후보에서 제외.
#   COST: 정말로 테스트 코드 자체가 복붙된 경우(진짜 헬퍼 함수 중복 등)는 test_ 접두여도 이제
#        놓친다 -- 이 검사의 책임 범위를 "실행 코드"로 좁힌 것으로 간주.
#   EXIT: 테스트 코드 복붙도 잡고 싶어지면 "이름"이 아니라 "본문 유사도"로 판정을 바꿔야 함.


def find_duplicate_definitions(repo_root, min_files=REPEATED_PATTERN_MIN_FILES):
    """같은 이름의 함수/클래스 정의가 min_files개 이상 파일에 등장하면 후보로 반환.
    {name: [file1, file2, ...]} 형태, dunder/짧은 이름/테스트 이름은 제외."""
    name_to_files = {}
    for root, dirs, fnames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not _is_skip_dir(d)]
        for fn in fnames:
            ext = os.path.splitext(fn)[1]
            pattern = DEFINITION_RE_BY_EXT.get(ext)
            if pattern is None:
                continue
            if GENERATED_FILENAME_RE.search(fn):  # D195: 스킵 디렉터리 밖 산출물 파일 제외
                continue
            text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
            for m in pattern.finditer(text):
                name = m.group(1)
                if _DUNDER_RE.match(name) or len(name) < DUPLICATE_DEFINITION_MIN_NAME_LEN:
                    continue
                if _TEST_NAME_RE.match(name):
                    continue
                name_to_files.setdefault(name, set()).add(fn)
    return {name: sorted(files) for name, files in name_to_files.items() if len(files) >= min_files}

# D29의 파일명 힌트 기반 location_signal은 D35로 대체됨 — 문헌 근거(SATD 탐지 방법론)가
#   있는 subrubric.rationale_signal()(코멘트 스캔)로 교체. LOCATION_INTENT_HINTS 상수는
#   더 이상 쓰이지 않아 삭제(원안은 git history D29 커밋 참고)


# D16: hub 동점 시 fan-out(자신이 얼마나 많이 import하는지)이 낮은 쪽으로 tie-break
#   WHY: D12(fan-in 이중계산 수정) 부작용으로 App.tsx(main.tsx→App.tsx 엣지가 새로 정확히
#        잡히면서 fan_in 7)와 firebase.ts(fan_in 7)가 동점이 됨 — 회귀 테스트에서 실측 발견.
#        fan_in만으론 "많은 파일이 의존하는 서비스 허브"(firebase.ts, fan_out=0)와
#        "여러 컴포넌트를 조립하는 컨테이너"(App.tsx, fan_out=8)를 구분 못함. 진짜 허브는
#        의존은 적게 받으면서 적게 하는(sink에 가까운) 쪽이라는 게 실측 근거
#   COST: fan_out도 계산해야 해서 find_hub가 edges까지 받아야 함(시그니처 변경)
#   EXIT: 이 휴리스틱도 틀리면 명시적 화이트리스트("firebase.ts", "db.ts" 같은 서비스 파일명
#        패턴)로 대체 검토
def find_hub(fan_in, edges):
    """entry point(main.tsx 등)를 제외한 fan-in 최고 파일 = 인지 블록이 뽑은 '허브'.

    fan_in이 동점이면 fan_out이 낮은(자기는 남을 덜 의존하는, sink에 가까운) 쪽을 우선한다.
    """
    candidates = {k: v for k, v in fan_in.items() if not _matches_entry_hint(k)}
    if not candidates:
        return None
    fan_out = {k: 0 for k in candidates}
    for src, dst in edges:
        if src in fan_out:
            fan_out[src] += 1
    max_fan_in = max(candidates.values())
    top = [k for k, v in candidates.items() if v == max_fan_in]
    return min(top, key=lambda k: fan_out[k])


# D19: 고립 판정 범위를 "전체 파일"이 아니라 "앱 루트가 직접 라우팅하는 형제 파일"로 제한
#   WHY: jxxnixx/LMS 실측 — 기존 로직은 fan_in>=1인 전체 51개 파일 중 허브(GenreContext.jsx)에
#        안 걸린 30개를 전부 "최우선" 고립으로 오탐지함. Study-Match-에서 통했던 이유는 App.tsx가
#        직접 라우팅하는 형제 컴포넌트가 전부 firebase.ts를 쓰는 게 우연이 아니라 구조였기 때문 —
#        규모가 커지고 관심사가 여러 개(Genre/Auth/Books 등)로 나뉘면 "전체가 허브를 써야 한다"는
#        가정 자체가 깨짐. 대신 "entry→root가 직접 import하는 형제들"끼리만 비교해야 함
#   COST: 형제 그룹 바깥의 진짜 고립 파일(예: 깊은 유틸리티 파일의 이상 패턴)은 이 규칙으로 못 잡음
#        — 그건 애초에 이 규칙의 책임 범위가 아니라는 뜻으로 재정의한 것(범위 축소가 의도)
#   EXIT: "형제 그룹"을 root 직계가 아니라 2단계까지 확장하고 싶으면 find_routed_peers의
#        BFS 깊이만 늘리면 됨
def find_routed_peers(edges):
    """entry point가 import하는 루트 컴포넌트(들)가 직접 import하는 파일 집합 = 비교 대상 형제 그룹."""
    entry_srcs = {src for src, dst in edges if _matches_entry_hint(src)}
    roots = {dst for src, dst in edges if src in entry_srcs}
    peers = {dst for src, dst in edges if src in roots}
    return peers


def find_isolated_from_hub(edges, fan_in, hub):
    """루트가 직접 라우팅하는 형제 파일 중, 허브로 가는 edge가 없는 파일(D19로 범위 제한).

    실측(Study-Match-): fan-in 지표만 보면 이런 파일을 놓친다 — 이 파일도 fan_in=1로
    '정상'처럼 보이기 때문. 반드시 edge의 목적지(dst)까지 봐야 드러난다.
    """
    peers = find_routed_peers(edges)
    routed = {f for f in peers if fan_in.get(f, 0) >= 1 and f != hub}
    connected_to_hub = {src for src, dst in edges if dst == hub}
    return sorted(routed - connected_to_hub)


def find_architecture_diffusion_point(fan_in, hub, repo_root):
    """허브 다음으로 fan-in이 높은 파일 = 여러 컴포넌트가 공유해서 쓰는 두 번째 확산 지점.

    이 자체는 관용 패턴(React Context 등)일 수도, 진짜 설계 판단일 수도 있다 —
    여기서는 후보만 뽑고, "관용 패턴인지"는 pattern_key를 붙여 idiom_filter가 판정하게 넘긴다.
    """
    candidates = {k: v for k, v in fan_in.items() if k != hub and v >= DIFFUSION_FAN_IN_THRESHOLD}
    if not candidates:
        return None
    top_file = max(candidates, key=candidates.get)

    pattern_key = None
    for root, dirs, fnames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not _is_skip_dir(d)]
        for fn in fnames:
            if fn == top_file:
                text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
                if re.search(r"createContext\s*(<[^>]*>)?\s*\(", text):
                    pattern_key = "react-context-global-state"
                # D21: React Query "리소스별 커스텀 훅" 컨벤션도 관용 패턴 후보로 인식
                #   WHY: jxxnixx/LMS 실측 — useBooksQueries.ts가 fan_in=6으로 diffusion 후보에
                #        올랐는데 내용은 @tanstack/react-query 공식 문서가 권장하는 표준 패턴
                #        (리소스당 useQuery 래퍼 훅)이라 createContext 패턴 하나만 보던 기존
                #        탐지로는 놓쳤음. 이것도 "설계 판단"보다 "라이브러리 컨벤션"에 가까움
                #   COST: 다른 라이브러리의 유사 컨벤션(Redux Toolkit의 slice 등)은 여전히 미탐지
                #   EXIT: 새 컨벤션이 발견될 때마다 이 블록에 정규식 분기 추가(패턴 사전이 커지면
                #        PATTERN_DETECTORS 딕셔너리로 리팩터링 검토)
                elif re.search(r"\buse(Suspense)?(Query|Mutation)\s*\(", text):
                    pattern_key = "react-query-custom-hook"
                break
    return {"file": top_file, "fan_in": candidates[top_file], "pattern_key": pattern_key}


def find_repeated_pattern_files(repo_root, pattern, min_hits):
    hits = {}
    for root, dirs, fnames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if not _is_skip_dir(d)]
        for fn in fnames:
            if not fn.endswith(ALL_SRC_EXTS):
                continue
            if GENERATED_FILENAME_RE.search(fn):  # D195: 스킵 디렉터리 밖 산출물 파일 제외
                continue
            text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
            c = len(re.findall(re.escape(pattern), text))
            if c >= min_hits:
                hits[fn] = c
    return hits


def score(scan_result, repo_root):
    tier_a = scan_result["tier_a_structural"]
    tier_b = scan_result["tier_b_risk_triggered"]
    fan_in, edges = tier_a["fan_in"], tier_a["edges"]

    findings = []

    hub = find_hub(fan_in, edges)
    if hub:
        # D29: risk_evidence.spread_count는 "동일 위험 패턴이 몇 파일에 반복되는가"만 의미한다.
        #   cognition-isolation/architecture-diffusion/repeated-pattern은 보안·신뢰성 위험이
        #   아니라 구조적 특성이므로 여기서는 항상 0으로 고정한다(design_intent의
        #   repetition_consistency 서브축이 "구조적 반복"은 이미 별도로 반영함 — risk 축까지
        #   fan_in을 재사용하면 "많이 참조되는 파일=위험"이라는 잘못된 의미가 섞인다).
        isolated_files = find_isolated_from_hub(edges, fan_in, hub)
        for f in isolated_files:
            finding = {
                "id": f"cognition-isolation:{f}",
                "file": f,
                "finding": f"{f} — 허브 모듈({hub})로 가는 edge 없음. fan_in={fan_in.get(f)}만 보면 정상으로 보임",
                "priority": "최우선",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=len(isolated_files) - 1,
                    idiom_status="none",
                    rationale=rationale_signal(f, repo_root),
                    mitigation_present=None,
                ),
                question_value_evidence=dict(
                    tradeoff_signal=True,
                    repo_specificity=True,
                    idiom_downgrade_votes=0,
                    ladder_richness=fan_in.get(f, 0),
                ),
                risk_evidence=dict(
                    trigger_confirmed=None,
                    exposure_client=None,
                    scenario_specific=False,
                    spread_count=0,
                ),
            )
            findings.append(finding)

        diffusion = find_architecture_diffusion_point(fan_in, hub, repo_root)
        if diffusion:
            idiom_status, idiom_confirmations = idiom_evidence(diffusion["pattern_key"], diffusion["file"])
            finding = {
                "id": f"architecture-diffusion:{diffusion['file']}",
                "file": diffusion["file"],
                "pattern_key": diffusion["pattern_key"],
                "finding": f"{diffusion['file']} — 허브 다음으로 fan_in={diffusion['fan_in']}, 여러 컴포넌트가 공유",
                "priority": "질문 대상",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=diffusion["fan_in"],
                    idiom_status=idiom_status,
                    rationale=rationale_signal(diffusion["file"], repo_root),
                    mitigation_present=None,
                ),
                question_value_evidence=dict(
                    tradeoff_signal=True,
                    repo_specificity=True,
                    idiom_downgrade_votes=idiom_confirmations,
                    ladder_richness=diffusion["fan_in"],
                ),
                risk_evidence=dict(
                    trigger_confirmed=None,
                    exposure_client=None,
                    scenario_specific=False,
                    spread_count=0,
                ),
            )
            findings.append(finding)

    # D14: Tier B raw 히트에서 confirmed 오탐((trigger,matched_text) 단위)을 먼저 걷어낸 뒤 채점
    tier_b_flagged = apply_tier_b_suppression(tier_b["flagged_files"])

    # D29: Tier B는 판단 축(위험도)의 spread_count가 실제로 의미 있는 유일한 finding
    #   군이다 — 같은 트리거가 몇 개 파일에서 반복되는지가 "위험의 확산 범위"이기 때문.
    trigger_file_counts = {}
    for hits in tier_b_flagged.values():
        for h in hits:
            trigger_file_counts[h["trigger"]] = trigger_file_counts.get(h["trigger"], 0) + 1

    for f, hits in tier_b_flagged.items():
        triggers = {h["trigger"] for h in hits}
        if "auth_info_leak_via_thrown_error" in triggers:
            finding = {
                "id": f"tier-b-risk:{f}",
                "file": f,
                "finding": f"{f} — 인증정보(uid/email 등)가 JSON.stringify되어 throw된 Error에 담김",
                "priority": "Important(🔴)",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=trigger_file_counts["auth_info_leak_via_thrown_error"] - 1,
                    idiom_status="none",
                    rationale=rationale_signal(f, repo_root),
                    mitigation_present=True,  # throw로 에러 컨텍스트를 남기려는 의도 자체는 존재
                ),
                question_value_evidence=dict(
                    tradeoff_signal=True,
                    repo_specificity=True,
                    idiom_downgrade_votes=0,
                    ladder_richness=len(hits),
                ),
                risk_evidence=dict(
                    trigger_confirmed=True,  # 오탐 억제 필터 통과 + auth/stringify/throw 3조건 AND 매치라 신뢰도 높음
                    exposure_client="server" not in f.lower(),
                    scenario_specific=True,
                    spread_count=trigger_file_counts["auth_info_leak_via_thrown_error"] - 1,
                ),
            )
            findings.append(finding)
        if "hardcoded_secret_pattern" in triggers:
            matched = next(h["matched_text"] for h in hits if h["trigger"] == "hardcoded_secret_pattern")
            finding = {
                "id": f"tier-b-risk:{f}:secret",
                "file": f,
                "finding": f"{f} — 시크릿 패턴 매치(오탐 가능성 있음, 육안 확인 필요) matched_text={matched!r}",
                "priority": "검토 대상(자동 신뢰 금지)",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=0,
                    idiom_status="none",
                    rationale=rationale_signal(f, repo_root),
                    mitigation_present=None,  # 하드코딩 시크릿에 "의도된 완화책"이란 개념 자체가 성립 안 함
                ),
                question_value_evidence=dict(
                    tradeoff_signal=False,  # 시크릿 하드코딩엔 정당화 가능한 대안이 없음 — 질문해도 트레이드오프 논의로 안 이어짐
                    repo_specificity=True,
                    idiom_downgrade_votes=0,
                    ladder_richness=1,
                ),
                risk_evidence=dict(
                    trigger_confirmed=None,  # D12-secret 이후에도 정규식 매치 자체는 오탐 이력이 있어 확정 불가
                    exposure_client=None,
                    scenario_specific=matched.lower().startswith(("sk-", "akia")),
                    spread_count=trigger_file_counts["hardcoded_secret_pattern"] - 1,
                ),
            )
            findings.append(finding)
        # D20: eval_or_dangerous_html 트리거를 finding으로 승격하는 규칙이 누락돼 있었음
        #   WHY: jxxnixx/LMS 실측 — 인지 블록은 Bookshelf.jsx의 dangerouslySetInnerHTML을 정확히
        #        잡았는데 판단 블록에 이 트리거를 finding화하는 규칙 자체가 없어서 조용히 버려짐
        #        (auth_info_leak/hardcoded_secret 두 트리거만 처리하고 있었음)
        #   COST: 없음 — 순수 누락 버그였음
        #   EXIT: 새 Tier B 트리거를 추가할 때마다 이 블록에도 대응 규칙을 반드시 추가해야 함
        #        (트리거 추가와 finding화 규칙 추가가 분리돼 있어 또 누락될 수 있음 — 근본 해법은
        #        트리거 이름→finding 템플릿을 딕셔너리로 묶어 한 곳에서 관리하는 리팩터링)
        if "eval_or_dangerous_html" in triggers:
            matched = next(h["matched_text"] for h in hits if h["trigger"] == "eval_or_dangerous_html")
            finding = {
                "id": f"tier-b-risk:{f}:dangerous-html",
                "file": f,
                "finding": f"{f} — 위험한 동적 실행/HTML 삽입 패턴 matched_text={matched!r} (XSS 등 위험 가능, 입력 출처 확인 필요)",
                "priority": "Important(🔴)",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=trigger_file_counts["eval_or_dangerous_html"] - 1,
                    idiom_status="none",
                    rationale=rationale_signal(f, repo_root),
                    mitigation_present=False,  # 정규식 매치 시점에 sanitize 흔적이 안 보임(부정 신호로 취급)
                ),
                question_value_evidence=dict(
                    tradeoff_signal=True,  # 항상 sanitize 라이브러리/JSX 텍스트 렌더링 같은 대안이 존재
                    repo_specificity=True,
                    idiom_downgrade_votes=0,
                    ladder_richness=2,
                ),
                risk_evidence=dict(
                    trigger_confirmed=True,  # D17로 메서드 호출(.eval()) 오탐은 이미 정규식에서 배제됨
                    exposure_client=True,  # eval/dangerouslySetInnerHTML은 정의상 실행·렌더 컨텍스트에 직접 노출
                    scenario_specific=True,
                    spread_count=trigger_file_counts["eval_or_dangerous_html"] - 1,
                ),
            )
            findings.append(finding)

    for pattern, min_files in {"onSnapshot": REPEATED_PATTERN_MIN_FILES}.items():
        repeated = find_repeated_pattern_files(repo_root, pattern, REPEATED_PATTERN_MIN_HITS)
        if len(repeated) >= min_files:
            finding = {
                "id": f"repeated-pattern:{pattern}",
                "file": None,
                "finding": f"'{pattern}' 반복 등장 파일: {repeated}",
                "priority": "질문 대상",
            }
            apply_subrubric(
                finding,
                design_intent_evidence=dict(
                    repetition=len(repeated),
                    idiom_status="none",
                    rationale=rationale_signal(None, repo_root),  # 단일 file 없음(여러 파일 걸침) → 항상 "none"
                    mitigation_present=None,
                ),
                question_value_evidence=dict(
                    tradeoff_signal=True,  # 공용 훅으로 추출한다는 대안이 항상 존재
                    repo_specificity=True,
                    idiom_downgrade_votes=0,
                    ladder_richness=len(repeated),
                ),
                risk_evidence=dict(
                    trigger_confirmed=None,
                    exposure_client=None,
                    scenario_specific=False,
                    spread_count=0,  # 유지보수성 이슈이지 보안/신뢰성 위험의 확산이 아님
                ),
            )
            findings.append(finding)

    for name, files in find_duplicate_definitions(repo_root).items():
        finding = {
            "id": f"repeated-pattern:duplicate-definition:{name}",
            "file": None,
            "finding": f"'{name}' 정의가 {len(files)}개 파일에 거의 그대로 재등장: {files} (import 대신 복붙 의심)",
            "priority": "질문 대상",
        }
        apply_subrubric(
            finding,
            design_intent_evidence=dict(
                repetition=len(files),
                idiom_status="none",
                rationale=rationale_signal(None, repo_root),
                mitigation_present=None,
            ),
            question_value_evidence=dict(
                tradeoff_signal=True,  # 공용 모듈로 추출한다는 대안이 항상 존재
                repo_specificity=True,
                idiom_downgrade_votes=0,
                ladder_richness=len(files),
            ),
            risk_evidence=dict(
                trigger_confirmed=None,
                exposure_client=None,
                scenario_specific=False,
                spread_count=0,  # 유지보수성 이슈이지 보안/신뢰성 위험의 확산이 아님
            ),
        )
        findings.append(finding)

    findings = apply_idiom_filter(findings, repo_root=repo_root)
    _tag_lang(findings, repo_root)
    # D194 (findings_ranking_plan.md Phase 0): must run AFTER apply_idiom_filter so the ranker
    # sees post-override subrubric.question_value (rank_evidence.idiom_gate_applied depends on
    # finding["subrubric"]["question_value"]["overridden_by"] already being set). fan_in is the
    # same Tier A dict already in scope above -- only used for the rank tie-break chain's 3rd
    # step, never re-derived.
    findings = apply_rank(findings, fan_in=fan_in)
    return {"hub": hub, "findings": findings}


# D76: 각 finding에 실제 파일 언어를 스키마 필드로 박아넣음 (D75가 dataset/corpus_report.py에서
#   사후 계산으로 우회했던 것을 스키마 자체에 반영)
#   WHY: repo 하나를 하나의 언어로 태깅하는 소비자(dataset/mine_repo.py 등)가 있으면, 그 repo
#        안에 섞인 다른 언어 파일(D75 실측: Django repo의 static/js/ 번들, Java repo의 React
#        프론트엔드)의 finding이 잘못된 언어로 집계된다. finding 자체가 자기 언어를 알면 이
#        문제는 소비자마다 재발명할 필요 없이 한 번에 해소된다. `apply_idiom_filter`가 이미
#        pattern_key 있는 finding만 국한해 lang을 계산하므로(idiom_filter.py:98) 재사용 불가 —
#        모든 finding에 대해 별도로 계산한다. `.h` 파일은 idiom_filter와 동일하게 내용 기반
#        c/cpp 재판정(D15)을 적용해 일관성을 유지한다.
#   COST: `file`이 None인 finding(예: repeated-pattern은 여러 파일에 걸침, D74 확인)은
#        `lang`도 None — 이건 정보 손실이 아니라 애초에 단일 파일에 귀속 안 되는 finding이라
#        None이 정확한 값이다. `.h` 파일 재판정을 위해 각 finding마다 repo_root를 다시
#        walk하므로(`_find_file_content`), finding 수가 아주 많은 repo에서는 약간의 중복
#        I/O가 생긴다(이미 D15/apply_idiom_filter가 architecture-diffusion finding에 대해
#        같은 비용을 지불하고 있었으므로 새로운 종류의 비용은 아님).
#   EXIT: repo_root가 없어 파일을 못 찾으면(예: scan_output.json만 따로 재채점하는 경우)
#        확장자만으로 판정 -- .h는 이 경우 idiom_filter와 동일하게 "c"로 보수적 기본값 처리.
def _tag_lang(findings, repo_root):
    for finding in findings:
        file = finding.get("file")
        if not file:
            finding["lang"] = None
            continue
        content = _find_file_content(repo_root, file) if (repo_root and file.endswith(".h")) else None
        finding["lang"] = resolve_lang(file, content)


def main():
    if len(sys.argv) < 3:
        print("usage: score_findings.py <scan_output.json> <repo_root>", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        scan_result = json.load(f)
    print(json.dumps(score(scan_result, sys.argv[2]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
