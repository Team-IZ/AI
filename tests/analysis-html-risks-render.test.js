// risks[] 렌더링 정적 회귀 테스트 (2026-08-05, redteam audit M4).
//   실행: node --test tests/analysis-html-risks-render.test.js   (저장소 루트에서)
//
// analysis.html은 DOM 렌더링이라 이 저장소에 DOM 테스트 환경(jsdom 등)이 없다 --
// 여기서는 "risks[]를 실제로 소비하는 코드가 조용히 사라지지 않았는지"만 고정한다.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "..", "app", "analysis.html"), "utf-8");

test("doc-risks element exists in the results card", () => {
  assert.match(html, /<div id="doc-risks"/);
});

test("renderResults() reads analysisDoc.risks and writes into #doc-risks", () => {
  assert.match(html, /analysis\.analysisDoc\.risks/);
  assert.match(html, /\$\("doc-risks"\)\.innerHTML/);
});
