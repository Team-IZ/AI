"""D-tierb1 / D-tierb2 회귀 테스트.

실행: python3 -m pytest tests/ -q   (저장소 루트에서)

이 저장소에는 이 파일 이전까지 Python 테스트가 **하나도 없었다**(유일한 테스트는
worker/nvidia-proxy.test.js). 그래서 Tier B 제거의 "무엇이 깨지고 무엇이 조용히
퇴화하는가"를 확인할 기존 그물이 없었고, 아래 테스트가 그 그물의 첫 칸이다.

위치가 judgment/ 나 cognition/ 이 아니라 tests/ 인 이유:
.github/workflows/pages.yml의 "Drift-check vendored files"가 cognition/judgment/shared를
feat/poc_full과 diff -r로 통째 비교한다 -- 그 디렉터리에 파일을 더할수록 두 브랜치를
함께 고쳐야 하는 표면이 넓어진다. 테스트는 런타임 산출물이 아니므로 drift 대상 밖에 둔다.
"""
import os
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(REPO_ROOT, "cognition"), os.path.join(REPO_ROOT, "judgment")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import two_tier_scan  # noqa: E402
import score_findings  # noqa: E402
import importance_rank  # noqa: E402


# D-tierb2가 인용하는 고정 fixture와 동일한 코드베이스.
# entry(main) -> App -> {firebase(hub), auth, Bookshelf, util}, util/auth에 중복 정의 1쌍.
# 과거 Tier B 트리거 3종(auth+stringify+throw / dangerouslySetInnerHTML / sk- 시크릿)이
# 전부 들어 있다 -- "이제는 아무것도 안 잡힌다"를 증명하기 위해 일부러 남겨둔 것이다.
FIXTURE = {
    "src/main.js": "import App from './App';\nApp();\n",
    "src/App.js": textwrap.dedent("""\
        import { db } from './firebase';
        import { login } from './auth';
        import { render } from './Bookshelf';
        import { helper } from './util';
        export function App(){ return db && login && render && helper; }
    """),
    "src/firebase.js": "export const db = {};\nexport function connect(){ return db; }\n",
    "src/auth.js": textwrap.dedent("""\
        import { db } from './firebase';
        export function login(uid){
          try { return db; }
          catch (e) { throw new Error(JSON.stringify({ uid: uid, email: 'x@y.z' })); }
        }
        export function sharedHelperRoutine(a){ return a; }
    """),
    "src/Bookshelf.js": textwrap.dedent("""\
        import { db } from './firebase';
        export function render(html){
          return { dangerouslySetInnerHTML: { __html: html } };
        }
    """),
    "src/util.js": textwrap.dedent("""\
        const api_key = "sk-live-abcdefgh12345678";
        export function helper(){ return api_key; }
        export function sharedHelperRoutine(a){ return a; }
    """),
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("submission")
    for rel, content in FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(root)


@pytest.fixture(scope="module")
def scanned(repo):
    scan = two_tier_scan.scan(repo)
    return scan, score_findings.score(scan, repo)


# ── D-tierb1: Tier B가 활성 경로에서 완전히 사라졌는가 ────────────────────────────
def test_tier_b_scan_function_is_gone():
    assert not hasattr(two_tier_scan, "tier_b_risk_triggered_scan")
    for const in ("AUTH_KEYWORDS", "STRINGIFY_RE", "THROW_RE", "EVAL_RE", "SECRET_RE"):
        assert not hasattr(two_tier_scan, const), f"{const}이 아직 남아 있다"


def test_tier_b_suppression_modules_are_gone():
    for mod in ("tier_b_hook", "tier_b_suppression_filter"):
        with pytest.raises(ImportError):
            __import__(mod)


def test_scan_output_has_no_tier_b_key(scanned):
    scan, _ = scanned
    assert "tier_b_risk_triggered" not in scan
    assert "tier_a_structural" in scan, "Tier A는 그대로 살아 있어야 한다"


def test_no_tier_b_findings_even_though_all_three_triggers_are_present(scanned):
    """fixture에는 과거 트리거 3종이 전부 들어 있는데 finding은 하나도 안 나와야 한다."""
    _, judgment = scanned
    assert [f for f in judgment["findings"] if f["id"].startswith("tier-b-risk")] == []


# ── D-tierb1: Tier A 계열은 계속 동작하고 계속 랭킹되는가 ─────────────────────────
def test_tier_a_findings_survive_and_are_ranked(scanned):
    _, judgment = scanned
    assert judgment["hub"] == "firebase.js"

    findings = judgment["findings"]
    assert findings, "Tier A 계열 finding은 계속 나와야 한다"

    ids = {f["id"] for f in findings}
    assert "cognition-isolation:util.js" in ids
    assert "repeated-pattern:duplicate-definition:sharedHelperRoutine" in ids

    # 랭킹이 실제로 붙었는가 (1..N 연속, rank_score 내림차순)
    assert [f["rank"] for f in findings] == list(range(1, len(findings) + 1))
    scores = [f["rank_score"] for f in findings]
    assert scores == sorted(scores, reverse=True)
    for f in findings:
        assert "rank_evidence" in f and "terms" in f["rank_evidence"]


def test_score_ignores_a_legacy_scan_json_that_still_has_the_tier_b_key(repo):
    """예전 형식으로 저장된 scan_output.json을 다시 먹여도 깨지지 않아야 한다(D-tierb1)."""
    scan = two_tier_scan.scan(repo)
    scan["tier_b_risk_triggered"] = {
        "flagged_files": {"util.js": [{"trigger": "hardcoded_secret_pattern", "matched_text": "sk-x"}]},
        "deep_read_count": 1, "total_files": 6, "cost_saved_ratio": 0.833,
    }
    judgment = score_findings.score(scan, repo)
    assert [f for f in judgment["findings"] if f["id"].startswith("tier-b-risk")] == []


# ── D-tierb2: 랭킹의 조용한 퇴화를 수치로 고정한다 ───────────────────────────────
def test_risk_axis_is_now_constant_across_every_surviving_finding(scanned):
    """이게 D-tierb2의 핵심 주장이다. 깨지면 -- risk를 실제로 채우는 finding 소스가
    새로 생겼다는 뜻이므로, importance_rank.py의 D-tierb2 주석을 갱신해야 한다."""
    _, judgment = scanned
    risk_totals = {f["subrubric"]["risk"]["total"] for f in judgment["findings"]}
    trigger_conf = {f["subrubric"]["risk"]["sub"]["trigger_confidence"] for f in judgment["findings"]}
    assert risk_totals == {2}, f"risk.total이 더 이상 상수가 아니다: {risk_totals}"
    assert trigger_conf == {1}, f"trigger_confidence가 더 이상 상수가 아니다: {trigger_conf}"


def test_sort_key_slots_1_and_2_no_longer_discriminate(scanned):
    """_sort_key의 (-risk_total, -trigger_confidence) 두 슬롯이 실질 무효임을 직접 확인."""
    _, judgment = scanned
    keys = [importance_rank._sort_key(f, {}) for f in judgment["findings"]]
    assert len({k[1] for k in keys}) == 1, "risk_total 슬롯이 정렬에 기여하고 있다"
    assert len({k[2] for k in keys}) == 1, "trigger_confidence 슬롯이 정렬에 기여하고 있다"


def test_risk_weight_cannot_change_the_order_only_the_score(scanned, repo):
    """웹 랩 p02-5의 RANK_WEIGHT_RISK 슬라이더가 순서를 못 바꾼다는 D-tierb2 COST(1)."""
    _, baseline = scanned
    baseline_order = [f["id"] for f in baseline["findings"]]

    original = importance_rank.RANK_WEIGHT_RISK
    try:
        importance_rank.RANK_WEIGHT_RISK = 99.0
        scan = two_tier_scan.scan(repo)
        skewed = score_findings.score(scan, repo)
    finally:
        importance_rank.RANK_WEIGHT_RISK = original

    assert [f["id"] for f in skewed["findings"]] == baseline_order, (
        "risk가 상수인 동안에는 가중치를 아무리 키워도 순서가 바뀌면 안 된다"
    )


def test_rank_is_deterministic(repo):
    """같은 제출물은 항상 같은 순서를 낸다(재현성 보장 -- _sort_key 마지막 슬롯이 id)."""
    a = score_findings.score(two_tier_scan.scan(repo), repo)["findings"]
    b = score_findings.score(two_tier_scan.scan(repo), repo)["findings"]
    assert [f["id"] for f in a] == [f["id"] for f in b]
    assert [f["rank_score"] for f in a] == [f["rank_score"] for f in b]


def test_apply_rank_still_works_without_fan_in():
    """fan_in을 안 넘기는 독립 재채점 경로도 그대로 동작해야 한다(apply_rank의 계약)."""
    findings = [
        {"id": "b", "subrubric": {"question_value": {"total": 5}, "risk": {"total": 2, "sub": {"trigger_confidence": 1}}, "design_intent": {"total": 3}}},
        {"id": "a", "subrubric": {"question_value": {"total": 9}, "risk": {"total": 2, "sub": {"trigger_confidence": 1}}, "design_intent": {"total": 3}}},
    ]
    ranked = importance_rank.apply_rank(findings)
    assert [f["id"] for f in ranked] == ["a", "b"]
    assert ranked[0]["rank"] == 1
