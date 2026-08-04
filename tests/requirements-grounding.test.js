// Requirements.judge() 근거 검증(grounding) 회귀 테스트 (2026-08-04, redteam audit H4).
//   실행: node --test tests/requirements-grounding.test.js   (저장소 루트에서)
//
// decision_points/topics는 이미 CodeFragment.extractFragment로 실제 파일과 대조하는데,
// 요구사항 P/F 판정만 모델의 evidence를 무검증으로 채택했다 -- 이 테스트는 실제
// Requirements.judge()를 그대로 불러 쓰고(로직을 복제하지 않음), POCStage.call만 스텁으로
// 채워 LLM 응답을 통제한다.
const test = require("node:test");
const assert = require("node:assert");

globalThis.P02Engine = {
  findFileByBasename(files, base) {
    return Object.keys(files).find((p) => p.split("/").pop() === base) || null;
  },
};
globalThis.CodeFragment = require("../app/stage2-analysis/code-fragment.js");
globalThis.LabApp = {
  getManifest() {
    return { pipelines: { p04: { stages: [{ id: "p04-2", truncation: { code_block: 12000 } }] } } };
  },
};

const FILES = {
  "src/payment.py": "def process_payment(order):\n    if order.total <= 0:\n        raise ValueError('invalid amount')\n    return charge(order)\n",
};

function stubPOCStage(results) {
  globalThis.POCStage = { call: async () => ({ results }) };
}

const Requirements = require("../app/stage2-analysis/requirements.js");

test("verdict=P with evidence grounded in the actual file stays P", async () => {
  stubPOCStage([{
    verdict: "P",
    evidence: { file: "src/payment.py", symbol: "if order.total <= 0:" },
    note: "",
  }]);
  const results = await Requirements.judge(["금액 검증이 있어야 한다"], FILES, {});
  assert.equal(results[0].verdict, "P");
});

test("verdict=P with a fabricated symbol not present in the file is downgraded to F", async () => {
  stubPOCStage([{
    verdict: "P",
    evidence: { file: "src/payment.py", symbol: "if order.total < MINIMUM_CHARGE_THRESHOLD:" },
    note: "",
  }]);
  const results = await Requirements.judge(["금액 검증이 있어야 한다"], FILES, {});
  assert.equal(results[0].verdict, "F");
  assert.match(results[0].note, /근거 코드를 확인할 수 없어 F로 강등/);
});

test("verdict=P pointing at a file that was never submitted is downgraded to F", async () => {
  stubPOCStage([{
    verdict: "P",
    evidence: { file: "src/nonexistent.py", symbol: "def process_payment(order):" },
    note: "",
  }]);
  const results = await Requirements.judge(["금액 검증이 있어야 한다"], FILES, {});
  assert.equal(results[0].verdict, "F");
});

test("verdict=F is unaffected by evidence grounding (only P is checked)", async () => {
  stubPOCStage([{ verdict: "F", evidence: null, note: "구현 없음" }]);
  const results = await Requirements.judge(["존재하지 않는 요구사항"], FILES, {});
  assert.equal(results[0].verdict, "F");
  assert.equal(results[0].note, "구현 없음");
});

test("a missing model result is still reported as a failed judgement (pre-existing behavior)", async () => {
  stubPOCStage([]);
  const results = await Requirements.judge(["요구사항 A"], FILES, {});
  assert.equal(results[0].verdict, "F");
  assert.match(results[0].note, /판정을 반환하지 않음/);
});
