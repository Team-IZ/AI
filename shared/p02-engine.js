// Ported from Pipeline Lab's p02-runner.js (popixoxipop-collab/Code_reviewer_with_feedback,
// docs/lab/p02-runner.js) via copy-then-subtract, NOT re-derived from a summary. Every
// function body below is byte-identical to the original except the specific, individually
// commented changes required because this is now a standalone page (not a tab in a
// single-page app) reached through a 3-page flow instead of clicked within one page:
//
//   1. ensurePipelineSource(): webtool_driver.py fetch path -> "../webtool_driver.py"
//      (this page lives in trainee/, one level deeper than the repo root the file sits at).
//   2. handleZipFile() split into a pure parseZipFile() (JSZip parsing + categorization,
//      no DOM) and formatZipStatus() (the exact original status-string logic, still a pure
//      function so the message text isn't re-typed by whoever builds the page). The
//      page owns turning these into on-screen status text.
//   3. run() takes an explicit `input` object ({method:"pat",repoInput,branch} or
//      {method:"zip",zipFiles}) instead of reading #p02-repo-input/#p02-branch-input/
//      module-level currentMethod/zipFiles from the DOM -- there is no DOM to read from on
//      a fresh page load, and per the port's own methodology module-level `let`s meant to
//      persist across calls don't survive a page navigation anyway.
//   4. Every LabApp.log/setStatus/startTimer/stopTimer call became the matching hooks.*
//      call (onProgress/onStatus/onRunStart/onRunEnd) -- see run()'s own comment for the
//      exact mapping. LabApp.saveFailedRun gained its onProgress callback param (see
//      lab-core.js).
//   5. renderInput() (input-method toggle + dropzone DOM/wiring) and renderResults() (+ the
//      "인터뷰 시작" click-wiring at its end) are deleted entirely -- the page draws its own
//      Team-IZ-styled input form and finding list, and owns the P02->P03 sessionStorage
//      handoff + navigation (there is no more P03Runner.run() cross-call; that was a
//      same-page tab-switch, this is now a page navigation).
//   6. run() rethrows the error after reporting it via hooks (the original never did,
//      because renderResults used to run in the same function on the success path only --
//      now that the caller draws the results itself, it needs a way to know success from
//      failure instead of only being able to parse hook message text).
//
// Everything else -- constants, the D164/D166/D179/D180/D181-documented resolvers,
// fetchGithubRepo's batching, the Pyodide bootstrap, collectOverrides -- is unchanged.
//
// P02 has zero LLM calls (verified against cognition/two_tier_scan.py + judgment/*.py --
// pure re/os/sys/json stdlib): no API key, no proxy, no external account needed for this
// stage. Runs the REAL, unmodified pipeline source in-browser via Pyodide, fetched fresh
// from raw.githubusercontent.com every run so it never drifts from the repo. The only new
// Python is webtool_driver.py, which just applies parameter overrides as module attribute
// writes, then calls the real scan()/score().
const P02Engine = (() => {
  const REPO_RAW_BASE = "https://raw.githubusercontent.com/popixoxipop-collab/Code_reviewer_with_feedback/main/";
  const PIPELINE_FILES = [
    "cognition/two_tier_scan.py",
    "judgment/score_findings.py",
    "judgment/idiom_filter.py",
    "judgment/tier_b_suppression_filter.py",
    "judgment/subrubric.py",
    "judgment/tier_b_hook.py",
    "judgment/subrubric_hook.py",
    "judgment/isolation_categories/role_separation/patterns.json",
    "judgment/isolation_categories/domain_irrelevance/patterns.json",
    "judgment/isolation_categories/alt_storage_or_scope/patterns.json",
    "judgment/isolation_categories/perf_optimization/patterns.json",
    "judgment/tier_b_suppressions/suppressions.json",
    "judgment/subrubric_weights/question_value/weights.json",
    "judgment/subrubric_weights/design_intent/weights.json",
    "judgment/subrubric_weights/risk/weights.json",
    "judgment/idioms/python/idiom_patterns.json",
    "judgment/idioms/java/idiom_patterns.json",
    "judgment/idioms/cpp/idiom_patterns.json",
    "judgment/idioms/swift/idiom_patterns.json",
    "judgment/idioms/javascript/idiom_patterns.json",
    "judgment/idioms/c/idiom_patterns.json",
  ];
  const SKIP_DIR_NAMES = new Set(["node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", "static", "vendor", "vendored"]);
  const SRC_EXTS = [".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".swift"];
  // D181: cap on how many text-mentioned files (D180) get sent as code context for one
  // finding -- unmeasured/provisional, chosen against p03-1's own existing 4000-char total
  // prompt budget (manifest truncation.code_context): 3 files leaves roughly 1300 chars
  // each on average, workable for typical function/class bodies but not derived from real
  // usage data. Only applies to the multi-file text-mention fallback (D180) -- a direct
  // file-field match (D179) is always exactly one file regardless of this constant.
  const MAX_CONNECT_FILES = 3;

  let pyodide = null;

  function isSkippedDir(relPath) {
    return relPath.split("/").some((p) => SKIP_DIR_NAMES.has(p));
  }

  // D164: extension matching is case-insensitive (lowercased before the SRC_EXTS check) so
  // a source file named e.g. MAIN.PY or App.TS isn't silently treated as non-source.
  function isSkippedPath(relPath) {
    if (isSkippedDir(relPath)) return true;
    const ext = "." + (relPath.split(".").pop() || "").toLowerCase();
    if (!SRC_EXTS.includes(ext)) return true;
    return false;
  }

  // D166: a .ipynb is JSON (cells/outputs/execution_count/metadata), not source text --
  // extract only the code cells' actual source and present that as a virtual .py file, so
  // the unmodified Python pipeline needs zero changes. Markdown/raw cells are dropped.
  function isNotebookPath(relPath) {
    return !isSkippedDir(relPath) && relPath.toLowerCase().endsWith(".ipynb");
  }

  function extractNotebookSource(jsonText) {
    let nb;
    try {
      nb = JSON.parse(jsonText);
    } catch (e) {
      return null; // malformed notebook JSON
    }
    const cells = Array.isArray(nb.cells) ? nb.cells : [];
    const codeParts = cells
      .filter((c) => c.cell_type === "code")
      .map((c) => (Array.isArray(c.source) ? c.source.join("") : (c.source || "")))
      .filter((s) => s.trim());
    return codeParts.join("\n\n");
  }

  // D179: the real pipeline's finding.file is always a bare basename (two_tier_scan.py's
  // fan_in_keys/flagged dicts key by os.path.basename()), but `files` here is keyed by the
  // full zip/repo-relative path -- match by basename instead of requiring an exact key
  // match. If two files share a basename in different folders, this picks the first
  // Object.keys() match -- not a new ambiguity, since the real pipeline's own
  // os.path.basename() reduction already collapses same-named files from different
  // folders into one fan_in key.
  function findFileByBasename(files, basename) {
    if (!basename) return null;
    if (files[basename] !== undefined) return basename;
    return Object.keys(files).find((relPath) => relPath.split("/").pop() === basename) || null;
  }

  // D180: repeated-pattern:duplicate-definition findings legitimately have file: null (see
  // judgment/score_findings.py's find_duplicate_definitions()) -- the real files ARE named
  // in the finding's free-text description though (Python f-string list/dict repr). Rather
  // than parsing that repr syntax, this checks each already-known real filename for whether
  // its basename literally appears as a substring of the finding text.
  function findReferencedFiles(files, findingText) {
    if (!findingText) return [];
    return Object.keys(files).filter((relPath) => findingText.includes(relPath.split("/").pop()));
  }

  // Single entry point for "what file(s) (if any) can this finding connect to" -- prefers
  // the real file field (D179's basename match) when present, falls back to text-mention
  // matching (D180) for file:null findings like repeated-pattern. Returns null, or
  // {path, viaText, allPaths} where viaText marks the fallback case so callers can label
  // the UI honestly ("여러 파일 중 하나" vs a direct match).
  function resolveConnectableFile(files, finding) {
    const direct = findFileByBasename(files, finding.file);
    if (direct) return { path: direct, viaText: false, allPaths: [direct] };
    const mentioned = findReferencedFiles(files, finding.finding);
    if (mentioned.length) return { path: mentioned[0], viaText: true, allPaths: mentioned };
    return null;
  }

  // Change #2: split from the original handleZipFile(file, container), which interleaved
  // this exact parsing/categorization with `container.querySelector("#p02-zip-status")`
  // DOM writes. Throws on a genuine unzip failure (JSZip.loadAsync) -- the page catches
  // and formats "압축 해제 실패: ${err.message}" itself, same text as the original.
  async function parseZipFile(blob) {
    const zip = await JSZip.loadAsync(blob);
    const entries = Object.values(zip.files).filter((e) => !e.dir);
    const files = {};
    // D153: "소스 파일 0개 로드됨"만으로는 zip 안에 뭐가 있었는지 전혀 안 보여서
    // 원인 파악이 안 됨. 스킵된 확장자별 개수를 세어뒀다가, 0개일 때만 진단으로 보여줌.
    const skippedExtCounts = {};
    let notebookCodeCount = 0;
    for (const entry of entries) {
      if (isNotebookPath(entry.name)) {
        try {
          const raw = await entry.async("string");
          const src = extractNotebookSource(raw);
          if (src && src.trim()) {
            files[entry.name + ".py"] = src;
            notebookCodeCount += 1;
          } else {
            skippedExtCounts[".ipynb(코드셀 없음/파싱실패)"] = (skippedExtCounts[".ipynb(코드셀 없음/파싱실패)"] || 0) + 1;
          }
        } catch (e) {
          skippedExtCounts[".ipynb(읽기실패)"] = (skippedExtCounts[".ipynb(읽기실패)"] || 0) + 1;
        }
        continue;
      }
      if (isSkippedPath(entry.name)) {
        const ext = "." + (entry.name.split(".").pop() || "") || "(확장자 없음)";
        skippedExtCounts[ext] = (skippedExtCounts[ext] || 0) + 1;
        continue;
      }
      try {
        files[entry.name] = await entry.async("string");
      } catch (e) { /* binary file, skip */ }
    }
    return { files, loadedCount: Object.keys(files).length, notebookCodeCount, skippedExtCounts };
  }

  // Change #2 cont'd: the original's exact status-string logic (breakdown sort/slice(0,6),
  // notebook-extraction note), kept as a pure function so this text isn't re-typed by
  // whoever builds the page's status display.
  function formatZipStatus({ loadedCount, notebookCodeCount, skippedExtCounts }, filename) {
    if (loadedCount === 0 && Object.keys(skippedExtCounts).length) {
      const breakdown = Object.entries(skippedExtCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([ext, n]) => `${ext}×${n}`)
        .join(", ");
      return `${filename}: 소스 파일 0개 로드됨 -- zip 안 파일: ${breakdown} (지원 확장자: ${SRC_EXTS.join(", ")}, .ipynb는 코드 셀만 추출해서 지원)`;
    }
    const notebookNote = notebookCodeCount ? ` (그 중 .ipynb에서 추출: ${notebookCodeCount}개)` : "";
    return `${filename}: 소스 파일 ${loadedCount}개 로드됨${notebookNote}`;
  }

  function parseRepoInput(raw) {
    let s = raw.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/$/, "").replace(/\.git$/, "");
    const parts = s.split("/");
    if (parts.length < 2) throw new Error("owner/repo 형식으로 입력하세요");
    return { owner: parts[0], repo: parts[1] };
  }

  async function fetchGithubRepo(owner, repo, branch, pat, onProgress) {
    const headers = { accept: "application/vnd.github+json" };
    if (pat) headers.authorization = `Bearer ${pat}`;

    if (!branch) {
      const infoRes = await fetch(`https://api.github.com/repos/${owner}/${repo}`, { headers });
      if (!infoRes.ok) throw new Error(`repo 조회 실패 (HTTP ${infoRes.status}) — 이름/권한 확인`);
      branch = (await infoRes.json()).default_branch;
    }

    const treeRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`, { headers });
    if (!treeRes.ok) throw new Error(`파일 목록 조회 실패 (HTTP ${treeRes.status})`);
    const tree = await treeRes.json();
    if (tree.truncated) onProgress("경고: repo가 커서 GitHub API가 파일 목록을 일부만 반환함");

    // D166: notebooks pass this filter too now (isSkippedPath alone would still reject
    // .ipynb) so they reach the per-blob step below, where the actual code-cell
    // extraction happens -- same handling as the ZIP path.
    const blobs = (tree.tree || []).filter((t) => t.type === "blob" && (isNotebookPath(t.path) || !isSkippedPath(t.path)));
    onProgress(`소스 파일 ${blobs.length}개 발견, 내용 가져오는 중...`);

    const files = {};
    const CONCURRENCY = 6;
    let done = 0;
    for (let i = 0; i < blobs.length; i += CONCURRENCY) {
      const batch = blobs.slice(i, i + CONCURRENCY);
      await Promise.all(batch.map(async (b) => {
        const blobRes = await fetch(b.url, { headers });
        if (blobRes.ok) {
          const blobData = await blobRes.json();
          if (blobData.encoding === "base64") {
            try {
              const decoded = decodeURIComponent(escape(atob(blobData.content.replace(/\n/g, ""))));
              if (isNotebookPath(b.path)) {
                const src = extractNotebookSource(decoded);
                if (src && src.trim()) files[b.path + ".py"] = src;
              } else {
                files[b.path] = decoded;
              }
            } catch (e) { /* binary content, skip */ }
          }
        }
        done += 1;
        if (done % 5 === 0 || done === blobs.length) onProgress(`${done}/${blobs.length} 파일 가져옴`);
      }));
    }
    return files;
  }

  async function ensurePyodide(onProgress) {
    pyodide = await LabPyodide.get(onProgress);
    return pyodide;
  }

  async function ensurePipelineSource(onProgress) {
    if (LabPyodide.isLoaded("p02")) return;
    onProgress("파이프라인 원본 코드 로드 중 (raw.githubusercontent.com, repo 최신본)...");
    await LabPyodide.loadFiles(pyodide, REPO_RAW_BASE, PIPELINE_FILES, "/lib", null);
    // Change #1: "webtool_driver.py" -> "../webtool_driver.py" -- this page lives in
    // trainee/, one level below the repo root the file actually sits at.
    const driverRes = await fetch("../webtool_driver.py");
    pyodide.FS.writeFile("/lib/webtool_driver.py", await driverRes.text(), { encoding: "utf8" });
    pyodide.runPython(`
import sys
for p in ["/lib", "/lib/cognition", "/lib/judgment"]:
    if p not in sys.path:
        sys.path.insert(0, p)
`);
    LabPyodide.markLoaded("p02");
  }

  function writeTargetFiles(files) {
    if (pyodide.FS.analyzePath("/target").exists) {
      pyodide.runPython(`
import shutil
shutil.rmtree("/target", ignore_errors=True)
`);
    }
    pyodide.FS.mkdirTree("/target");
    for (const [relPath, content] of Object.entries(files)) {
      const fullPath = "/target/" + relPath;
      const dir = fullPath.substring(0, fullPath.lastIndexOf("/"));
      pyodide.FS.mkdirTree(dir);
      pyodide.FS.writeFile(fullPath, content, { encoding: "utf8" });
    }
  }

  function collectOverrides() {
    const manifest = LabApp.getManifest();
    const stages = manifest.pipelines.p02.stages;
    const overrides = { two_tier_scan: {}, score_findings: {} };
    const moduleForStage = { "p02-1": "two_tier_scan", "p02-2": "two_tier_scan", "p02-3": "two_tier_scan", "p02-4": "score_findings" };
    for (const stage of stages) {
      const ov = LabApp.getOverride("p02", stage.id);
      if (ov && ov.params) {
        const mod = moduleForStage[stage.id];
        Object.assign(overrides[mod], ov.params);
      }
    }
    return overrides;
  }

  // Change #3+#4: takes {method:"pat",repoInput,branch} | {method:"zip",zipFiles} instead
  // of reading DOM/module state; every LabApp.log/setStatus/startTimer/stopTimer call ->
  // hooks.onProgress(msg) / hooks.onStatus(text,kind) / hooks.onRunStart() /
  // hooks.onRunEnd(elapsedMs). Change #5: no renderResults()/interview-button wiring here
  // anymore -- returns {result, files} so the page draws its own finding list. Change #6:
  // rethrows after reporting (see file header).
  async function run(input, hooks) {
    const startedAt = new Date();
    hooks.onStatus("실행 중...", "running");
    hooks.onRunStart();
    try {
      let files;
      if (input.method === "pat") {
        const repoInput = input.repoInput || "";
        const branch = (input.branch || "").trim() || null;
        if (!repoInput.trim()) throw new Error("repo를 입력하세요 (owner/repo)");
        const { owner, repo } = parseRepoInput(repoInput);
        const pat = LabConfig.get("github-pat");
        hooks.onProgress(`${owner}/${repo} (${branch || "기본 브랜치"}) 가져오는 중...`);
        files = await fetchGithubRepo(owner, repo, branch, pat, (msg) => hooks.onProgress(msg));
      } else {
        if (!input.zipFiles) throw new Error("ZIP 파일을 먼저 드롭하세요");
        files = input.zipFiles;
      }
      if (Object.keys(files).length === 0) throw new Error("스캔 대상 소스 파일을 찾지 못함 (확장자/디렉터리 필터 확인)");

      await ensurePyodide((msg) => hooks.onProgress(msg));
      await ensurePipelineSource((msg) => hooks.onProgress(msg));
      hooks.onProgress(`대상 파일 ${Object.keys(files).length}개를 파이썬 가상 파일시스템에 기록 중...`);
      writeTargetFiles(files);

      const overrides = collectOverrides();
      hooks.onProgress("two_tier_scan.py + score_findings.py 실행 중 (원본 코드, 수정 없음)...");
      const py = await ensurePyodide();
      py.globals.set("overrides_json", JSON.stringify(overrides));
      py.runPython(`
import webtool_driver
_result = webtool_driver.run_scan("/target", overrides_json)
`);
      const resultJson = py.globals.get("_result");
      const result = JSON.parse(resultJson);

      const finishedAt = new Date();
      hooks.onRunEnd(finishedAt - startedAt);
      hooks.onStatus("완료", "done");
      hooks.onProgress(`finding ${result.judgment.findings.length}건 산출됨`);
      await maybeSaveRun(result, files, startedAt, finishedAt, input.method, hooks);
      return { result, files };
    } catch (err) {
      hooks.onRunEnd(new Date() - startedAt);
      console.error(err);
      hooks.onStatus(`오류: ${err.message}`, "error");
      hooks.onProgress(`오류: ${err.message}`);
      await LabApp.saveFailedRun("p02", null, err, startedAt, hooks.onProgress);
      throw err;
    }
  }

  // `method` param replaces the original's module-level `currentMethod` read (see file
  // header change #3).
  async function maybeSaveRun(result, files, startedAt, finishedAt, method, hooks) {
    if (!LabDB.isConfigured()) {
      hooks.onProgress("Supabase 미설정 — 결과는 화면에만 표시됨(DB 저장 안 됨)");
      return;
    }
    try {
      await LabDB.saveRun({
        pipeline: "p02",
        model: null,
        input_meta: { file_count: Object.keys(files).length, method },
        overrides: result.overrides_applied || [],
        rubric_overridden: false,
        artifacts: [{ kind: "findings", content: result.judgment }],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
      });
      hooks.onProgress(`결과가 팀 DB에 저장됨 (소요시간 ${LabApp.formatElapsed(finishedAt - startedAt)})`);
    } catch (err) {
      hooks.onProgress(`DB 저장 실패(결과는 화면에 남아있음): ${err.message}`);
    }
  }

  return {
    resolveConnectableFile, findFileByBasename, findReferencedFiles,
    parseRepoInput, fetchGithubRepo, parseZipFile, formatZipStatus,
    run, MAX_CONNECT_FILES, SRC_EXTS,
  };
})();
