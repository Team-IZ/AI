// shared/code-locate.js (D-ground1)의 순수 로직 테스트.
//   실행: node --test tests/code-locate.test.js   (저장소 루트에서)
//
// 이 테스트는 **실제 구현을 그대로 require해서** 검증한다 -- 목(mock) locate를 쓰면
// "poc_full의 로직을 복제하지 않고 이 저장소 사정에 맞게 하드닝했다"는 D-ground1 §3의
// 핵심 결정을 정작 테스트가 검증하지 않게 된다.
//
// 위치가 shared/가 아니라 tests/인 이유: .github/workflows/pages.yml의 drift-check가
// shared/ 전체를 feat/poc_full과 diff -r로 비교하므로, shared/에 파일을 하나 더 놓을수록
// 두 브랜치를 함께 고쳐야 하는 표면이 넓어진다. 테스트는 런타임에 브라우저가 부르지
// 않으므로 drift 대상 밖(tests/)에 두는 게 맞다.
const test = require("node:test");
const assert = require("node:assert");

const CodeLocate = require("../shared/code-locate.js");

// P02Engine.findFileByBasename의 브라우저 구현과 동등한 최소 스텁(D-fix8의 결정론적
// 정렬 규칙: 짧은 경로 우선, 그다음 알파벳순)을 주입한다.
const RESOLVER = (files, basename) => {
  const c = Object.keys(files).filter((p) => p.split("/").pop() === basename);
  if (!c.length) return null;
  c.sort((a, b) => a.length - b.length || a.localeCompare(b));
  return c[0];
};
const OPTS = { resolveByBasename: RESOLVER };

const FILES = {
  "src/pay.py": [
    "import os",                       // 1
    "",                                // 2
    "def pay(order, method):",         // 3
    "    if method == 'card':",        // 4
    "        return charge(order)",    // 5
    "    return None",                 // 6
    "",                                // 7
    "def refund(order):",              // 8
    "    return None",                 // 9
  ].join("\n"),
  "src/util.py": [
    "def log_message(msg):",           // 1
    "    print('prefix', msg)",        // 2
    "",                                // 3
    "def log_message_again(msg):",     // 4
    "    print('prefix', msg)",        // 5
  ].join("\n"),
};

// ── 핵심 계약: 시작 줄은 LLM이 센 값이 아니라 문자열 매치로 산정된 사실 ─────────────
test("실재하는 심볼은 grounding되고 시작 줄이 확정된다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/pay.py", "def pay(order, method):", OPTS);
  assert.strictEqual(r.valid, true);
  assert.strictEqual(r.matchedLine, 3);
  assert.strictEqual(r.ambiguous, false);
  assert.strictEqual(r.matchedBy, "exact");
});

test("블록 끝은 들여쓰기로 추정되고 뒤따르는 빈 줄은 제외된다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/pay.py", "def pay(order, method):", OPTS);
  // 3행에서 시작, 들여쓰기가 얕아지는 8행(def refund) 앞까지 -> 6행, 7행(빈 줄)은 잘림
  assert.deepStrictEqual(r.lines, [3, 6]);
});

test("지어낸 심볼은 버려진다 (D-ground1 §6: 없는 코드는 절대 통과 못 함)", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/pay.py", "def settle(order, gateway):", OPTS);
  assert.strictEqual(r.valid, false);
  assert.match(r.reason, /찾을 수 없음/);
  assert.strictEqual(r.fileResolved, "src/pay.py"); // 파일은 실재했다는 정보는 남긴다
});

test("파일 자체가 없으면 버려진다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/nope.py", "def pay(order, method):", OPTS);
  assert.strictEqual(r.valid, false);
  assert.match(r.reason, /파일을 찾을 수 없음/);
  assert.strictEqual(r.fileResolved, null);
});

test("베이스네임만 줘도 해석된다(P02 finding.file이 베이스네임인 D179 계약)", () => {
  const r = CodeLocate.locateSymbol(FILES, "pay.py", "def pay(order, method):", OPTS);
  assert.strictEqual(r.valid, true);
  assert.strictEqual(r.file, "src/pay.py");
});

// ── D-ground1 §3(a): 짧은 심볼 하드닝 (poc_full locateSymbol에는 없는 방어) ─────────
test("너무 짧은 심볼은 조용히 엉뚱한 줄에 매치되지 않고 거부된다", () => {
  // 폐기된 Tier B의 matched_text가 "uid" 같은 3글자였다 -- locateSymbol이 첫 매치만
  // 주므로 그대로 믿으면 틀린 줄을 확신에 차서 근거로 보여주게 된다.
  const r = CodeLocate.locateSymbol(FILES, "src/pay.py", "os", OPTS);
  assert.strictEqual(r.valid, false);
  assert.match(r.reason, /너무 짧아/);
  assert.strictEqual(r.fileResolved, "src/pay.py");
});

test("임계값 경계: MIN_SYMBOL_LEN 미만은 거부, 이상은 통과", () => {
  const short = "x".repeat(CodeLocate.MIN_SYMBOL_LEN - 1);
  const ok = "import os"; // 9자 >= 9
  assert.strictEqual(CodeLocate.locateSymbol({ "a.py": short }, "a.py", short, OPTS).valid, false);
  assert.strictEqual(CodeLocate.locateSymbol(FILES, "src/pay.py", ok, OPTS).valid, true);
});

test("MIN_SYMBOL_LEN은 실측값 9로 고정된다 (D-ground1m)", () => {
  // 손으로 고른 8이 아니라 실측값이다. 62파일/14,855줄 실제 코퍼스에서 심각 모호
  // (같은 파일 내 5줄 이상 매치)가 L=4..8의 13.3% -> L=9..13의 3.7%로 3.6배 꺾이는 지점.
  // 측정 방법·전체 분포·재보정 조건은 shared/code-locate.js 상단 §3의 D-ground1m 블록.
  //
  // 이 값을 바꾸려면 **다시 측정하고** 그 수치로 위 블록을 교체해야 한다 --
  // 직관으로 조정하는 것은 이 저장소가 금지한다(judgment/importance_rank.py D194 참조).
  assert.strictEqual(CodeLocate.MIN_SYMBOL_LEN, 9);
});

test("실측 재앙 구간(L<=3)은 확실히 거부된다", () => {
  // 폐기된 Tier B의 matched_text("uid")가 정확히 이 구간이었다. 측정상 L=1..3은
  // 같은 파일에서 평균 41.3줄에 매치되고 95.5%가 2줄 이상 -- 첫 매치는 사실상 무작위다.
  for (const needle of ["uid", "os", "db"]) {
    const r = CodeLocate.locateSymbol(FILES, "src/pay.py", needle, OPTS);
    assert.strictEqual(r.valid, false, `${needle}는 거부돼야 한다`);
    assert.match(r.reason, /너무 짧아/);
  }
});

// ── D-ground1 §3(b): 다중 매치는 버리지 않고 강등 ────────────────────────────────
test("여러 줄에 매치되면 버리지 않고 ambiguous로 표시한다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/util.py", "    print('prefix', msg)", OPTS);
  assert.strictEqual(r.valid, true);
  assert.strictEqual(r.ambiguous, true);
  assert.strictEqual(r.matchCount, 2);
  assert.strictEqual(r.matchedLine, 2, "첫 매치를 쓰되 모호하다는 사실을 함께 알린다");
});

test("유일 매치는 ambiguous가 아니다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/util.py", "def log_message(msg):", OPTS);
  assert.strictEqual(r.ambiguous, false);
  assert.strictEqual(r.matchCount, 1);
});

// ── 공백 정규화 폴백 ──────────────────────────────────────────────────────────────
test("들여쓰기/공백만 다르게 인용해도 정규화 폴백으로 찾는다", () => {
  const r = CodeLocate.locateSymbol(FILES, "src/pay.py", "def  pay(order,   method):", OPTS);
  assert.strictEqual(r.valid, true);
  assert.strictEqual(r.matchedLine, 3);
  assert.strictEqual(r.matchedBy, "normalized");
});

test("빈 symbol은 거부된다", () => {
  assert.strictEqual(CodeLocate.locateSymbol(FILES, "src/pay.py", "   ", OPTS).valid, false);
  assert.strictEqual(CodeLocate.locateSymbol(FILES, "src/pay.py", null, OPTS).valid, false);
});

// ── 조각 추출 ────────────────────────────────────────────────────────────────────
test("fragment는 ±2줄 문맥을 포함하고 실제 파일 내용과 일치한다", () => {
  const located = CodeLocate.locateSymbol(FILES, "src/pay.py", "def pay(order, method):", OPTS);
  const frag = CodeLocate.buildFragment(FILES, located);
  assert.deepStrictEqual(frag.contextLines, [1, 8]);
  assert.ok(frag.text.includes("def pay(order, method):"));
  assert.ok(frag.text.includes("import os"), "위쪽 문맥 2줄");
  assert.ok(frag.text.includes("def refund(order):"), "아래쪽 문맥 2줄");
  // 지어낸 텍스트가 아니라 실제 파일에서 그대로 떼어온 것이어야 한다
  assert.ok(FILES["src/pay.py"].includes(frag.text));
});

test("무효 위치로는 fragment를 만들지 않는다", () => {
  const bad = CodeLocate.locateSymbol(FILES, "src/pay.py", "def nope_at_all():", OPTS);
  assert.strictEqual(CodeLocate.buildFragment(FILES, bad), null);
});

// ── §6 규율의 진입점 ─────────────────────────────────────────────────────────────
test("groundDecisionPoints는 검증 통과분만 남기고 버린 이유를 보고한다", () => {
  const { kept, dropped } = CodeLocate.groundDecisionPoints(FILES, [
    { title: "결제 분기", file: "src/pay.py", symbol: "def pay(order, method):", why_it_matters: "핵심" },
    { title: "환각", file: "src/pay.py", symbol: "def settle(order, gateway):" },
    { title: "짧은 심볼", file: "src/pay.py", symbol: "os" },
    { title: "없는 파일", file: "src/ghost.py", symbol: "def anything(x):" },
  ], OPTS);

  assert.strictEqual(kept.length, 1);
  assert.strictEqual(kept[0].title, "결제 분기");
  assert.strictEqual(kept[0].located.matchedLine, 3);
  assert.ok(kept[0].fragment.text.includes("def pay"));
  assert.strictEqual(kept[0].why_it_matters, "핵심", "원본 필드는 보존된다");

  assert.strictEqual(dropped.length, 3);
  assert.ok(dropped.every((d) => typeof d.reason === "string" && d.reason.length));
});

test("decision_points가 없거나 배열이 아니어도 깨지지 않는다", () => {
  for (const bad of [undefined, null, {}, "nope"]) {
    const { kept, dropped } = CodeLocate.groundDecisionPoints(FILES, bad, OPTS);
    assert.deepStrictEqual(kept, []);
    assert.deepStrictEqual(dropped, []);
  }
});

// ── D-ground1 §4: code_block 채우는 순서 ──────────────────────────────────────────
test("fan_in 내림차순으로 정렬되고 동점은 알파벳순으로 결정론적이다", () => {
  const files = { "src/z_core.py": "x", "src/a_helper.py": "y", "src/m_other.py": "z" };
  const fanIn = { "z_core.py": 7, "a_helper.py": 0, "m_other.py": 0 };
  assert.deepStrictEqual(
    CodeLocate.orderFilesByImportance(files, fanIn),
    ["src/z_core.py", "src/a_helper.py", "src/m_other.py"]
  );
  // 입력 순서를 바꿔도 같은 결과(재현성)
  const reversed = { "src/m_other.py": "z", "src/a_helper.py": "y", "src/z_core.py": "x" };
  assert.deepStrictEqual(
    CodeLocate.orderFilesByImportance(reversed, fanIn),
    ["src/z_core.py", "src/a_helper.py", "src/m_other.py"]
  );
});

test("fan_in이 없어도(예: Swift 등 구조 스캔 불가) 알파벳순으로 안전하게 떨어진다", () => {
  const files = { "b.swift": "x", "a.swift": "y" };
  assert.deepStrictEqual(CodeLocate.orderFilesByImportance(files, null), ["a.swift", "b.swift"]);
});
