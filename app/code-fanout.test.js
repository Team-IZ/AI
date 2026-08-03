// D-poc13 4단계(병렬 fan-out) 실행부의 순수 로직 테스트.
//   실행: node --test app/code-fanout.test.js   (저장소 루트에서)
//
// 1~3단계 테스트는 app/code-candidates.test.js에 그대로 있다 -- 이 파일은 그 뒤에 붙은
// 실행부(runWithConcurrency / deepDiveCandidate / mergeDeepDives / runFanout)만 다룬다.
//
// **네트워크를 절대 건드리지 않는다.** code-candidates.test.js가 grounding을 검증할 때
// 실제 CodeFragment.locateSymbol을 쓰고 목을 쓰지 않았던 것과 같은 이유로, 여기서도
// grounding/파편 추출은 실제 구현을 그대로 쓴다 -- 목으로 대체하는 건 POCStage.call
// (= 유일하게 네트워크를 타는 지점) 하나뿐이다. opts.call 주입구가 존재하는 이유가 이것이다.
const test = require("node:test");
const assert = require("node:assert");

globalThis.P02Engine = {
  findFileByBasename(files, base) {
    return Object.keys(files).find((p) => p.split("/").pop() === base) || null;
  },
};

const CodeFragment = require("./code-fragment.js");
const CC = require("./code-candidates.js");

const INJECT = { locate: CodeFragment.locateSymbol, extract: CodeFragment.extractFragment };

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
    "def audit(event):",
    "    print(event)",
  ].join("\n"),
};

const DOC = {
  overview: "결제 데모",
  decision_points: [
    { title: "결제 분기", file: "src/pay.py", symbol: "def pay(order, method):", why_it_matters: "분기 판단", related_teach: "t1" },
    { title: "환불", file: "src/pay.py", symbol: "def refund(order):", why_it_matters: "미구현 처리", related_teach: null },
    { title: "감사 로그", file: "src/util.py", symbol: "def audit(event):", why_it_matters: "관측 가능성", related_teach: "t1" },
  ],
};

const GOOD_ANSWER = {
  what_it_does: "카드 결제만 처리하고 나머지는 None을 돌려준다.",
  why_this_way: "분기를 문자열 비교로 두어 결제 수단 추가가 이 함수 수정으로만 가능하다.",
  alternatives: [{ approach: "수단별 핸들러 맵", tradeoff: "간접 참조가 늘어 추적이 어려워진다" }],
  failure_modes: ["method가 None이면 조용히 None을 반환해 호출부가 실패를 못 본다"],
};

/** POCStage.call 목. 호출 기록을 남기고, 응답/지연/실패를 시나리오별로 제어한다. */
function mockCall(behavior = () => GOOD_ANSWER) {
  const calls = [];
  const fn = async (stageId, values, opts) => {
    const seq = calls.length;
    calls.push({ stageId, values, opts });
    return behavior(values, seq);
  };
  fn.calls = calls;
  return fn;
}

const RATE_FREE = { count: 0, isServerWide: true, threshold: 40 };

// ── 동시성 러너 ────────────────────────────────────────────────────────────────
test("runWithConcurrency는 동시 실행 수가 limit을 넘지 않는다", async () => {
  let live = 0;
  let peak = 0;
  const items = [1, 2, 3, 4, 5];
  await CC.runWithConcurrency(items, 2, async (x) => {
    live++;
    peak = Math.max(peak, live);
    await new Promise((r) => setTimeout(r, 5));
    live--;
    return x;
  });
  assert.strictEqual(peak, 2, `동시 실행이 2를 넘었다(peak=${peak})`);
});

test("runWithConcurrency 결과는 입력과 인덱스가 정렬된다(완료 순서가 아니라)", async () => {
  // 앞 항목을 일부러 더 느리게 -- 완료 순서로 모으는 구현이면 여기서 순서가 뒤집힌다.
  const settled = await CC.runWithConcurrency([30, 1, 20, 2], 4, async (ms) => {
    await new Promise((r) => setTimeout(r, ms));
    return `done:${ms}`;
  });
  assert.deepStrictEqual(settled.map((s) => s.value), ["done:30", "done:1", "done:20", "done:2"]);
});

test("하나가 reject돼도 나머지 성공분이 버려지지 않는다(Promise.all 금지 계약)", async () => {
  const settled = await CC.runWithConcurrency([1, 2, 3], 2, async (x) => {
    if (x === 2) throw new Error("가운데가 터짐");
    return x * 10;
  });
  assert.deepStrictEqual(settled.map((s) => s.status), ["fulfilled", "rejected", "fulfilled"]);
  assert.strictEqual(settled[0].value, 10);
  assert.strictEqual(settled[2].value, 30);
  assert.match(settled[1].reason.message, /가운데가 터짐/);
});

test("전부 실패해도 예외를 던지지 않고 전부 rejected로 회수한다", async () => {
  const settled = await CC.runWithConcurrency([1, 2], 2, async () => { throw new Error("전부 실패"); });
  assert.strictEqual(settled.length, 2);
  assert.ok(settled.every((s) => s.status === "rejected"));
});

test("빈 목록/limit 과대 지정에도 깨지지 않는다", async () => {
  assert.deepStrictEqual(await CC.runWithConcurrency([], 4, async () => 1), []);
  const settled = await CC.runWithConcurrency([1], 99, async (x) => x);
  assert.deepStrictEqual(settled.map((s) => s.value), [1]);
});

// ── p04-1b 호출 래퍼 ───────────────────────────────────────────────────────────
test("deepDiveCandidate는 grounding에서 산정된 줄 범위로 파편을 만들어 p04-1b에 넘긴다", async () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", title: "결제 분기", why: "분기 판단" },
  ], INJECT);
  const call = mockCall();
  const dive = await CC.deepDiveCandidate(c, { files: FILES, model: "m", call, extract: INJECT.extract });

  assert.strictEqual(call.calls.length, 1);
  const [{ stageId, values }] = call.calls;
  assert.strictEqual(stageId, "p04-1b");
  assert.match(values.code_ref, /^src\/pay\.py:3-6$/); // 우리가 산정한 줄, LLM이 센 값이 아니다
  assert.ok(values.code_block.includes("def pay(order, method):"), "실제 소스 파편이 들어가야 한다");
  assert.strictEqual(values.why_it_matters, "분기 판단");
  assert.strictEqual(values.title, "결제 분기");
  assert.strictEqual(dive.what_it_does, GOOD_ANSWER.what_it_does);
  assert.strictEqual(dive.ref, "src/pay.py:3-6");
});

test("why_it_matters가 비어도 프롬프트에 빈 칸이 남지 않는다", async () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "structural", file: "src/util.py", symbol: "def audit(event):", title: "", why: "" },
  ], INJECT);
  const call = mockCall();
  await CC.deepDiveCandidate(c, { files: FILES, call, extract: INJECT.extract });
  assert.match(call.calls[0].values.why_it_matters, /설명 없음/);
  assert.strictEqual(call.calls[0].values.title, "(제목 없음)");
});

test("응답이 비면 성공으로 위장하지 않고 던진다(실패 = 오늘 렌더로 degrade)", async () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):" },
  ], INJECT);
  const call = mockCall(() => ({ what_it_does: "  ", alternatives: [], failure_modes: [] }));
  await assert.rejects(
    () => CC.deepDiveCandidate(c, { files: FILES, call, extract: INJECT.extract }),
    /내용이 없음/
  );
});

test("망가진 응답 형태(문자열 대신 객체, 배열 아닌 필드)는 경계에서 정규화된다", async () => {
  const [c] = CC.groundCandidates(FILES, [
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):" },
  ], INJECT);
  const call = mockCall(() => ({
    what_it_does: "정상",
    alternatives: "배열이 아님",
    failure_modes: ["ok", "", null, "b", "c", "d", "e"],
  }));
  const dive = await CC.deepDiveCandidate(c, { files: FILES, call, extract: INJECT.extract });
  assert.deepStrictEqual(dive.alternatives, []);
  assert.deepStrictEqual(dive.failure_modes, ["ok", "b", "c", "d"]); // 빈값 제거 + 최대 4개
  assert.strictEqual(dive.why_this_way, "");
});

test("파편을 만들 수 없으면 LLM을 부르지 않고 던진다(예산 낭비 방지)", async () => {
  const call = mockCall();
  await assert.rejects(
    () => CC.deepDiveCandidate(
      { file: "src/nope.py", symbol: "def ghost():" },
      { files: FILES, call, extract: INJECT.extract }
    ),
    /코드 파편을 만들 수 없음/
  );
  assert.strictEqual(call.calls.length, 0);
});

// ── decision_points 병합 ───────────────────────────────────────────────────────
test("성공분은 dp_index가 가리키는 항목에 정확히 붙는다", () => {
  const dps = DOC.decision_points;
  const picked = [
    { title: "감사 로그", meta: { dp_index: 2 } },
    { title: "결제 분기", meta: { dp_index: 0 } },
  ];
  const settled = [
    { status: "fulfilled", value: { what_it_does: "감사" } },
    { status: "fulfilled", value: { what_it_does: "결제" } },
  ];
  const r = CC.mergeDeepDives(dps, picked, settled);
  assert.strictEqual(r.attached, 2);
  assert.strictEqual(r.decision_points[2].deep_dive.what_it_does, "감사");
  assert.strictEqual(r.decision_points[0].deep_dive.what_it_does, "결제");
  assert.ok(!("deep_dive" in r.decision_points[1]), "손대지 않은 항목엔 키 자체가 없어야 한다");
});

test("실패한 항목에는 deep_dive 키가 아예 생기지 않는다(undefined도 넣지 않는다)", () => {
  const picked = [{ title: "결제 분기", meta: { dp_index: 0 } }];
  const settled = [{ status: "rejected", reason: new Error("타임아웃") }];
  const r = CC.mergeDeepDives(DOC.decision_points, picked, settled);
  assert.strictEqual(r.attached, 0);
  assert.strictEqual(Object.prototype.hasOwnProperty.call(r.decision_points[0], "deep_dive"), false);
  assert.deepStrictEqual(r.failed, [{ label: "결제 분기", reason: "타임아웃" }]);
  // 오늘의 출력과 동일해야 한다는 신뢰성 계약 -- 직렬화 결과까지 같은지로 확인한다.
  assert.strictEqual(JSON.stringify(r.decision_points), JSON.stringify(DOC.decision_points));
});

test("mergeDeepDives는 입력을 변형하지 않는다", () => {
  const dps = [{ title: "a" }, { title: "b" }];
  const before = JSON.stringify(dps);
  CC.mergeDeepDives(dps, [{ meta: { dp_index: 1 } }], [{ status: "fulfilled", value: { what_it_does: "x" } }]);
  assert.strictEqual(JSON.stringify(dps), before);
});

test("범위를 벗어난 dp_index는 조용히 엉뚱한 항목에 붙지 않는다", () => {
  const r = CC.mergeDeepDives(DOC.decision_points, [{ title: "유령", meta: { dp_index: 99 } }],
    [{ status: "fulfilled", value: { what_it_does: "x" } }]);
  assert.strictEqual(r.attached, 0);
  assert.strictEqual(r.unattached.length, 1);
  assert.ok(r.decision_points.every((d) => !("deep_dive" in d)));
});

// ── 전체 fan-out ───────────────────────────────────────────────────────────────
test("runFanout: 여유가 있으면 상위 K개에 p04-1b가 K번 돈다", async () => {
  const call = mockCall();
  const r = await CC.runFanout(
    { analysisDoc: DOC, files: FILES, fanIn: { "pay.py": 5, "util.py": 1 }, teachIds: ["t1"], k: 3 },
    { ...INJECT, call, getRate: async () => RATE_FREE }
  );
  assert.strictEqual(r.plan.k, 3);
  assert.strictEqual(call.calls.length, 3);
  assert.ok(call.calls.every((c) => c.stageId === "p04-1b"));
  assert.strictEqual(r.attached, 3);
  assert.ok(r.decision_points.every((d) => d.deep_dive), "세 항목 모두 deep_dive가 붙어야 한다");
});

test("runFanout: 트래픽 여유가 없으면 LLM을 한 번도 부르지 않고 오늘 출력 그대로 돌려준다", async () => {
  const call = mockCall();
  const r = await CC.runFanout(
    { analysisDoc: DOC, files: FILES, k: 3 },
    { ...INJECT, call, getRate: async () => ({ count: 39, isServerWide: true, threshold: 40 }) }
  );
  assert.strictEqual(call.calls.length, 0, "K=0인데 호출이 나갔다");
  assert.strictEqual(r.attached, 0);
  assert.match(r.skipped, /생략/);
  assert.strictEqual(JSON.stringify(r.decision_points), JSON.stringify(DOC.decision_points));
});

test("runFanout: 일부 실패해도 성공분은 살아남고 실패분은 오늘 렌더로 남는다", async () => {
  // 두 번째 호출만 터뜨린다 -- 관측된 NVIDIA 무응답(p=0.1, K=3에서 27%)이 예외가 아니라
  // 기본값이라는 4단계 COST(b)의 정상 경로 검증.
  const call = mockCall((_values, seq) => {
    if (seq === 1) throw new Error("504 무응답");
    return GOOD_ANSWER;
  });
  const r = await CC.runFanout(
    { analysisDoc: DOC, files: FILES, k: 3 },
    { ...INJECT, call, getRate: async () => RATE_FREE }
  );
  assert.strictEqual(call.calls.length, 3);
  assert.strictEqual(r.attached, 2);
  assert.strictEqual(r.failed.length, 1);
  const withDive = r.decision_points.filter((d) => "deep_dive" in d);
  assert.strictEqual(withDive.length, 2);
});

test("runFanout: decision_point에 연결되지 않은 후보(구조/finding 단독)에는 호출을 쓰지 않는다", async () => {
  // util.py의 fan_in을 최고로 올려 구조 후보가 랭킹 1위가 되게 만든다. 그래도 그 후보는
  // 붙일 자리(deep_dive)가 없으므로 예산을 쓰면 안 된다.
  const call = mockCall();
  const soloDoc = { decision_points: [DOC.decision_points[0]] };
  const r = await CC.runFanout(
    { analysisDoc: soloDoc, files: FILES, fanIn: { "util.py": 99, "pay.py": 1 }, k: 3 },
    { ...INJECT, call, getRate: async () => RATE_FREE }
  );
  assert.strictEqual(call.calls.length, 1, "decision_point 1개니까 호출도 1번이어야 한다");
  assert.match(call.calls[0].values.code_ref, /^src\/pay\.py/);
  assert.strictEqual(r.attached, 1);
});

test("runFanout: 구조 신호(fan_in)가 '어느 decision_point를 깊게 볼지'를 실제로 바꾼다", async () => {
  // 예산이 모자란 상황(K=1)에서 세 decision_point 중 하나만 고르게 하고, 구조 신호만
  // 뒤집어 본다. 랭킹이 장식이 아니라면 고르는 항목이 달라져야 한다.
  //   DOC[0](pay.py:3)은 구조 후보와 같은 줄이라 agreement 가점을 받아 어느 쪽이든 1위다 --
  //   그래서 여기서는 agreement 가점이 없는 DOC[1](pay.py:8) vs DOC[2](util.py:4)로 비교한다.
  //   (agreement 자체의 효과는 아래 별도 테스트에서 확인한다.)
  const twoDoc = { decision_points: [DOC.decision_points[1], DOC.decision_points[2]] };
  const pickWith = async (fanIn) => {
    const call = mockCall();
    await CC.runFanout({ analysisDoc: twoDoc, files: FILES, fanIn, k: 1 },
      { ...INJECT, call, getRate: async () => RATE_FREE });
    return call.calls[0].values.code_ref;
  };
  assert.match(await pickWith({ "pay.py": 9, "util.py": 1 }), /^src\/pay\.py:8/);
  assert.match(await pickWith({ "pay.py": 1, "util.py": 9 }), /^src\/util\.py:4/);
});

test("runFanout: 구조 후보가 같은 줄을 지목하면(agreement) 그 decision_point가 예산을 먼저 받는다", async () => {
  // 구조 신호가 pay.py를 **가장 낮게** 보는 상황인데도, 구조 후보의 심볼이 DOC[0]과 같은
  // 줄(pay.py:3)이라 agreement 항이 켜져 1위를 유지한다. 세 소스를 섞는 이유가 이것이다 --
  // "두 소스가 독립적으로 같은 곳을 찍었다" 자체가 신호다(1단계 WHY).
  const call = mockCall();
  await CC.runFanout(
    { analysisDoc: DOC, files: FILES, fanIn: { "pay.py": 1, "util.py": 9 }, k: 1 },
    { ...INJECT, call, getRate: async () => RATE_FREE }
  );
  assert.match(call.calls[0].values.code_ref, /^src\/pay\.py:3/);
});

test("runFanout: 트래픽을 못 읽어도(프록시 없음) 낙관하지 않고 계속 진행한다", async () => {
  const call = mockCall();
  const r = await CC.runFanout(
    { analysisDoc: DOC, files: FILES, k: 5 },
    { ...INJECT, call, getRate: async () => { throw new Error("프록시 미설정"); } }
  );
  assert.strictEqual(r.plan.concurrency, 1);
  assert.ok(r.plan.k <= CC.FANOUT_CONCURRENCY, "못 읽을 땐 K도 보수적으로 깎여야 한다");
  assert.strictEqual(call.calls.length, r.plan.k);
});

test("runFanout: grounding에 전부 실패하면 호출 없이 원본을 돌려준다", async () => {
  const call = mockCall();
  const ghostDoc = { decision_points: [{ title: "환각", file: "src/pay.py", symbol: "def ghost(x):" }] };
  const r = await CC.runFanout({ analysisDoc: ghostDoc, files: FILES, k: 3 },
    { ...INJECT, call, getRate: async () => RATE_FREE });
  assert.strictEqual(call.calls.length, 0);
  assert.strictEqual(JSON.stringify(r.decision_points), JSON.stringify(ghostDoc.decision_points));
});

test("runFanout: decision_points가 없거나 배열이 아니어도 깨지지 않는다", async () => {
  const call = mockCall();
  for (const doc of [{}, { decision_points: null }, { decision_points: [] }]) {
    const r = await CC.runFanout({ analysisDoc: doc, files: FILES }, { ...INJECT, call, getRate: async () => RATE_FREE });
    assert.deepStrictEqual(r.decision_points, []);
  }
  assert.strictEqual(call.calls.length, 0);
});

test("runFanout: 진행 로그는 남기되 개별 실패로 예외를 던지지 않는다", async () => {
  const msgs = [];
  const call = mockCall(() => { throw new Error("전부 실패"); });
  const r = await CC.runFanout(
    { analysisDoc: DOC, files: FILES, k: 2, onProgress: (m) => msgs.push(m) },
    { ...INJECT, call, getRate: async () => RATE_FREE }
  );
  assert.strictEqual(r.attached, 0);
  assert.ok(msgs.some((m) => m.includes("⚠") && m.includes("실패")), "실패를 숨기지 않아야 한다");
  assert.ok(msgs.some((m) => m.includes("심층 분석")), "진행 로그에 단계 이름이 있어야 한다");
});

// ── 1단계와의 연결고리 ─────────────────────────────────────────────────────────
test("collectCandidates가 llm 후보에 dp_index를 남긴다(병합의 유일한 연결고리)", () => {
  const cands = CC.collectCandidates({ analysisDoc: DOC, files: FILES });
  assert.deepStrictEqual(cands.map((c) => c.meta.dp_index), [0, 1, 2]);
});

test("여러 소스가 합쳐져도 dp_index가 살아남는다", () => {
  const cands = [
    { source: "finding", file: "src/pay.py", symbol: "def pay(order, method):", meta: { finding_rank_score: 9 } },
    { source: "llm", file: "src/pay.py", symbol: "def pay(order, method):", meta: { dp_index: 0 } },
  ];
  const ranked = CC.rankCandidates(CC.groundCandidates(FILES, cands, INJECT));
  assert.strictEqual(ranked.length, 1);
  assert.strictEqual(ranked[0].meta.dp_index, 0);
  assert.strictEqual(ranked[0].rank_evidence.terms.agreement, 1);
});
