// pdf_analysis_units_view 정적 회귀 테스트 (2026-08-04, redteam audit H6 companion,
// poc_full의 p04_timing_view 수정 중 교차검증으로 발견).
//   실행: node --test db/02_pdf_analysis.test.js   (저장소 루트에서)
//
// 이 뷰는 라이브 Supabase에만 존재한다 -- 이 SQL 파일을 고쳐도 실제 배포본은 바뀌지
// 않는다. 여기서 확인할 수 있는 건 파일 내용뿐이고, 라이브 반영은 별도로 확인해야 한다.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const sql = readFileSync(path.join(__dirname, "02_pdf_analysis.sql"), "utf-8");

test("pdf_analysis_units_view is declared with security_invoker = true", () => {
  assert.match(sql, /create view public\.pdf_analysis_units_view\s*\nwith \(security_invoker = true\)/);
});
