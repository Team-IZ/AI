// makeUnitMap()/normalizeUnitMap() prototype-pollution-key regression test
// (2026-08-04, redteam audit H3).
//
// index.js can't be imported directly in plain Node -- it pulls in `DurableObject` from
// `cloudflare:workers`, a Workers-runtime-only module. Its makeUnitMap()/normalizeUnitMap()
// are pure functions with no external dependencies (no `window`, no `LabApp` on the path
// exercised here), so this extracts their literal source text from both this file and the
// browser twin they were ported from/to (docs/lab/p01-runner.js), writes each extract to a
// throwaway .mjs file, and imports it as a real ES module -- testing the actual shipped
// source, not a reimplementation, for both copies the fix touched, without resorting to
// new Function()/eval() (both files here are our own repo source, not untrusted input, but
// a real module import avoids the code-injection *pattern* entirely rather than relying on
// that distinction).
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const scratchDir = mkdtempSync(path.join(tmpdir(), "p01-unitmap-test-"));
test.after(() => rmSync(scratchDir, { recursive: true, force: true }));

function extractFunction(source, name) {
  let start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} not found`);
  // Include a preceding "async " if this is an async function -- extracting from the
  // `function` keyword alone silently drops it, leaving `await` inside a non-async
  // function (a real SyntaxError when the extracted text is imported as a module).
  if (source.slice(Math.max(0, start - 6), start) === "async ") start -= 6;

  // Skip the parameter list with its OWN paren-depth counter before brace-counting the
  // body -- a default value like `init = {}` is a balanced {}-pair that closes and
  // reopens depth 0 while still inside the parameter list, which a body-brace counter
  // starting from `start` would misread as the function body opening AND closing.
  let i = source.indexOf("(", start);
  let parenDepth = 0;
  for (; i < source.length; i++) {
    if (source[i] === "(") parenDepth++;
    else if (source[i] === ")") {
      parenDepth--;
      if (parenDepth === 0) { i++; break; }
    }
  }
  const bodyStart = source.indexOf("{", i);
  let depth = 0;
  for (let j = bodyStart; j < source.length; j++) {
    if (source[j] === "{") {
      depth++;
    } else if (source[j] === "}") {
      depth--;
      if (depth === 0) return source.slice(start, j + 1);
    }
  }
  throw new Error(`unbalanced braces extracting ${name}`);
}

let moduleCounter = 0;

async function loadBothFunctions(sourceFilePath) {
  const source = readFileSync(sourceFilePath, "utf-8");
  const makeUnitMapSrc = extractFunction(source, "makeUnitMap");
  const normalizeUnitMapSrc = extractFunction(source, "normalizeUnitMap");
  const moduleFile = path.join(scratchDir, `extracted-${moduleCounter++}.mjs`);
  writeFileSync(
    moduleFile,
    `${makeUnitMapSrc}\n${normalizeUnitMapSrc}\nexport { makeUnitMap, normalizeUnitMap };\n`
  );
  // Safe: moduleFile is a path this function just wrote itself (mkdtempSync'd dir, this
  // process's own PID/counter in the name), never derived from network/user input, and its
  // contents are extracted verbatim from sourceFilePath (a path hardcoded in TARGETS below,
  // this repo's own committed source) -- not from any argument this test file accepts
  // externally. Nothing here is attacker-reachable.
  return import(pathToFileURL(moduleFile).href);
}

const TARGETS = {
  "server (services/p01-orchestrator/index.js)": path.join(__dirname, "index.js"),
  "browser (docs/lab/p01-runner.js)": path.join(__dirname, "..", "..", "docs", "lab", "p01-runner.js"),
};

// isAllowedProxyUrl() regression test (2026-08-04, redteam audit H1, revisited).
// A first attempt at this fix hardcoded a single proxyUrl -- reverted because it broke
// the documented self-hosted-Worker feature (config.js's DEFAULT_PROXY_URL comment).
async function loadIsAllowedProxyUrl() {
  const source = readFileSync(path.join(__dirname, "index.js"), "utf-8");
  const constMatch = source.match(/const ALLOWED_PROXY_HOST_SUFFIXES = \[[^\]]*\];/);
  assert.ok(constMatch, "ALLOWED_PROXY_HOST_SUFFIXES const not found");
  const fnSrc = extractFunction(source, "isAllowedProxyUrl");
  const moduleFile = path.join(scratchDir, `extracted-proxy-${moduleCounter++}.mjs`);
  writeFileSync(moduleFile, `${constMatch[0]}\n${fnSrc}\nexport { isAllowedProxyUrl };\n`);
  return (await import(pathToFileURL(moduleFile).href)).isAllowedProxyUrl;
}

test("isAllowedProxyUrl(): accepts the team default and any other *.workers.dev origin", async () => {
  const isAllowedProxyUrl = await loadIsAllowedProxyUrl();
  assert.equal(isAllowedProxyUrl("https://team-iz-nvidia-proxy.popixoxipop.workers.dev"), true);
  assert.equal(isAllowedProxyUrl("https://someone-elses-deploy.workers.dev"), true);
});

test("isAllowedProxyUrl(): rejects non-workers.dev hosts, including private/internal-looking ones", async () => {
  const isAllowedProxyUrl = await loadIsAllowedProxyUrl();
  assert.equal(isAllowedProxyUrl("http://169.254.169.254/latest/meta-data/"), false);
  assert.equal(isAllowedProxyUrl("https://169.254.169.254/latest/meta-data/"), false);
  assert.equal(isAllowedProxyUrl("https://evil.example.com"), false);
  assert.equal(isAllowedProxyUrl("https://team-iz-nvidia-proxy.popixoxipop.workers.dev.evil.com"), false);
});

test("isAllowedProxyUrl(): rejects non-https schemes and malformed URLs", async () => {
  const isAllowedProxyUrl = await loadIsAllowedProxyUrl();
  assert.equal(isAllowedProxyUrl("http://team-iz-nvidia-proxy.popixoxipop.workers.dev"), false);
  assert.equal(isAllowedProxyUrl("ext::sh -c 'touch pwned'"), false);
  assert.equal(isAllowedProxyUrl("not a url at all"), false);
});

// verifyReadAccess() regression test (2026-08-05, redteam audit M1).
async function loadVerifyReadAccess() {
  const source = readFileSync(path.join(__dirname, "index.js"), "utf-8");
  const supabaseUrlMatch = source.match(/const SUPABASE_URL = "[^"]*";/);
  const supabaseKeyMatch = source.match(/const SUPABASE_ANON_KEY = "[^"]*";/);
  assert.ok(supabaseUrlMatch && supabaseKeyMatch, "SUPABASE_URL/SUPABASE_ANON_KEY consts not found");
  const pgFetchSrc = extractFunction(source, "pgFetch");
  const verifySrc = extractFunction(source, "verifyReadAccess");
  const moduleFile = path.join(scratchDir, `extracted-verify-${moduleCounter++}.mjs`);
  writeFileSync(
    moduleFile,
    `${supabaseUrlMatch[0]}\n${supabaseKeyMatch[0]}\n${pgFetchSrc}\n${verifySrc}\nexport { verifyReadAccess };\n`
  );
  return (await import(pathToFileURL(moduleFile).href)).verifyReadAccess;
}

test("verifyReadAccess(): rejects a 200-with-empty-array response (RLS-excluded row) instead of trusting it", async () => {
  const verifyReadAccess = await loadVerifyReadAccess();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify([]), { status: 200 });
  try {
    await assert.rejects(() => verifyReadAccess("some-token", "run-not-mine"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("verifyReadAccess(): accepts a 200-with-one-row response", async () => {
  const verifyReadAccess = await loadVerifyReadAccess();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify([{ id: "run-mine" }]), { status: 200 });
  try {
    await assert.doesNotReject(() => verifyReadAccess("some-token", "run-mine"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("verifyReadAccess(): still rejects a genuine non-2xx response", async () => {
  const verifyReadAccess = await loadVerifyReadAccess();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("nope", { status: 401 });
  try {
    await assert.rejects(() => verifyReadAccess("bad-token", "run-x"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

// M2/M3 regression tests (2026-08-05). Both live inside the P01AnalysisJob Durable
// Object (POST /analyses handler + alarm()) -- exercising them behaviorally would mean
// mocking DO storage, patchRun/upsertArtifact/callChunkAnalysis's network calls, and the
// class's `extends DurableObject` (cloudflare:workers, unavailable in plain Node) all at
// once. Disproportionate to how simple these two fixes are (bounds-check a request body;
// add cleanup calls on three known exit paths) -- static checks against the real source
// text catch an accidental revert without that machinery.
test("M2: /analyses request handler bounds chunks.length and per-chunk text length", () => {
  const source = readFileSync(path.join(__dirname, "index.js"), "utf-8");
  assert.match(source, /const MAX_CHUNKS = \d+;/);
  assert.match(source, /const MAX_CHUNK_TEXT_CHARS = \d+;/);
  assert.match(source, /chunks\.length > MAX_CHUNKS/);
  assert.match(source, /c\.text\.length > MAX_CHUNK_TEXT_CHARS/);
});

test("M3: apiKey/accessToken are deleted from DO storage on all three terminal alarm() paths", () => {
  const source = readFileSync(path.join(__dirname, "index.js"), "utf-8");
  const alarmStart = source.indexOf("async alarm() {");
  assert.ok(alarmStart >= 0, "alarm() method not found");
  let depth = 0;
  let alarmEnd = -1;
  for (let i = source.indexOf("{", alarmStart); i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) { alarmEnd = i; break; }
    }
  }
  assert.ok(alarmEnd > alarmStart, "unbalanced braces in alarm()");
  const alarmSrc = source.slice(alarmStart, alarmEnd + 1);
  const cleanupCalls = alarmSrc.match(/storage\.delete\(\["apiKey", "accessToken"\]\)/g) || [];
  assert.equal(cleanupCalls.length, 3, "expected cleanup on cancelled/done/error paths (3 total)");
});

for (const [label, sourceFilePath] of Object.entries(TARGETS)) {
  test(`${label}: makeUnitMap() does not crash on unit_id="__proto__"`, async () => {
    const { makeUnitMap } = await loadBothFunctions(sourceFilePath);
    const chunkResults = [{
      units: [{ unit_id: "__proto__", unit_title: "injected", source_pages: [1] }],
      concepts: [],
    }];
    const unitMap = makeUnitMap(chunkResults);
    assert.equal(Object.keys(unitMap).length, 1);
    assert.ok(Object.prototype.hasOwnProperty.call(unitMap, "__proto__"));
    assert.equal(unitMap["__proto__"].unit_title, "injected");
  });

  test(`${label}: makeUnitMap() handles "constructor"/"toString" unit_ids too`, async () => {
    const { makeUnitMap } = await loadBothFunctions(sourceFilePath);
    const chunkResults = [{
      units: [
        { unit_id: "constructor", unit_title: "a", source_pages: [1] },
        { unit_id: "toString", unit_title: "b", source_pages: [2] },
      ],
      concepts: [],
    }];
    const unitMap = makeUnitMap(chunkResults);
    assert.equal(Object.keys(unitMap).length, 2);
    assert.equal(unitMap.constructor.unit_title, "a");
    assert.equal(unitMap.toString.unit_title, "b");
  });

  test(`${label}: normalizeUnitMap() does not silently drop unit_id="__proto__"`, async () => {
    const { makeUnitMap, normalizeUnitMap } = await loadBothFunctions(sourceFilePath);
    const chunkResults = [{
      units: [{ unit_id: "__proto__", unit_title: "injected", source_pages: [1] }],
      concepts: [],
    }];
    const normalized = normalizeUnitMap(makeUnitMap(chunkResults));
    assert.equal(Object.keys(normalized).length, 1, "the __proto__ unit must survive normalization, not vanish");
    assert.equal(Object.getPrototypeOf(normalized), null, "normalized's own prototype must be untouched");
  });

  test(`${label}: ordinary unit_ids are unaffected`, async () => {
    const { makeUnitMap, normalizeUnitMap } = await loadBothFunctions(sourceFilePath);
    const chunkResults = [{
      units: [{ unit_id: "02", unit_title: "정상 유닛", source_pages: [3, 3, 1] }],
      concepts: [{ unit_id: "02", name: "개념", kind: "concept", source_pages: [1] }],
    }];
    const normalized = normalizeUnitMap(makeUnitMap(chunkResults));
    assert.deepEqual(Object.keys(normalized), ["02"]);
    assert.deepEqual(normalized["02"].source_pages, [1, 3]);
    assert.equal(normalized["02"].concepts.length, 1);
  });
}
