// 제출물 크기/개수 상한 회귀 테스트 (2026-08-05, redteam audit M5).
//   실행: node --test tests/p02-engine-submission-caps.test.js   (저장소 루트에서)
const test = require("node:test");
const assert = require("node:assert");

// 최소 JSZip 스텁 -- 실제 jszip은 이 저장소의 의존성이 아니다(브라우저 전역으로만 로드).
// parseZipFile()이 실제로 쓰는 표면(zip.files의 {dir,name}과 entry.async("string"))만 흉내낸다.
function fakeZip(entries) {
  const files = {};
  for (const [name, content] of Object.entries(entries)) {
    files[name] = { dir: false, name, async: async () => content };
  }
  return { loadAsync: async () => ({ files }) };
}

globalThis.LabApp = { resolveParam: () => undefined };
const P02Engine = require("../shared/p02-engine.js");

test("parseZipFile(): rejects when file count exceeds the cap", async () => {
  const entries = {};
  for (let i = 0; i <= P02Engine.MAX_SUBMISSION_FILES; i++) entries[`f${i}.py`] = "x = 1\n";
  globalThis.JSZip = fakeZip(entries);
  try {
    await assert.rejects(() => P02Engine.parseZipFile(new Blob()), /파일 수가 상한/);
  } finally {
    delete globalThis.JSZip;
  }
});

test("parseZipFile(): rejects when total bytes exceed the cap even with few files", async () => {
  const big = "x".repeat(P02Engine.MAX_SUBMISSION_BYTES + 1);
  globalThis.JSZip = fakeZip({ "one_huge_file.py": big });
  try {
    await assert.rejects(() => P02Engine.parseZipFile(new Blob()), /총 용량이 상한/);
  } finally {
    delete globalThis.JSZip;
  }
});

test("parseZipFile(): a small submission is accepted normally", async () => {
  globalThis.JSZip = fakeZip({ "a.py": "def a():\n    pass\n", "b.py": "def b():\n    pass\n" });
  try {
    const result = await P02Engine.parseZipFile(new Blob());
    assert.equal(result.loadedCount, 2);
  } finally {
    delete globalThis.JSZip;
  }
});

test("fetchGithubRepo(): rejects when total bytes exceed the cap", async () => {
  const bigB64 = Buffer.from("x".repeat(P02Engine.MAX_SUBMISSION_BYTES + 1)).toString("base64");
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes("/git/trees/")) {
      return new Response(JSON.stringify({ tree: [{ type: "blob", path: "huge.py", url: "https://api.github.com/blob/1" }] }), { status: 200 });
    }
    if (u.includes("/blob/1")) {
      return new Response(JSON.stringify({ encoding: "base64", content: bigB64 }), { status: 200 });
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
  try {
    await assert.rejects(
      () => P02Engine.fetchGithubRepo("owner", "repo", "main", null, () => {}),
      /총 용량이 상한/
    );
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("writeTargetFiles(): rejects an oversized files map before touching Pyodide FS", () => {
  const files = {};
  for (let i = 0; i <= P02Engine.MAX_SUBMISSION_FILES; i++) files[`f${i}.py`] = "x = 1\n";
  assert.throws(() => P02Engine.writeTargetFiles(files), /파일 수가 상한/);
});
