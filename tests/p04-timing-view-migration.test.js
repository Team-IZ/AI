// p04_timing_view 마이그레이션 정적 회귀 테스트 (2026-08-04, redteam audit H6).
//   실행: node --test tests/p04-timing-view-migration.test.js   (저장소 루트에서)
//
// 이 뷰는 라이브 Supabase에만 존재한다 -- 이 SQL 파일을 고쳐도 실제 배포본은 바뀌지
// 않는다(파일 자체의 주석 참고). 그래서 여기서 확인할 수 있는 건 "마이그레이션 파일이
// 의도한 형태로 남아있는가"뿐이고, 라이브 반영 여부는 별도로 확인해야 한다.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const sql = fs.readFileSync(
  path.join(__dirname, "..", "db", "migrations", "p04_timing_schema.sql"),
  "utf-8"
);

test("p04_timing_view no longer selects members.email", () => {
  const viewMatch = sql.match(/create or replace view public\.p04_timing_view[\s\S]*?;/);
  assert.ok(viewMatch, "p04_timing_view definition not found");
  assert.doesNotMatch(viewMatch[0], /\bm\.email\b/);
  assert.doesNotMatch(viewMatch[0], /left join public\.members/);
});

test("p04_timing_view is declared with security_invoker = true", () => {
  assert.match(sql, /create or replace view public\.p04_timing_view\s*\nwith \(security_invoker = true\)/);
});

test("migration file flags that applying it locally does not update the live database", () => {
  assert.match(sql, /고쳐도 이미 배포된 라이브 Supabase의 뷰는 바뀌지 않는다/);
});
