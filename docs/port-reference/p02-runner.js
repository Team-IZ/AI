// P02 has zero LLM calls (verified against cognition/two_tier_scan.py + judgment/*.py --
// pure re/os/sys/json stdlib), so this is the only pipeline that needs no API key, no
// proxy, and no external account at all -- it's the Phase-1 MVP from PLAN.md.
//
// D-E (PLAN.md): runs the REAL, unmodified pipeline source in-browser via Pyodide,
// fetched fresh from raw.githubusercontent.com every run so it never drifts from the
// repo. The only new Python is webtool_driver.py, which just applies parameter
// overrides as module attribute writes, then calls the real scan()/score().
const P02Runner = (() => {
  const REPO_RAW_BASE = "https://raw.githubusercontent.com/popixoxipop-collab/Code_reviewer_with_feedback/main/";
  const PIPELINE_FILES = [
    "cognition/two_tier_scan.py",
    "judgment/score_findings.py",
    "judgment/idiom_filter.py",
    "judgment/tier_b_suppression_filter.py",
    "judgment/subrubric.py",
    "judgment/tier_b_hook.py",
    "judgment/subrubric_hook.py",
    "judgment/importance_rank.py",
    "judgment/rank_weights/rank_weights.json",
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
  const SRC_EXTS = [".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".swift"];
  // D181: cap on how many text-mentioned files (D180) get sent as code context for one
  // finding -- unmeasured/provisional, chosen against p03-1's own existing 4000-char total
  // prompt budget (manifest truncation.code_context): 3 files leaves roughly 1300 chars
  // each on average, workable for typical function/class bodies but not derived from real
  // usage data. Only applies to the multi-file text-mention fallback (D180) -- a direct
  // file-field match (D179) is always exactly one file regardless of this constant.
  const MAX_CONNECT_FILES = 3;

  let pyodide = null;
  let currentMethod = "pat"; // "pat" | "zip"
  let zipFiles = null; // { relPath: content }

  // D195-fix: these three used to be a hardcoded module-level SKIP_DIR_NAMES Set that
  // silently fell out of sync with judgment/score_findings.py's SKIP_DIRS when D195
  // widened it from 10 to 26 entries -- files under target/, .next/, .pytest_cache/ etc.
  // still got downloaded here (and burned GitHub API rate limit on them, D192) even
  // though Python discarded them right after. Resolving live from the manifest means the
  // fetch step and the Python scan can never drift again, and a trainee's SKIP_DIRS edit
  // in the p02-1 param panel now actually affects what gets downloaded.
  function isSkippedDir(relPath) {
    const skipDirs = new Set(LabApp.resolveParam("p02", "p02-1", "SKIP_DIRS") || []);
    const prefixes = LabApp.resolveParam("p02", "p02-1", "SKIP_DIR_PREFIXES") || [];
    const suffixes = LabApp.resolveParam("p02", "p02-1", "SKIP_DIR_SUFFIXES") || [];
    return relPath.split("/").some(
      (p) => skipDirs.has(p) || prefixes.some((pre) => p.startsWith(pre)) || suffixes.some((suf) => p.endsWith(suf))
    );
  }

  // D164 (2026-07-15): Codex root-cause investigation (user-delegated) into a real "스캔
  // 대상 소스 파일을 찾지 못함" failure confirmed this via a direct Node repro: extension
  // matching was case-SENSITIVE against an all-lowercase SRC_EXTS, so a legitimately-source
  // file named e.g. MAIN.PY or App.TS (case varies across platforms/editors/exports) was
  // silently treated as non-source and skipped. Lowercasing before the SRC_EXTS check can
  // only ever ADD previously-wrongly-excluded valid source files to the scan -- it can't
  // cause any new false inclusion, since it's still the same whitelist, just case-normalized.
  function isSkippedPath(relPath) {
    if (isSkippedDir(relPath)) return true;
    const ext = "." + (relPath.split(".").pop() || "").toLowerCase();
    if (!SRC_EXTS.includes(ext)) return true;
    // D195-fix: mirrors GENERATED_FILENAME_RE in two_tier_scan.py/score_findings.py --
    // catches artifacts (contenthash bundles, .min.js, .d.ts) sitting outside any skip
    // directory, matched against the basename only (same as Python's os.walk fnames).
    const genRePattern = LabApp.resolveParam("p02", "p02-1", "GENERATED_FILENAME_RE");
    if (genRePattern) {
      const basename = relPath.split("/").pop() || "";
      if (new RegExp(genRePattern, "i").test(basename)) return true;
    }
    return false;
  }

  // D166 (2026-07-15): a real user's zip (AI_LLMOps_3일차_실습예시파일.zip, confirmed via
  // D153's own diagnostic surfacing ".ipynb×3") was made ENTIRELY of Jupyter notebooks --
  // 0 files ever loadable, not a bug, just genuinely unsupported. A .ipynb is JSON (cells,
  // outputs, execution_count, metadata), not source text -- adding it to SRC_EXTS and
  // feeding the raw JSON to the (unmodified) Python pipeline would scan notebook plumbing,
  // not code. Instead: extract only the code cells' actual source and present that to the
  // pipeline as a virtual .py file, so cognition/two_tier_scan.py etc. need zero changes.
  // Markdown/raw cells are dropped (they're commentary, not code the scan pipeline judges)
  // -- a possible future enhancement, not needed to fix "0 files found".
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

  // D179 (2026-07-15): real user report -- against a real subdirectory-nested zip, the D176
  // "인터뷰 시작" connector produced ZERO buttons even though findings had non-null `file`
  // values. Root cause confirmed by reading cognition/two_tier_scan.py directly:
  // tier_a_structural_scan() builds fan_in_keys via os.path.basename(f) (line ~186), and
  // tier_b's flagged dict is keyed the same way (line ~233) -- every finding.file the REAL
  // pipeline ever reports is a bare basename, with any subdirectory prefix stripped, by
  // design (unrelated to this web tool, can't change without diverging from the real
  // pipeline). But `files` here is keyed by the FULL zip-relative path (entry.name), which
  // keeps subdirectory prefixes -- so files[finding.file] silently misses for any nested
  // file, and hasCode was always false whenever the zip had folders at all. This resolver
  // matches by basename instead of requiring an exact key match; a plain root-level file
  // still matches on the first branch (all D176 test fixtures happened to be flat, which is
  // why this didn't surface until a real nested zip). If two files share a basename in
  // different folders, this picks the first Object.keys() match -- not a new ambiguity this
  // code introduces, since the real pipeline's own os.path.basename() reduction already
  // collapses same-named files from different folders into one fan_in key.
  function findFileByBasename(files, basename) {
    if (!basename) return null;
    if (files[basename] !== undefined) return basename;
    return Object.keys(files).find((relPath) => relPath.split("/").pop() === basename) || null;
  }

  // D180 (2026-07-15): real user report -- against TWO separate real practice-notebook zips
  // in a row, every finding produced was repeated-pattern:duplicate-definition:* (file:
  // null by design, see score_findings.py's find_duplicate_definitions() loop -- a "same
  // function copy-pasted into N files" finding has no single file, so file is genuinely
  // null, not a bug). That made the connector button never appear at all for this user's
  // actual content, even though D179's fix was correct for what it covered. User confirmed
  // (AskUserQuestion) they want this connected anyway, auto-picking one of the referenced
  // files.
  //
  // The real files ARE named in the finding's free-text description though, e.g.
  // "'load_api_keys' 정의가 3개 파일에... 재등장: ['Ch06...ipynb.py', 'Ch07...ipynb.py', ...]"
  // -- Python's f-string interpolation of a list/dict repr. Rather than parsing Python repr
  // syntax (fragile: filenames here can themselves contain brackets, e.g. "Ch08. [추가
  // 과제]...ipynb.py", which would confuse a naive bracket-matching parser), this instead
  // checks EACH already-known real filename (from `files`, the actual loaded set) for
  // whether its basename literally appears as a substring of the finding text. Any real
  // filename that's mentioned will match; nothing needs to be parsed as list/dict syntax.
  function findReferencedFiles(files, findingText) {
    if (!findingText) return [];
    return Object.keys(files).filter((relPath) => findingText.includes(relPath.split("/").pop()));
  }

  // Single entry point for "what file (if any) can this finding connect to" -- prefers the
  // real file field (D179's basename match) when present, falls back to text-mention
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

  function renderInput(container) {
    container.innerHTML = `
      <div class="input-panel">
        <h3>코드 입력</h3>
        <div class="input-methods">
          <button class="method-btn active" data-method="pat">GitHub (PAT)</button>
          <button class="method-btn" data-method="zip">ZIP 드래그앤드롭</button>
        </div>
        <div id="p02-method-pat">
          <div class="input-row">
            <input type="text" id="p02-repo-input" placeholder="owner/repo 또는 https://github.com/owner/repo">
            <input type="text" id="p02-branch-input" placeholder="branch (비우면 기본 브랜치)" style="max-width:180px;">
          </div>
          <p style="color:var(--ink-faint); font-size:0.72rem; margin-top:6px;">
            공개 repo는 PAT 없이도 동작(GitHub API 비인증 60회/시간 제한). 비공개 repo는 상단 연결 설정에 PAT 입력 필요.
            PAT은 api.github.com에 직접 전달되며 프록시를 거치지 않음.
          </p>
        </div>
        <div id="p02-method-zip" class="hidden">
          <div class="dropzone" id="p02-dropzone">여기로 .zip 파일을 드래그하거나 클릭해서 선택</div>
          <input type="file" id="p02-zip-input" accept=".zip" class="hidden">
          <p id="p02-zip-status" style="color:var(--ink-faint); font-size:0.72rem; margin-top:6px;"></p>
        </div>
      </div>`;

    container.querySelectorAll(".method-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentMethod = btn.dataset.method;
        container.querySelectorAll(".method-btn").forEach((b) => b.classList.toggle("active", b === btn));
        container.querySelector("#p02-method-pat").classList.toggle("hidden", currentMethod !== "pat");
        container.querySelector("#p02-method-zip").classList.toggle("hidden", currentMethod !== "zip");
      });
    });

    const dropzone = container.querySelector("#p02-dropzone");
    const zipInput = container.querySelector("#p02-zip-input");
    dropzone.addEventListener("click", () => zipInput.click());
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
    dropzone.addEventListener("drop", async (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
      const file = e.dataTransfer.files[0];
      if (file) await handleZipFile(file, container);
    });
    zipInput.addEventListener("change", async () => {
      if (zipInput.files[0]) await handleZipFile(zipInput.files[0], container);
    });
  }

  async function handleZipFile(file, container) {
    const status = container.querySelector("#p02-zip-status");
    status.textContent = `압축 해제 중: ${file.name}...`;
    try {
      const zip = await JSZip.loadAsync(file);
      const entries = Object.values(zip.files).filter((e) => !e.dir);
      const files = {};
      // D153: "소스 파일 0개 로드됨"만으로는 zip 안에 뭐가 있었는지 전혀 안 보여서
      // 원인 파악이 안 됨(사용자 실측: LLMOps 실습 zip이 전부 0개로 나옴). 스킵된
      // 확장자별 개수를 세어뒀다가, 0개일 때만 진단으로 보여줌 -- 정상 로드 시엔
      // 기존 메시지 그대로.
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
      zipFiles = files;
      const loadedCount = Object.keys(files).length;
      if (loadedCount === 0 && Object.keys(skippedExtCounts).length) {
        const breakdown = Object.entries(skippedExtCounts)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 6)
          .map(([ext, n]) => `${ext}×${n}`)
          .join(", ");
        status.textContent = `${file.name}: 소스 파일 0개 로드됨 -- zip 안 파일: ${breakdown} (지원 확장자: ${SRC_EXTS.join(", ")}, .ipynb는 코드 셀만 추출해서 지원)`;
      } else {
        const notebookNote = notebookCodeCount ? ` (그 중 .ipynb에서 추출: ${notebookCodeCount}개)` : "";
        status.textContent = `${file.name}: 소스 파일 ${loadedCount}개 로드됨${notebookNote}`;
      }
    } catch (err) {
      status.textContent = `압축 해제 실패: ${err.message}`;
      zipFiles = null;
    }
  }

  function parseRepoInput(raw) {
    let s = raw.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\/$/, "").replace(/\.git$/, "");
    const parts = s.split("/");
    if (parts.length < 2) throw new Error("owner/repo 형식으로 입력하세요");
    return { owner: parts[0], repo: parts[1] };
  }

  // D192 (2026-07-16): real production incident (Supabase runs.error 실측) — 한 사용자가
  // "파일 목록 조회 실패 (HTTP 403)"에 8분간 87회 재제출했고, 직후 같은 네트워크의 두 번째
  // 사용자까지 같은 403을 맞음. 원인은 GitHub API 비인증 한도(IP당 60회/시간, 팀 공유망이면
  // 전원이 공유): 이 함수는 repo 하나 스캔에 blob마다 1회씩 수십 회를 쓰므로 한도가 금방
  // 소진되는데, 기존 메시지는 일반 403(없는 repo/권한 없음)과 구분이 안 돼 사용자가 재시도만
  // 반복했다. 판별은 GitHub의 공식 신호인 x-ratelimit-remaining==0 헤더로만 한다 — 상태코드
  // 403 단독으로는 권한 문제와 구분 불가. X-RateLimit-*는 Access-Control-Expose-Headers에
  // 포함돼 브라우저 fetch에서 읽힘(실제 rate-limited 403으로 실측 확인, 2026-07-16).
  // 재시도 로직은 일부러 안 넣음: 리셋 시각 전에는 몇 번을 다시 보내도 똑같이 실패한다
  // (87연속 실측). 헤더가 없거나 remaining>0이면 null을 반환해 기존 메시지를 그대로 쓴다.
  // EXIT: PAT 사용자가 5,000회 한도까지 소진하는 사례가 관측되면 그때 배치 크기를 재검토.
  function githubRateLimitError(res, pat) {
    if (!res.headers || res.headers.get("x-ratelimit-remaining") !== "0") return null;
    const resetTs = Number(res.headers.get("x-ratelimit-reset"));
    const resetStr = Number.isFinite(resetTs) && resetTs > 0
      ? new Date(resetTs * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
      : null;
    const until = resetStr ? `${resetStr}까지는` : "한도가 리셋될 때까지(최대 1시간)";
    return new Error(pat
      ? `GitHub API 요청 한도 초과 (PAT 기준 5,000회/시간) — ${until} 재시도해도 계속 실패합니다`
      : `GitHub API 요청 한도 초과 (비인증 IP당 60회/시간, 같은 네트워크 사용자끼리 공유) — ${until} 재시도해도 계속 실패합니다. GitHub PAT을 입력하면 한도가 5,000회/시간으로 늘어나 바로 해결됩니다`);
  }

  async function fetchGithubRepo(owner, repo, branch, pat, onProgress) {
    const headers = { accept: "application/vnd.github+json" };
    if (pat) headers.authorization = `Bearer ${pat}`;

    if (!branch) {
      const infoRes = await fetch(`https://api.github.com/repos/${owner}/${repo}`, { headers });
      if (!infoRes.ok) throw (githubRateLimitError(infoRes, pat) || new Error(`repo 조회 실패 (HTTP ${infoRes.status}) — 이름/권한 확인`));
      branch = (await infoRes.json()).default_branch;
    }

    const treeRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`, { headers });
    if (!treeRes.ok) throw (githubRateLimitError(treeRes, pat) || new Error(`파일 목록 조회 실패 (HTTP ${treeRes.status})`));
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
        } else {
          // D192: rate-limit이면 즉시 중단 — 조용히 파일만 빠진 "성공"을 만들지 않는다.
          // 그 외 비정상 응답은 기존대로 해당 파일만 건너뜀.
          const rlErr = githubRateLimitError(blobRes, pat);
          if (rlErr) throw rlErr;
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
    const driverRes = await fetch("webtool_driver.py");
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
    const overrides = { two_tier_scan: {}, score_findings: {}, importance_rank: {} };
    const moduleForStage = { "p02-1": "two_tier_scan", "p02-2": "two_tier_scan", "p02-3": "two_tier_scan", "p02-4": "score_findings", "p02-5": "importance_rank" };
    for (const stage of stages) {
      const ov = LabApp.getOverride("p02", stage.id);
      if (ov && ov.params) {
        const mod = moduleForStage[stage.id];
        Object.assign(overrides[mod], ov.params);
      }
    }
    return overrides;
  }

  async function run() {
    const pipelineId = "p02";
    LabApp.setStatus(pipelineId, "실행 중...", "running");
    const startedAt = new Date();
    LabApp.startTimer(pipelineId);
    try {
      let files;
      if (currentMethod === "pat") {
        const repoInput = document.getElementById("p02-repo-input").value;
        const branch = document.getElementById("p02-branch-input").value.trim() || null;
        if (!repoInput.trim()) throw new Error("repo를 입력하세요 (owner/repo)");
        const { owner, repo } = parseRepoInput(repoInput);
        const pat = LabConfig.get("github-pat");
        LabApp.log(pipelineId, `${owner}/${repo} (${branch || "기본 브랜치"}) 가져오는 중...`);
        files = await fetchGithubRepo(owner, repo, branch, pat, (msg) => LabApp.log(pipelineId, msg));
      } else {
        if (!zipFiles) throw new Error("ZIP 파일을 먼저 드롭하세요");
        files = zipFiles;
      }
      if (Object.keys(files).length === 0) throw new Error("스캔 대상 소스 파일을 찾지 못함 (확장자/디렉터리 필터 확인)");

      await ensurePyodide((msg) => LabApp.log(pipelineId, msg));
      await ensurePipelineSource((msg) => LabApp.log(pipelineId, msg));
      LabApp.log(pipelineId, `대상 파일 ${Object.keys(files).length}개를 파이썬 가상 파일시스템에 기록 중...`);
      writeTargetFiles(files);

      const overrides = collectOverrides();
      LabApp.log(pipelineId, "two_tier_scan.py + score_findings.py 실행 중 (원본 코드, 수정 없음)...");
      const py = await ensurePyodide();
      py.globals.set("overrides_json", JSON.stringify(overrides));
      py.runPython(`
import webtool_driver
_result = webtool_driver.run_scan("/target", overrides_json)
`);
      const resultJson = py.globals.get("_result");
      const result = JSON.parse(resultJson);

      const finishedAt = new Date();
      LabApp.stopTimer(pipelineId);
      LabApp.setStatus(pipelineId, "완료", "done");
      LabApp.log(pipelineId, `finding ${result.judgment.findings.length}건 산출됨`);
      renderResults(result, files);
      await maybeSaveRun(result, files, startedAt, finishedAt);
    } catch (err) {
      LabApp.stopTimer(pipelineId);
      console.error(err);
      LabApp.setStatus(pipelineId, `오류: ${err.message}`, "error");
      LabApp.log(pipelineId, `오류: ${err.message}`);
      await LabApp.saveFailedRun("p02", null, err, startedAt);
    }
  }

  function renderResults(result, files) {
    const j = result.judgment;
    let html = `<p>대상 파일 <b>${Object.keys(files).length}</b>개 · finding <b>${j.findings.length}</b>건`;
    if (j.hub) html += ` · 허브: <code>${LabApp.escapeHtml(j.hub)}</code>`;
    html += `</p>`;
    if (result.overrides_applied && result.overrides_applied.length) {
      html += `<p style="color:var(--accent); font-size:0.78rem;">적용된 파라미터 오버라이드: ${result.overrides_applied.map((s) => `<code>${LabApp.escapeHtml(s)}</code>`).join(", ")}</p>`;
    }
    if (j.findings.length === 0) {
      html += `<p style="color:var(--ink-faint);">이번 실행에서는 finding이 나오지 않았다.</p>`;
    }
    j.findings.forEach((f, idx) => {
      // D176: only findings tied to one real, loaded file can hand P03 real code context --
      // repeated-pattern findings (see judgment/score_findings.py) are cross-file by nature
      // and legitimately have file: null. D179: match by basename, not exact key. D180: a
      // real user's two separate test zips both produced ONLY repeated-pattern findings
      // (file: null) -- resolveConnectableFile() falls back to the files actually mentioned
      // in the finding's own description text, so this category can connect too.
      // D181: D180 silently used only the FIRST matched file, dropping the others -- but
      // the whole point of a duplicate-definition finding is comparing copies, and the
      // user's stated goal is generating questions that are maximally checkable against the
      // 5-axis rubric (a grader can't verify "did they accurately describe the duplication"
      // without seeing all the actual copies). Now ALL matched files (capped, see
      // MAX_CONNECT_FILES) are included, not just one.
      const resolved = resolveConnectableFile(files, f);
      let connectHtml;
      if (!resolved) {
        connectHtml = `<div style="margin-top:8px; font-size:0.7rem; color:var(--ink-faint);">코드 컨텍스트 없음 — 언급된 파일도 로드된 파일 중에 없어 인터뷰 연결 불가</div>`;
      } else if (resolved.viaText) {
        const shown = resolved.allPaths.slice(0, MAX_CONNECT_FILES);
        const omitted = resolved.allPaths.length - shown.length;
        const note = ` <span style="color:var(--ink-faint);">(finding에 언급된 파일 ${shown.length}개 코드 포함${omitted > 0 ? `, ${omitted}개는 길이 제한으로 제외` : ""})</span>`;
        connectHtml = `<button class="secondary" data-interview-idx="${idx}" style="margin-top:8px; font-size:0.72rem;">인터뷰 시작 →</button>${note}`;
      } else {
        connectHtml = `<button class="secondary" data-interview-idx="${idx}" style="margin-top:8px; font-size:0.72rem;">인터뷰 시작 →</button>`;
      }
      // D194: rank/rank_score from judgment/importance_rank.py -- always present on a fresh
      // scan (this is same-session run_scan() output, never historical Supabase data).
      const rankBadge = f.rank != null ? `#${f.rank} (${f.rank_score}점) · ` : "";
      html += `<div class="finding-card">
        <div class="fid">${LabApp.escapeHtml(rankBadge)}${LabApp.escapeHtml(f.id)} · priority: ${LabApp.escapeHtml(f.priority || "")}</div>
        <div>${LabApp.escapeHtml(f.finding || "")}</div>
        ${connectHtml}
      </div>`;
    });
    html += LabApp.jsonResultBlock("원본 scan/judgment JSON", result, "p02-result.json");
    LabApp.showResults(html);

    // D176: wired after showResults() replaces #results-content's innerHTML -- the buttons
    // don't exist until then. P03Runner is a later <script> in index.html, but this only
    // runs at click time (long after all scripts finish loading), so the load order is fine
    // (same classic-script-scope pattern already relied on throughout this codebase).
    // D178: user explicitly chose one-click auto-run over a pre-fill-then-manual-run step
    // (asked directly after D176 shipped) -- loadFindingFromP02() only pre-fills state/DOM,
    // so the actual firing of a real LLM call is this one extra P03Runner.run() below, not
    // something loadFindingFromP02() does itself. run()'s own NVIDIA key/proxy guard is the
    // only thing left protecting an unconfigured teammate from a confusing failure.
    document.querySelectorAll("[data-interview-idx]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const finding = j.findings[parseInt(btn.dataset.interviewIdx, 10)];
        if (!finding) return;
        const resolved = resolveConnectableFile(files, finding);
        // D181: pass ALL matched files (capped), not just resolved.path (the first) -- see
        // MAX_CONNECT_FILES. codeContexts is always an array now, even for the common
        // single-file case (D179's direct match), so P03Runner has one shape to handle.
        const codeContexts = resolved
          ? resolved.allPaths.slice(0, MAX_CONNECT_FILES).map((p) => ({ path: p, content: files[p] }))
          : [];
        document.querySelector('.tab-btn[data-pipeline="p03"]').click();
        P03Runner.loadFindingFromP02(finding, codeContexts);
        P03Runner.run();
      });
    });
  }

  async function maybeSaveRun(result, files, startedAt, finishedAt) {
    if (!LabDB.isConfigured()) {
      LabApp.log("p02", "Supabase 미설정 — 결과는 화면에만 표시됨(DB 저장 안 됨)");
      return;
    }
    try {
      await LabDB.saveRun({
        pipeline: "p02",
        model: null,
        input_meta: { file_count: Object.keys(files).length, method: currentMethod },
        overrides: result.overrides_applied || [],
        rubric_overridden: false,
        artifacts: [{ kind: "findings", content: result.judgment }],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
      });
      LabApp.log("p02", `결과가 팀 DB에 저장됨 (소요시간 ${LabApp.formatElapsed(finishedAt - startedAt)})`);
    } catch (err) {
      LabApp.log("p02", `DB 저장 실패(결과는 화면에 남아있음): ${err.message}`);
    }
  }

  LabApp.registerRunner("p02", { renderInput, run });
  return { renderInput, run };
})();
