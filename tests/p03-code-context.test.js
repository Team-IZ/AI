// shared/p03-engine.js::buildCombinedCodeContext(contexts, cap, decisionPoint)의 테스트
// (D-ground2 -- p02-6의 decision point로 "파일 앞부분" 대신 "근거 줄 주변"을 잘라 넣는 배선).
//   실행: node --test tests/p03-code-context.test.js   (저장소 루트에서)
//
// tests/code-locate.test.js와 같은 원칙: **실제 구현을 그대로 require해서** 검증한다.
// p03-engine.js는 그러라고 파일 맨 끝에 module.exports 가드가 붙어 있고, 그 안에서
// CodeLocate도 (브라우저 전역이 없으면) 실제 shared/code-locate.js를 require한다 --
// 조각 추출 규칙을 이 테스트에 복제하지 않기 위해서다.
//
// 이 파일이 지키는 가장 중요한 계약은 (a)다: decision point가 없을 때 오늘과 **한 글자도**
// 다르지 않아야 한다. grounding 데이터가 쌓이기 전까지 그게 정상 경로이므로, 이 변경은
// 순수 가산이어야 하고 그 사실이 회귀로 고정돼 있어야 한다.
const test = require("node:test");
const assert = require("node:assert");

const P03Engine = require("../shared/p03-engine.js");
const CodeLocate = require("../shared/code-locate.js");

const { buildCombinedCodeContext } = P03Engine;

// 논점이 파일 **뒤쪽**에 있는 파일 -- D-poc13이 지적한 실패 모드를 그대로 재현하려면
// 앞부분 슬라이스에 안 걸리는 위치여야 한다.
const PAY_LINES = [
  "import os",                        // 1
  "import json",                      // 2
  "",                                 // 3
  "HEADER = 'x' * 10",                // 4
  "",                                 // 5
  "def unrelated_a():",               // 6
  "    return 1",                     // 7
  "",                                 // 8
  "def unrelated_b():",               // 9
  "    return 2",                     // 10
  "",                                 // 11
  "def settle_payment(order, gw):",   // 12  <- 진짜 논점
  "    if order.total > 0:",          // 13
  "        return gw.charge(order)",  // 14
  "    return None",                  // 15
];
const PAY = PAY_LINES.join("\n");
const UTIL = ["def helper(x):", "    return x + 1", "", "def other(y):", "    return y - 1"].join("\n");

const CONTEXTS = [
  { path: "src/pay.py", content: PAY },
  { path: "src/util.py", content: UTIL },
];

// submission.html이 저장하는 것과 같은 모양: {title, file, symbol, why_it_matters, located}
// -- located는 CodeLocate.locateSymbol이 **실제 파일과 대조해 산정한** 결과이지 손으로 쓴
// 줄 번호가 아니다(D-ground1 §2). 여기서도 그 실제 함수로 만든다.
const RESOLVER = (files, basename) => {
  const c = Object.keys(files).filter((p) => p.split("/").pop() === basename);
  if (!c.length) return null;
  c.sort((a, b) => a.length - b.length || a.localeCompare(b));
  return c[0];
};
// 파일 내용 자체에 빈 줄("\n\n")이 있어서 결과를 "\n\n"으로 단순 split하면 섹션이 쪼개진다
// -- 헤더 위치로 잘라낸다.
function sectionFor(out, path) {
  const start = out.indexOf(`--- ${path}`);
  assert.ok(start >= 0, `${path} 섹션이 있어야 한다`);
  const next = out.indexOf("\n\n--- ", start + 1);
  return next === -1 ? out.slice(start) : out.slice(start, next);
}
function bodyOf(section) {
  return section.slice(section.indexOf("\n") + 1);
}

function makeDecisionPoint(file, symbol) {
  const located = CodeLocate.locateSymbol({ "src/pay.py": PAY, "src/util.py": UTIL }, file, symbol, {
    resolveByBasename: RESOLVER,
  });
  return { title: "결제 정산 분기", file, symbol, why_it_matters: "게이트웨이 호출 조건", located };
}

// ── (a) 회귀 고정: decisionPoint 인자가 없으면 오늘과 완전히 동일 ──────────────────
test("(a) decisionPoint 없이 부르면 기존 앞부분 슬라이스 동작 그대로다", () => {
  const cap = 120;
  const perFileCap = Math.floor(cap / CONTEXTS.length);
  const expected = [
    `--- src/pay.py ---\n${PAY.slice(0, perFileCap)}`,
    `--- src/util.py ---\n${UTIL.slice(0, perFileCap)}`,
  ].join("\n\n");
  assert.strictEqual(buildCombinedCodeContext(CONTEXTS, cap), expected);
});

test("(a) cap이 없으면 파일 내용을 통째로 넣는다(기존 동작)", () => {
  assert.strictEqual(
    buildCombinedCodeContext(CONTEXTS, null),
    `--- src/pay.py ---\n${PAY}\n\n--- src/util.py ---\n${UTIL}`
  );
});

test("(a) contexts가 비었으면 null (기존 동작)", () => {
  assert.strictEqual(buildCombinedCodeContext([], 100), null);
  assert.strictEqual(buildCombinedCodeContext(null, 100), null);
  assert.strictEqual(buildCombinedCodeContext(undefined, 100, makeDecisionPoint("src/pay.py", "def settle_payment(order, gw):")), null);
});

// ── (b) 매칭되면 그 파일만 근거 줄 주변에서 잘린다 ────────────────────────────────
test("(b) 매칭된 파일은 앞이 아니라 located 주변에서 잘린다", () => {
  const dp = makeDecisionPoint("src/pay.py", "def settle_payment(order, gw):");
  assert.strictEqual(dp.located.valid, true);
  assert.strictEqual(dp.located.matchedLine, 12, "줄 번호는 문자열 매치로 산정된 사실이어야 한다");

  const out = buildCombinedCodeContext(CONTEXTS, 400, dp);

  // 논점(12행)과 그 블록이 들어왔다 -- 앞부분 슬라이스였다면 절대 못 들어올 위치다.
  assert.ok(out.includes("def settle_payment(order, gw):"), "근거 줄이 포함돼야 한다");
  assert.ok(out.includes("        return gw.charge(order)"), "블록 본문도 포함돼야 한다");
  // 파일 맨 앞(1행)은 이 조각(±2줄 문맥 = 10행부터)에 포함되지 않는다.
  assert.ok(!out.includes("import json"), "더는 파일 앞부분을 넣지 않는다");
  // 조각 텍스트는 지어낸 게 아니라 실제 파일에서 그대로 떼어온 것이어야 한다.
  const frag = CodeLocate.buildFragment({ "src/pay.py": PAY }, dp.located);
  assert.ok(PAY.includes(frag.text));
  assert.ok(out.includes(frag.text));
});

test("(b) 같은 호출의 매칭 안 된 형제 파일은 기존 앞부분 슬라이스 그대로다", () => {
  const dp = makeDecisionPoint("src/pay.py", "def settle_payment(order, gw):");
  const cap = 400;
  const perFileCap = Math.floor(cap / CONTEXTS.length);
  const out = buildCombinedCodeContext(CONTEXTS, cap, dp);

  assert.strictEqual(sectionFor(out, "src/util.py"), `--- src/util.py ---\n${UTIL.slice(0, perFileCap)}`,
    "매칭되지 않은 파일의 헤더/내용/자르는 방식이 하나도 바뀌면 안 된다");
});

test("(b) 발췌한 파일의 헤더는 발췌라는 사실과 행 범위를 정직하게 표시한다", () => {
  const dp = makeDecisionPoint("src/pay.py", "def settle_payment(order, gw):");
  const frag = CodeLocate.buildFragment({ "src/pay.py": PAY }, dp.located);
  const out = buildCombinedCodeContext(CONTEXTS, 400, dp);
  assert.ok(out.includes(`--- src/pay.py (${frag.contextLines[0]}-${frag.contextLines[1]}행 발췌`),
    "파일 전체가 아니라 일부라는 사실이 프롬프트에 드러나야 한다");
});

test("(b) 발췌도 perFileCap 예산을 그대로 지킨다(D-fix10 규율 유지)", () => {
  const dp = makeDecisionPoint("src/pay.py", "def settle_payment(order, gw):");
  const cap = 120; // perFileCap = 60
  const frag = CodeLocate.buildFragment({ "src/pay.py": PAY }, dp.located);
  assert.ok(frag.text.length > 60, "이 픽스처에서 조각은 예산보다 길다(=자름이 실제로 일어난다)");
  const body = bodyOf(sectionFor(buildCombinedCodeContext(CONTEXTS, cap, dp), "src/pay.py"));
  assert.strictEqual(body.length, 60, "grounding 여부와 무관하게 perFileCap 예산은 동일하게 적용된다");
  assert.strictEqual(body, frag.text.slice(0, 60));
  // 앞에서 자르더라도 조각의 맨 앞은 근거 줄 바로 위 문맥이라 논점이 살아남는다 --
  // 파일 앞에서 자르던 기존 동작(1행부터)과 방향이 반대다.
  assert.ok(body.includes("def settle_payment(order, gw):"), "예산을 줄여도 근거 줄은 남아야 한다");
});

// ── (c) 폴백: 못 믿을 위치 / 매칭 실패는 기존 동작 그대로 ─────────────────────────
test("(c) located.valid가 false면 grounding을 쓰지 않고 기존 동작으로 떨어진다", () => {
  // 실제 파일에 없는 심볼 -> locateSymbol이 valid:false를 준다(D-ground1 §6).
  const dp = makeDecisionPoint("src/pay.py", "def hallucinated_handler(req):");
  assert.strictEqual(dp.located.valid, false);
  assert.strictEqual(
    buildCombinedCodeContext(CONTEXTS, 120, dp),
    buildCombinedCodeContext(CONTEXTS, 120),
    "무효 위치는 없는 것과 완전히 같아야 한다"
  );
});

test("(c) file이 이번 contexts의 어느 파일과도 매칭되지 않으면 기존 동작 그대로다", () => {
  // 위치 자체는 유효하지만(util.py의 실제 한 줄) 이번 호출의 contexts에는 그 파일이 없다.
  const dp = makeDecisionPoint("src/util.py", "def helper(x):");
  assert.strictEqual(dp.located.valid, true);
  const onlyPay = [{ path: "src/pay.py", content: PAY }];
  assert.strictEqual(
    buildCombinedCodeContext(onlyPay, 120, dp),
    buildCombinedCodeContext(onlyPay, 120)
  );
});

test("(c) decisionPoint가 null/undefined/located 없음이어도 깨지지 않고 기존 동작", () => {
  const baseline = buildCombinedCodeContext(CONTEXTS, 120);
  for (const bad of [null, undefined, {}, { file: "src/pay.py" }, { file: "src/pay.py", located: null }]) {
    assert.strictEqual(buildCombinedCodeContext(CONTEXTS, 120, bad), baseline);
  }
});

// ── (d) D-ground2b: dp.file 표기(전체 경로 vs 베이스네임)가 c.path와 달라도 매칭된다 ──
// p02-6의 LLM이 code_block(전체 경로 헤더)을 보고 dp.file을 "pay.py"(베이스네임)로만
// 돌려준 실제 사례를 재현 -- 고치기 전에는 dp.file === c.path("src/pay.py")가 거짓이라
// grounding이 조용히 안 걸렸다(항상 폴백). 베이스네임 비교로 고친 뒤에는 걸려야 한다.
test("(d) dp.file이 베이스네임뿐이어도 하위 디렉터리 c.path와 매칭된다", () => {
  const located = CodeLocate.locateSymbol({ "src/pay.py": PAY, "src/util.py": UTIL }, "pay.py", "def settle_payment(order, gw):", {
    resolveByBasename: RESOLVER,
  });
  assert.strictEqual(located.valid, true);
  const dp = { title: "결제 정산 분기", file: "pay.py", symbol: "def settle_payment(order, gw):", why_it_matters: "게이트웨이 호출 조건", located };

  const out = buildCombinedCodeContext(CONTEXTS, 400, dp);
  assert.ok(out.includes("def settle_payment(order, gw):"), "베이스네임만 있는 dp.file도 그라운딩이 걸려야 한다");
  assert.ok(!out.includes("import json"), "여전히 파일 앞부분이 아니라 근거 줄 주변이어야 한다");
});
