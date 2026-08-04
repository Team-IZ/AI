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
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} not found`);
  let depth = 0;
  for (let i = start; i < source.length; i++) {
    if (source[i] === "{") {
      depth++;
    } else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
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
