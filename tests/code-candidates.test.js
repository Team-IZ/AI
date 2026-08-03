// D-poc13 프로토타입(1~3단계 + 예산 계산)의 순수 로직 테스트.
//   실행: node --test tests/code-candidates.test.js   (저장소 루트에서)
//
// 이 테스트는 grounding을 위해 **실제 CodeFragment.locateSymbol을 그대로 불러 쓴다** --
// 목(mock) locate를 쓰면 "복제하지 않고 재사용한다"는 D-poc13 2단계의 핵심 결정을 정작
// 테스트가 검증하지 않게 된다. code-fragment.js는 P02Engine 전역을 베이스네임 폴백에만
// 쓰므로, 그 한 함수만 스텁으로 채운다(브라우저에서는 shared/p02-engine.js가 제공).
const test = require("node:test");
const assert = require("node:assert");

globalThis.P02Engine = {
  findFileByBasename(files, base) {
    return Object.keys(files).find((p) => p.split("/").pop() === base) || null;
  },
};

const CodeFragment = require("../app/stage2-analysis/code-fragment.js");
const CC = require("../app/stage2-analysis/code-candidates.js");

const LOCATE = { locate: CodeFragment.locateSymbol };

const FILES = {
  "src/pay.py": [
    "import os",
    "",
    "def pay(order, method):",
    "    if method == 'card':",
    "        return charge(order)",
    "    return None",
    "",
    "def refund(order):",
    "    return None",
  ].join("\n"),
  "src/util.py": [
    "def log(msg):",
    "    print(msg)",
    "",
    "def log_again(msg):",
    "    print(msg)",
  ].join("\n"),
};

// ── 2단계: grounding ────────────────────────────────────────────────────────────
test("실재하는 심볼은 grounding되고 시작 줄이 확정된다", () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", title: "결제 분기" },
  ], LOCATE);
  assert.strictEqual(c.grounded, true);
  assert.strictEqual(c.located.matchedLine, 3); // LLM이 센 값이 아니라 문자열 매치로 산정된 사실
  assert.strictEqual(c.confidence, CC.GROUND_CONFIDENCE.exactUnique);
});

test("지어낸 심볼은 버려진다(D-poc6/D-poc10 규율 유지)", () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/pay.py", symbol: "def settle(order, gateway):", title: "환각" },
  ], LOCATE);
  assert.strictEqual(c.grounded, false);
  assert.strictEqual(c.located, null);
  assert.match(c.groundReason, /찾을 수 없음/);
});

test("같은 줄이 여러 번 매치되면 버리지 않고 confidence만 강등한다", () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "finding", file: "src/util.py", symbol: "    print(msg)", title: "중복 매치" },
  ], LOCATE);
  assert.strictEqual(c.grounded, true);
  assert.strictEqual(c.confidence, CC.GROUND_CONFIDENCE.exactAmbiguous);
  assert.strictEqual(c.matchCounts.exact, 2);
  assert.match(c.groundReason, /다른 줄일 수 있음/);
});

test("너무 짧은 심볼은 위치로 쓰지 않지만 파일 단위 후보로는 남는다", () => {
  // P02 tier-b finding의 matched_text가 "uid" 같은 3글자로 오는 실제 경우
  // (score_findings.py:399 / two_tier_scan.py:292). locateSymbol은 첫 매치만 주므로
  // 이걸 그대로 믿으면 엉뚱한 줄에 조용히 grounding된다.
  const [c] = CC.groundCandidates(FILES, [
    { source: "finding", file: "src/pay.py", symbol: "os", title: "짧은 심볼" },
  ], LOCATE);
  assert.strictEqual(c.grounded, false);
  assert.strictEqual(c.fileResolved, "src/pay.py"); // 파일은 실재 -- 후보 자체가 사라지진 않는다
  assert.match(c.groundReason, /너무 짧아/);
});

test("파일 자체가 없으면 버려진다", () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/nope.py", symbol: "def pay(order, method):" },
  ], LOCATE);
  assert.strictEqual(c.grounded, false);
  assert.match(c.groundReason, /파일을 찾을 수 없음/);
});

// ── 1단계: 후보 수집 ────────────────────────────────────────────────────────────
test("finding 텍스트에서 matched_text 심볼을 뽑는다", () => {
  const sym = CC.symbolFromFinding({
    finding: "a.js — 시크릿 패턴 매치(오탐 가능성 있음, 육안 확인 필요) matched_text='API_KEY = \"sk-live-123\"'",
  });
  assert.strictEqual(sym, 'API_KEY = "sk-live-123"');
  assert.strictEqual(CC.symbolFromFinding({ finding: "허브 모듈로 가는 edge 없음" }), null);
});

test("구조 후보의 심볼은 파일에서 그대로 떼어오므로 항상 grounding된다", () => {
  const cands = CC.collectCandidates({
    fanIn: { "pay.py": 4, "util.py": 1 },
    files: FILES,
    structuralTop: 2,
  });
  assert.strictEqual(cands.length, 2);
  const grounded = CC.groundCandidates(FILES, cands, LOCATE);
  assert.ok(grounded.every((c) => c.grounded), "구조 후보는 전부 grounding되어야 한다");
});

// ── 3단계: 랭킹 ────────────────────────────────────────────────────────────────
test("두 소스가 같은 위치를 지목하면 합쳐지고 agreement 항이 켜진다", () => {
  const cands = [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: { teach_linked: true } },
    { source: "finding", file: "src/pay.py", symbol: "def pay(order, method):", meta: { finding_rank_score: 9 } },
    { source: "structural", file: "src/util.py", symbol: "def log(msg):", meta: {} },
  ];
  const ranked = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE), {
    fanIn: { "pay.py": 4, "util.py": 1 },
  });
  assert.strictEqual(ranked.length, 2, "같은 파일 같은 줄은 하나로 병합되어야 한다");
  const top = ranked[0];
  assert.strictEqual(top.rank, 1);
  assert.strictEqual(top.rank_evidence.terms.agreement, 1);
  assert.strictEqual(top.rank_evidence.terms.llm_proposed, 1);
  assert.strictEqual(top.rank_evidence.terms.teach_linked, 1);
  assert.ok(top.rank_score > ranked[1].rank_score);
  // D194와 같은 자기설명 계약: 항목 하나만 봐도 자기 순위를 설명할 수 있어야 한다
  assert.ok(top.rank_evidence.weights_provenance.includes("provisional"));
  assert.strictEqual(typeof top.rank_evidence.tie_break_depth, "number");
});

test("랭킹은 결정론적이다(입력 순서를 바꿔도 같은 순서)", () => {
  const cands = [
    { source: "llm", file: "src/pay.py", symbol: "def refund(order):", meta: {} },
    { source: "structural", file: "src/util.py", symbol: "def log(msg):", meta: {} },
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: {} },
  ];
  const order1 = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE)).map((c) => c.located.matchedLine + c.fileResolved);
  const order2 = CC.rankCandidates(CC.groundCandidates(FILES, cands.slice().reverse(), LOCATE)).map((c) => c.located.matchedLine + c.fileResolved);
  assert.deepStrictEqual(order1, order2);
});

test("selectTopK는 조건에 안 맞는 상위 후보를 건너뛸 뿐 그 아래를 버리지 않는다", () => {
  // codemap shortlist.py의 stopped=True 캐스케이드 함정 회귀 방지:
  // 같은 파일 후보 3개(전부 상위) + 다른 파일 후보 1개 -> maxPerFile=1이면
  // stop 구현은 뒤를 전부 버려 1개만 남지만, skip 구현은 다른 파일 후보를 살려낸다.
  const cands = [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: { teach_linked: true } },
    { source: "llm", file: "src/pay.py", symbol: "def refund(order):", meta: { teach_linked: true } },
    { source: "llm", file: "src/pay.py", symbol: "    return charge(order)", meta: { teach_linked: true } },
    { source: "structural", file: "src/util.py", symbol: "def log(msg):", meta: {} },
  ];
  const ranked = CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE));
  const picked = CC.selectTopK(ranked, { k: 3, maxPerFile: 1 });
  assert.strictEqual(picked.length, 2);
  assert.deepStrictEqual(
    Array.from(new Set(picked.map((c) => c.fileResolved))).sort(),
    ["src/pay.py", "src/util.py"]
  );
});

test("selectTopK는 grounding 실패 후보를 심층 분석 대상에서 뺀다", () => {
  const cands = [{ source: "llm", file: "src/pay.py", symbol: "def nope():" }];
  const picked = CC.selectTopK(CC.rankCandidates(CC.groundCandidates(FILES, cands, LOCATE)), { k: 3 });
  assert.strictEqual(picked.length, 0);
});

// ── buildCodeBlock 정렬 ─────────────────────────────────────────────────────────
test("fan_in 순서를 넘기면 알파벳순에서 잘려나가던 핵심 파일이 살아남는다", () => {
  const files = {
    "a_generated_bundle.js": "x".repeat(11960), // 알파벳으로 먼저 와서 예산을 먹는 파일
    "z_core.js": "function core(){ return 1; }", // 실제로 중요한 파일(fan_in 높음)
  };
  const fanIn = { "a_generated_bundle.js": 0, "z_core.js": 7 };

  const alphabetical = CodeFragment.buildCodeBlock(files, { maxChars: 12000 });
  assert.ok(!alphabetical.includes("function core()"), "오늘 동작: 알파벳으로 늦은 핵심 파일이 생략된다");
  assert.match(alphabetical, /생략함/);

  const ordered = CodeFragment.buildCodeBlock(files, {
    maxChars: 12000,
    order: CC.orderFilesByImportance(files, fanIn),
  });
  assert.ok(ordered.includes("function core()"), "fan_in 순서면 핵심 파일이 먼저 들어간다");
});

test("order를 안 넘기면 동작이 오늘과 완전히 같다", () => {
  const files = { "b.js": "bbb", "a.js": "aaa" };
  assert.strictEqual(
    CodeFragment.buildCodeBlock(files, { maxChars: 12000 }),
    CodeFragment.buildCodeBlock(files, { maxChars: 12000, order: null })
  );
});

// ── 4단계 예산 계산 ─────────────────────────────────────────────────────────────
test("여유가 충분하면 희망 K를 그대로 쓰되 동시성은 상한을 넘지 않는다", () => {
  const plan = CC.resolveFanoutPlan({ count: 0, isServerWide: true, threshold: 40 }, { k: 3 });
  assert.strictEqual(plan.k, 3);
  assert.strictEqual(plan.concurrency, CC.FANOUT_CONCURRENCY);
  assert.strictEqual(plan.maxAttempts, undefined); // P03 D181과 달리 재시도를 사지 않는다
});

test("트래픽이 상한에 가까우면 K가 0으로 깎여 fan-out 자체를 건너뛴다", () => {
  const plan = CC.resolveFanoutPlan({ count: 39, isServerWide: true, threshold: 40 }, { k: 3 });
  assert.strictEqual(plan.k, 0);
  assert.match(plan.reason, /생략/);
});

test("탭 기준 카운트(서버 전체 아님)면 여유를 더 깎는다", () => {
  const wide = CC.resolveFanoutPlan({ count: 25, isServerWide: true, threshold: 40 }, { k: 5 });
  const tabOnly = CC.resolveFanoutPlan({ count: 25, isServerWide: false, threshold: 40 }, { k: 5 });
  assert.ok(tabOnly.k < wide.k, "다른 팀원 트래픽이 안 잡히는 상황에서는 더 보수적이어야 한다");
});

test("희망 K가 상한을 넘어도 FANOUT_MAX_K로 잘린다", () => {
  const plan = CC.resolveFanoutPlan({ count: 0, isServerWide: true, threshold: 40 }, { k: 99 });
  assert.strictEqual(plan.k, CC.FANOUT_MAX_K);
});

test("트래픽을 못 읽으면 낙관하지 않고 동시성 1로 간다", () => {
  const plan = CC.resolveFanoutPlan(null, { k: 5 });
  assert.strictEqual(plan.concurrency, 1);
  assert.ok(plan.k <= CC.FANOUT_CONCURRENCY);
});
