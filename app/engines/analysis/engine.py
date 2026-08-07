""" 실물 분석 엔진. 부품을 순서대로 엮어 `AnalysisResult`를 만든다.

**이 파일에 판단 로직을 두지 않는다.** 각 단계의 판단은 자기 모듈에 있고 여기는
순서·연결·스키마 채우기만 한다. 여기에 규칙이 새면 "어디를 고쳐야 하는가"가 흐려진다.

    룰 스캔          rules.find_candidates()   ZIP → 후보 + 소스 본문
      ↓
    p04-1 분석 문서  analysis_doc.build()      후보·교안·코드 → 문서
      ↓
    p04-2 요구사항   requirements.judge()      요구사항 P/F (있을 때만)
      ↓
    p04-3 문제 선정  topics.select()           teach당 문제 1개, 부족분은 일반 문제
      ↓
    p04-4 질문       questions.freeze()        문제당 L1~L4 질문 4개
      ↓
    p04-7 힌트       hints.freeze_for_stage()  질문당 힌트 2개
      ↓
    AnalysisResult

**전면 동결이라 세션 중 생성이 없다**(PLAN §T10). 문제 3개면 여기서 질문 12개 +
힌트 24개가 전부 만들어져 나간다.

## 실패를 어떻게 다루나

**부분 실패를 통째 실패로 만들지 않는다.** LLM 콜이 30회를 넘고 무료 티어 실패율이
32%라, 하나 깨졌다고 전체를 버리면 완주 확률이 거의 없다.

    p04-1 실패      전체 실패 — 뒤 단계가 전부 이 문서를 입력으로 받는다
    p04-2 실패      요구사항만 비우고 계속 — 문답과 독립이다
    질문 생성 실패   그 문제만 flagged로 남기고 계속 (questions.py가 처리)
    힌트 생성 실패   결정론적 폴백 문장 (hints.py가 처리)

`usages`는 실패한 콜까지 전부 모은다 — 실패한 호출도 토큰을 태우기 때문이다.
"""

import hashlib
import uuid
from typing import Any

from app.config import get_settings
from app.engines.analysis import (
    analysis_doc,
    fetch,
    fragments,
    hints,
    imports,
    materialize,
    questions,
    requirements,
    rules,
    scoring,
    stages,
    topics,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 확장자 → assessment_problem.code_language. DB CHECK가 "공백 아님"만 요구하므로
# 값 집합은 우리가 정한다. 정의서 예시(JAVA·PYTHON·JAVASCRIPT·YAML)를 따른다.
_LANGUAGE_BY_SUFFIX = {
    "py": "PYTHON", "java": "JAVA", "js": "JAVASCRIPT", "jsx": "JAVASCRIPT",
    "ts": "TYPESCRIPT", "tsx": "TYPESCRIPT", "kt": "KOTLIN", "go": "GO",
    "rb": "RUBY", "c": "C", "h": "C", "cpp": "CPP", "cc": "CPP", "hpp": "CPP",
    "cs": "CSHARP", "rs": "RUST", "php": "PHP", "swift": "SWIFT", "scala": "SCALA",
    "sql": "SQL", "sh": "SHELL", "yml": "YAML", "yaml": "YAML",
    "json": "JSON", "xml": "XML", "html": "HTML", "css": "CSS", "md": "MARKDOWN",
}


def _code_language(path: str) -> str:
    """파일 확장자로 언어를 정한다. 모르면 UNKNOWN — 빈 값은 DB CHECK가 막는다."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _LANGUAGE_BY_SUFFIX.get(suffix, "UNKNOWN")


def _snippet_key(problem_no: int, evidence_hash: str) -> str:
    """`submission.code_snippets[].snippet_key`와 맞물릴 안정 키.

    문제 번호로 분석 안에서의 유일성이 보장되고, 해시를 붙여 **같은 코드면 재분석해도
    같은 값**이 나오게 한다(문제 번호만 쓰면 순서가 바뀔 때 다른 코드에 같은 키가 붙는다).
    """
    return f"p{problem_no}-{evidence_hash[:16]}"


def _problem_title(topic: dict[str, Any], teach: dict[str, Any] | None,
                   source_path: str) -> str:
    """`assessment_problem.title`(GENERATED면 NOT NULL).

    p04-3이 낸 topic 제목이 이 지점을 가장 잘 설명한다. 없으면 검증 개념 이름으로,
    그것도 없으면 파일 경로로 물러난다 — 빈 문자열은 INSERT가 거부한다.
    """
    for value in (topic.get("title"), (teach or {}).get("label"),
                  (teach or {}).get("id"), source_path):
        text = str(value or "").strip()
        if text:
            return text[:200]
    return "코드 이해 문제"


# 화면에 통째로 띄울 파일의 상한. 실측(spring-petclinic 49개 파일)에서 중앙 1,987자·
# p90 6,264자·최대 10,464자라 평범한 소스는 전부 들어온다. 이 상한에 걸리는 파일은
# 생성물이거나 한 파일에 다 밀어 넣은 경우인데, 그때는 통째로 띄우는 것 자체가
# 학생에게 도움이 안 되므로 파편으로 되돌린다(잘라서 줄 번호를 어긋나게 하지 않는다).
_MAX_DISPLAY_CHARS = 100_000


def _display_source(files: dict[str, str], ref: dict[str, Any], fragment: str) -> str:
    """학생 화면에 띄울 코드. **문제를 낸 파일 전체다.**

    파일을 못 찾거나 너무 크면 파편으로 되돌린다 — 그 경우 `lineStart`가 파일 기준
    절대 줄 번호라는 점은 그대로라, 화면이 줄 번호를 함께 그리면 어긋나지 않는다.
    """
    text = files.get(ref.get("file", "")) or ""
    if not text or len(text) > _MAX_DISPLAY_CHARS:
        return fragment
    return text


def _snapshot_meta(zip_bytes: bytes | None, files: dict[str, str]) -> dict[str, Any]:
    """제출물 지문. **같은 코드를 다시 내면 같은 값이 나와야 한다** — Spring이
    "같은 제출물인가"를 이 값으로 판정한다.

    ZIP은 업로드된 바이트 그대로가 기준이다. GITHUB_URL은 기준이 될 바이트가 없어서
    (클론 결과는 파일 시각·순서 때문에 매번 다르게 압축된다) **스캔한 소스 본문**으로
    낸다. 경로로 정렬해 넣으므로 파일 시스템 순서에 안 흔들린다.
    """
    if zip_bytes is not None:
        return {
            "content_hash": hashlib.sha256(zip_bytes).hexdigest(),
            "file_count": len(files),
            "byte_count": len(zip_bytes),
        }

    digest = hashlib.sha256()
    byte_count = 0
    for path in sorted(files):
        body = files[path].encode("utf-8")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(body)
        byte_count += len(body)
    return {
        "content_hash": digest.hexdigest(),
        "file_count": len(files),
        "byte_count": byte_count,
    }


def _stamp(usages: list[dict[str, Any]], feature_code: str) -> list[dict[str, Any]]:
    """호출 기록에 "어느 기능이 불렀나"를 찍는다.

    `llm/client.py`는 자기가 어느 기능에 쓰이는지 모른다(알아야 할 이유도 없다).
    나머지 요청 범위 값(`contextId`·`traceId` 등)은 job 계층이 채운다 — 엔진은
    job_id도 헤더도 모르기 때문이다.
    """
    return [{**u, "feature_code": feature_code} for u in usages]


def _problem_type(topic: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    """룰이 같은 파일에서 잡은 후보가 있으면 그 분류를 쓴다.

    없으면 `DESIGN_CHOICE`다 — LLM이 "판단이 개입된 지점"으로 고른 것이므로
    기본값이 `COMPLEXITY_HOTSPOT`(룰의 기본값)이면 선정 이유와 어긋난다.
    """
    path = (topic.get("code_ref") or {}).get("file")
    for c in candidates:
        if path and c.get("source_path") == path:
            return c.get("problem_type") or "DESIGN_CHOICE"
    return "DESIGN_CHOICE"


def _priority(topic: dict[str, Any], candidates: list[dict[str, Any]]) -> float:
    path = (topic.get("code_ref") or {}).get("file")
    for c in candidates:
        if path and c.get("source_path") == path:
            return float(c.get("priority") or 0.0)
    return 0.0


def _references(ref: dict[str, Any], snippet: str, teach: dict[str, Any] | None,
                importers: list[str]) -> list[dict[str, Any]]:
    """문제의 근거 목록. **LLM을 부르지 않는다** — 이미 산정된 사실만 모은다.

    지금까지 항상 빈 배열이었다. 채우는 데 필요한 것이 다 있었는데 조립을 안 했다.

        PRIMARY_BLOCK        문제를 낸 그 지점. 화면에 띄울 본문의 위치
        QUESTION_HIGHLIGHT   축별 강조 구간. 4축 전부 같은 지점을 가리킨다 —
                             축마다 다른 구간을 짚으려면 LLM이 필요하고, 지금은 근거가 없다
        CURRICULUM_EVIDENCE  이 문제가 검증하는 교안 개념. 코드 라인이 없다
        CALLER               이 파일을 import 하는 파일들 (import 그래프)

    ⚠️ `RELATED_CONTEXT`는 안 만든다. 심볼 테이블이 없어 "같이 봐야 하는 자리"를
    특정할 근거가 없다 — 지어내면 학생이 무관한 코드를 읽는다.
    """
    path = ref.get("file", "")
    lo = ref.get("line_start") or 1
    hi = ref.get("line_end") or lo
    fragment_hash = _sha256(snippet)

    refs: list[dict[str, Any]] = [{
        "reference_type": "PRIMARY_BLOCK", "display_order": 1,
        "path": path, "line_start": lo, "line_end": hi,
        "evidence_hash": fragment_hash,
    }]

    order = 2
    for axis in scoring.AXIS_CODES:
        refs.append({
            "reference_type": "QUESTION_HIGHLIGHT", "display_order": order,
            "path": path, "line_start": lo, "line_end": hi,
            "axis_code": axis, "evidence_hash": fragment_hash,
        })
        order += 1

    if teach and teach.get("id"):
        refs.append({
            "reference_type": "CURRICULUM_EVIDENCE", "display_order": order,
            "teach_id": teach["id"],
            # 교안 근거는 코드가 없다. 개념 이름을 해시해 중복만 막는다.
            "evidence_hash": _sha256(f"teach:{teach['id']}"),
        })
        order += 1

    for importer in importers:
        refs.append({
            "reference_type": "CALLER", "display_order": order,
            "path": importer, "line_start": 1, "line_end": 1,
            "evidence_hash": _sha256(f"caller:{importer}->{path}"),
        })
        order += 1

    return refs


def _stage(axis_code: str, question: str | None, hint_list: list[hints.Hint],
           flagged: bool) -> dict[str, Any]:
    return {
        "axis_code": axis_code,
        "question_text": question,
        "flagged": flagged,
        "hints": [{"hint_level": h.hint_level, "hint_text": h.text} for h in hint_list],
    }


def _teach_by_id(teaches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in teaches if t.get("id")}


class AnalysisFailed(Exception):
    """엔진이 중간에 멈췄다. **여기까지 태운 원장을 들고 나온다.**

    원장은 결과와 별개다 — job이 FAILED여도 콜은 실제로 나갔고 백엔드가 그걸로
    비용을 집계한다. 그냥 raise하면 실패 지점 이전의 성공분까지 전부 사라진다
    (2026-08-03 실호출: p04-3에서 터지며 p04-1·p04-2의 24콜이 aiUsage 0건으로
    나갔다).
    """

    def __init__(self, message: str, ai_usage: list[dict[str, Any]]):
        super().__init__(message)
        self.ai_usage = ai_usage


# 실패한 스테이지를 원장의 어느 종류로 적을지. 스테이지마다 다르다.
_STAGE_KIND = {
    "p04-1": "CODE_ANALYSIS", "p04-2": "CODE_ANALYSIS",
    "p04-3": "QUESTION_GENERATION", "p04-4": "QUESTION_GENERATION",
    "p04-7": "QUESTION_GENERATION",
    "p04-5": "GRADING", "p04-6": "REPORT",
}


def _failed_kind(message: str) -> str:
    """StageError 메시지 앞머리(`p04-3: ...`)에서 종류를 읽는다."""
    return _STAGE_KIND.get(message.split(":", 1)[0].strip(), "CODE_ANALYSIS")


class RealAnalysisEngine:
    """`engine_mode="real"`일 때 쓰이는 엔진."""

    def analyze(self, request: dict[str, Any], zip_bytes: bytes | None = None,
                *, prefetched_root: str | None = None,
                prefetched_git: dict[str, Any] | None = None) -> dict[str, Any]:
        """실패해도 원장은 살려 내보낸다. 실제 작업은 `_run`이 한다.

        `fetch.FetchError`(M4 -- 이 엔진 자신이 fetch할 때도 이제 fetch.py를 쓴다)는
        여기서 `AnalysisFailed`로 감싸지 않고 그대로 흘려보낸다 -- fetch 실패 시점엔
        아직 LLM 콜이 하나도 없어 `usages`가 항상 빈 배열이라 원장을 잃을 게 없고,
        `jobs.py`가 이미 `FetchError`를 따로 받아 `failure_code`로 정확히 옮긴다
        (M3). 여기서 감싸면 그 분류가 무너진다.
        """
        usages: list[dict[str, Any]] = []
        try:
            return self._run(request, zip_bytes, usages, prefetched_root=prefetched_root,
                              prefetched_git=prefetched_git)
        except fetch.FetchError:
            raise
        except stages.StageError as exc:
            usages.extend(_stamp(exc.usages, _failed_kind(str(exc))))
            raise AnalysisFailed(str(exc), usages) from exc
        except Exception as exc:
            raise AnalysisFailed(str(exc), usages) from exc

    def _run(self, request: dict[str, Any], zip_bytes: bytes | None,
             usages: list[dict[str, Any]], *, prefetched_root: str | None = None,
             prefetched_git: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = get_settings()
        # wire 필드는 providerModelCode다 — 공급자에게 그대로 넘길 문자열이라
        # 화면 선택값(model_code)이 아니라 ai_model.provider_model_code 값이다.
        model_code = request.get("provider_model_code") or settings.model_code_analysis

        teaches = request.get("teaches") or []
        reqs = request.get("requirements") or []
        budget = int(request.get("question_budget") or scoring.QUESTIONS_PER_SUBMISSION)

        # ── 룰 스캔 ────────────────────────────────────────────────────────────
        # GITHUB_URL이면 클론, ZIP이면 압축 해제. 두 경로가 같은 스캔으로 합류한다.
        # 디렉터리는 with를 빠져나가며 지워지므로 파일 내용은 여기서 다 읽어 나온다.
        #
        # prefetched_root가 있으면(analysisInput 경로, D2) 이 엔진의 fetch를 건너뛰고
        # 그 경로를 그대로 스캔한다 -- jobs._run_via_analysis_input이 refetch_pinned()의
        # `with` 블록 **안에서** analyze()를 부르므로 여기서 디렉터리가 아직 살아 있다.
        #
        # (M4) 자기 fetch가 필요한 경우도 fetch.py를 쓴다(예전엔 materialize.py였다) --
        # 구현체를 하나로 통합하면 실패 시 failureCode 분류(fetch.FetchError)를
        # 이 경로도 그대로 받는다. request의 소스 모양(`source.repo_url`/`source.branch`)을
        # fetch.py가 기대하는 스펙 키(`repository_url`/`requested_branch`)로 바꿔 넘긴다.
        commit_sha = request.get("commit_sha")
        # D-analysis-b1(2026-08-07): resolved_branch/head_commit/git_history는 두 경로 다
        # 채워야 AnalysisResult에 실린다(기존엔 아예 안 실렸다 -- 배선 누락).
        resolved_branch: str | None = None
        head_commit: dict[str, Any] | None = None
        git_history: list[dict[str, Any]] = []
        git_history_source = "NONE"
        history_truncated = False
        if prefetched_root is not None:
            scan = rules.scan_directory(prefetched_root)
            if request.get("method") == "GITHUB_URL":
                commit_sha = materialize.head_sha(prefetched_root) or commit_sha
            if prefetched_git:
                resolved_branch = prefetched_git.get("resolved_branch")
                head_commit = prefetched_git.get("head_commit")
                git_history = prefetched_git.get("git_history") or []
                git_history_source = prefetched_git.get("git_history_source", "NONE")
                history_truncated = bool(prefetched_git.get("history_truncated", False))
        else:
            source = request.get("source") or {}
            spec = {
                "method": request.get("method"),
                "repository_url": source.get("repo_url"),
                "requested_branch": source.get("branch"),
            }
            with fetch.fetch(spec, zip_bytes) as fetched:
                scan = rules.scan_directory(fetched.root)
                if fetched.head_commit:
                    # 클론 경로에서만 실제 커밋을 안다. ZIP은 요청 값을 그대로 쓴다.
                    commit_sha = fetched.head_commit["sha"]
                resolved_branch = fetched.resolved_branch
                head_commit = fetched.head_commit
                git_history = fetched.git_history
                git_history_source = fetched.git_history_source
                history_truncated = fetched.history_truncated
        files, candidates = scan["files"], scan["candidates"]

        # ── p04-1 분석 문서 ────────────────────────────────────────────────────
        # 실패하면 전체 실패다. 뒤 단계가 전부 이 문서를 입력으로 받는다.
        doc = analysis_doc.build(files, teaches, candidates, model_code=model_code)
        usages.extend(_stamp(doc.usages, "CODE_ANALYSIS"))

        # ── p04-2 요구사항 P/F ────────────────────────────────────────────────
        # 문답과 독립이라 실패해도 계속 간다 — 이해도 측정이 요구사항 판정에 걸려
        # 통째로 무너지면 안 된다(PM 설계 v2 §8-3: "이해도 점수와 섞지 않는다").
        requirement_results: list[dict[str, Any]] = []
        if reqs:
            try:
                judged = requirements.judge(reqs, files, model_code=model_code)
                requirement_results = judged.results
                usages.extend(_stamp(judged.usages, "CODE_ANALYSIS"))
            except stages.StageError as exc:
                usages.extend(_stamp(exc.usages, "CODE_ANALYSIS"))
                requirement_results = [
                    {"requirement_id": str(r.get("requirement_id") or r.get("requirementId")
                                           or f"req-{i + 1}"),
                     "verdict": "FAIL", "evidence": None,
                     "note": f"판정 실패: {exc}"}
                    for i, r in enumerate(reqs)
                ]

        # ── p04-3 문제 선정 ───────────────────────────────────────────────────
        selection = topics.select(files, teaches, doc.document, candidates,
                                  model_code=model_code, question_budget=budget)
        usages.extend(_stamp(selection.usages, "QUESTION_GENERATION"))

        # ── p04-4 질문 + p04-7 힌트 ───────────────────────────────────────────
        teach_map = _teach_by_id(teaches)
        # "이 파일을 누가 import 하나". references[].CALLER 를 채운다. LLM 0회.
        importers = imports.build(files)
        # 강사 지정 초점 후보를 문제에 순서대로 물린다(C-1 확정 — 받은 id를 그대로 에코).
        focus_ids = [item["id"] for item in (request.get("focus_items") or [])]
        problems: list[dict[str, Any]] = []

        # 질문을 먼저 다 만들고, 힌트는 **한 배치로 몰아 병렬 호출한다.**
        # 실측(2026-08-02): 힌트 8콜이 616초로 전체 902초의 68%였고 서로 완전히
        # 독립이다. 문제 3개면 24콜인데 순차로 돌면 그것만 30분이 넘는다.
        planned: list[dict[str, Any]] = []
        hint_specs: list[dict[str, Any]] = []

        for no, topic in enumerate(selection.topics, start=1):
            teach = teach_map.get(topic.get("teach_id"))
            qset = questions.freeze(topic, files, teach, model_code=model_code)
            usages.extend(_stamp(qset.usages, "QUESTION_GENERATION"))

            ref = topic.get("code_ref") or {}
            code_ref_str = fragments.format_ref(
                ref.get("file", "?"), ref.get("line_start"), ref.get("line_end")
            )
            snippet = ref.get("snippet") or ""

            # 질문 생성이 막혔어도 문제는 만든다. 스키마가 4단계를 요구하므로 빈 자리를
            # 두면 응답 전체가 검증에서 깨진다 — 그러면 성공한 문제 2개까지 같이 잃는다.
            by_axis = {lv["axis_code"]: lv["question"] for lv in qset.levels}
            axis_questions = [
                by_axis.get(axis) or _blocked_question(axis, code_ref_str)
                for axis in scoring.AXIS_CODES
            ]
            planned.append({
                "no": no, "topic": topic, "ref": ref, "snippet": snippet,
                "code_ref": code_ref_str, "flagged": qset.flagged,
                "questions": axis_questions,
                "hint_offset": len(hint_specs),
            })
            hint_specs += [
                {"question": q, "teach": teach,
                 "code_snippet": snippet, "code_ref": code_ref_str}
                for q in axis_questions
            ]

        hint_sets = hints.freeze_many(hint_specs, model_code=model_code)
        for hint_list in hint_sets:
            for h in hint_list:
                usages.extend(_stamp(h.usages, "QUESTION_GENERATION"))

        for plan in planned:
            no, topic, ref = plan["no"], plan["topic"], plan["ref"]
            snippet = plan["snippet"]
            offset = plan["hint_offset"]
            stage_rows = [
                _stage(axis, plan["questions"][i], hint_sets[offset + i], plan["flagged"])
                for i, axis in enumerate(scoring.AXIS_CODES)
            ]

            evidence_hash = _sha256(snippet)
            source_path = ref.get("file", "")
            display_source = _display_source(files, ref, snippet)
            problems.append({
                "problem_id": str(uuid.uuid4()),
                "problem_no": no,
                "status": "READY",
                # 아래 3개는 DB assessment_problem이 GENERATED에서 NOT NULL로 요구한다
                # (테이블정의서 v06). 안 보내면 문제가 하나도 안 들어간다.
                "title": _problem_title(topic, teach_map.get(topic.get("teach_id")),
                                        source_path),
                "snippet_key": _snippet_key(no, evidence_hash),
                "code_language": _code_language(source_path),
                "problem_type": _problem_type(topic, candidates),
                "priority": _priority(topic, candidates),
                "question_focus_item_id": focus_ids[no - 1] if no <= len(focus_ids) else None,
                # 어느 교안 개념을 검증하는 문제인가. 일반 문제면 None이다
                # (topics._general_topics가 teach_id를 비운다).
                "teach_id": topic.get("teach_id"),
                "source_path": source_path,
                # 파일 전체 안에서 **하이라이트할 구간**이다. codeSnippet의 부분범위가
                # 아니라 파일 기준 절대 줄 번호다 — 화면이 파일을 그리고 이 구간을 강조한다.
                "line_start": ref.get("line_start") or 1,
                "line_end": ref.get("line_end") or ref.get("line_start") or 1,
                # 🔴 **파일 전체다**(2026-08-03 확정). 파편만 주면 학생이 판단할 재료가
                # 없다 — L2 질문이 checkOut을 언급하는데 화면엔 선언 한 줄만 뜨는 일이
                # 실제로 났다(실측: 스니펫 29~51자, 1줄).
                "code_snippet": display_source,
                # 해시가 둘이다. **대상이 다르다.**
                #   content_hash   codeSnippet 전체 = submission.code_snippets[].content_hash
                #   evidence_hash  파편           = assessment_problem.code_snippet_hash
                # 하나로 합치면 둘 중 하나가 틀린 것을 가리킨다.
                "content_hash": _sha256(display_source),
                # 🔴 해시는 **파편** 기준을 유지한다. 파일 전체로 바꾸면 무관한 한 줄
                # 수정에도 "근거가 바뀌었다"가 되어 판정이 쓸모없어진다.
                "evidence_hash": evidence_hash,
                "extractor_version": scan["extractor_version"],
                "references": _references(ref, snippet, teach_map.get(topic.get("teach_id")),
                                         importers.get(ref.get("file", ""), [])),
                "stages": stage_rows,
            })

        return {
            "snapshot_id": str(uuid.uuid4()),
            "snapshot_meta": _snapshot_meta(zip_bytes, files),
            "applied_scope": request["extraction_scope"],
            # OWN_COMMIT은 아직 구현이 없다. 요청이 오면 TOTAL로 물러나되 그 사실을 알린다.
            "scope_fallback": request["extraction_scope"] == "OWN_COMMIT",
            "fallback_reason": ("OWN_COMMIT 범위는 아직 지원하지 않아 전체를 분석했습니다"
                                if request["extraction_scope"] == "OWN_COMMIT" else None),
            "commit_sha": commit_sha,
            "analysis_document": analysis_doc.to_schema(doc.document, files),
            "requirement_results": requirement_results,
            "problems": problems,
            "unmatched_teaches": selection.unmatched,
            "question_count_planned": budget,
            "ai_usage": usages,
            "resolved_branch": resolved_branch,
            "head_commit": head_commit,
            "git_history": git_history,
            "git_history_source": git_history_source,
            "history_truncated": history_truncated,
        }


def _blocked_question(axis_code: str, code_ref: str) -> str:
    """질문 생성이 재생성 상한까지 막혔을 때 그 자리를 채우는 문장.

    **flagged=True와 함께만 나간다** — 화면에 "검수 필요"로 뜨고, 사람이 보기 전까지
    이 문장이 학생에게 그대로 나갈 수 있으므로 축의 의도는 지킨다.
    """
    intent = scoring.AXES[axis_code]["question_intent"]
    return f"{code_ref}에 대해 이야기해 주세요 — {intent}."
