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
// stage. Runs the REAL, unmodified pipeline source in-browser via Pyodide. The only new
// Python is webtool_driver.py, which just applies parameter overrides as module attribute
// writes, then calls the real scan()/score().
// D208: REPO_RAW_BASE used to be an absolute raw.githubusercontent.com URL pointing at
// popixoxipop-collab/Code_reviewer_with_feedback's `main` branch -- every P02/P03 run
// fetched this branch's own pipeline source live over the network, from a repo this team
// doesn't own. Fixed by copying the actual cognition/judgment/feedback source this branch
// needs into this repo (same relative layout the fetch list below already expects) and
// pointing the base at a same-origin relative path instead.
//   WHY: this repo's own maintainers can now update/patch that Python source directly
//   (git blame/PR review works normally) without needing any access to, or even the
//   continued existence of, the other team's repo -- true self-containment.
//   COST: this branch's copy of cognition/judgment/feedback no longer auto-updates when
//   the origin repo changes; a future upstream fix must be pulled over manually (same
//   "update via diff" process documented in Readme.md's 이식 방법론).
//   EXIT: to go back to always-fresh-from-origin, restore REPO_RAW_BASE to the absolute
//   raw.githubusercontent.com URL and delete the local cognition/judgment/feedback copies.
const P02Engine = (() => {
  const REPO_RAW_BASE = "../";
  const PIPELINE_FILES = [
    "cognition/two_tier_scan.py",
    "judgment/score_findings.py",
    "judgment/idiom_filter.py",
    // D-tierb1 (2026-07-31): tier_b_suppression_filter.py / tier_b_hook.py /
    // tier_b_suppressions/suppressions.json 3건 삭제 -- 해당 .py 파일이 저장소에서
    // 사라졌으므로 목록에 남겨두면 LabPyodide.loadFiles가 404를 받아 P02 부트스트랩
    // 자체가 실패한다(찾기 어려운 실패 모드라 파일 삭제와 반드시 같은 커밋이어야 함).
    "judgment/subrubric.py",
    "judgment/subrubric_hook.py",
    "judgment/importance_rank.py",
    "judgment/rank_weights/rank_weights.json",
    "judgment/isolation_categories/role_separation/patterns.json",
    "judgment/isolation_categories/domain_irrelevance/patterns.json",
    "judgment/isolation_categories/alt_storage_or_scope/patterns.json",
    "judgment/isolation_categories/perf_optimization/patterns.json",
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

  // D164: extension matching is case-insensitive (lowercased before the SRC_EXTS check) so
  // a source file named e.g. MAIN.PY or App.TS isn't silently treated as non-source.
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
  // D-fix8 (연주 audit D1, 2026-07-23): when the same basename exists in >1 folder, the old
  // Object.keys().find() picked whichever came first in insertion order -- for a ZIP this is
  // the archive's own internal entry order, which isn't something a student or reviewer
  // controls or can predict, so which file "won" varied non-deterministically by upload.
  //   WHY: collect every candidate and sort deterministically (shorter path first, then
  //   alphabetical) before picking -- doesn't make the pick "correct" (still a real
  //   ambiguity the original pipeline's own basename-keyed fan_in already has, per D179's
  //   comment above), but makes it reproducible: the same files always resolve to the same
  //   evidence code across reruns/re-uploads.
  //   COST: none beyond the sort itself (candidate counts are always small -- one project's
  //   worth of same-named files, not repo-wide).
  //   EXIT: if collision visibility (not just determinism) is needed later, this can return
  //   the full sorted candidate list instead of just the first, mirroring how
  //   resolveConnectableFile's text-mention branch already exposes `allPaths`.
  function findFileByBasename(files, basename) {
    if (!basename) return null;
    if (files[basename] !== undefined) return basename;
    const candidates = Object.keys(files).filter((relPath) => relPath.split("/").pop() === basename);
    if (!candidates.length) return null;
    candidates.sort((a, b) => a.length - b.length || a.localeCompare(b));
    return candidates[0];
  }

  // D-fix7 (연주 audit C2/D2, 2026-07-23): plain .includes() matched a basename anywhere
  // inside findingText, including as a substring of an unrelated longer word/filename
  // (e.g. "app.js" matching inside "webapp.js" or inside prose that happens to contain
  // that exact character run) -- a real risk given repeated-pattern findings' free-text
  // descriptions aren't controlled vocabulary.
  //   WHY: require the basename to be flanked by something other than a word character or
  //   a dot on both sides (or be at the very start/end of the text) -- basenames contain a
  //   dot themselves, so a plain \b word-boundary regex doesn't work here directly.
  //   COST: still not perfect (e.g. a basename embedded with no separator at all inside a
  //   run-on word is still possible in principle, just much rarer than the plain-substring
  //   case this replaces).
  //   EXIT: if false positives/negatives are actually observed, tighten further (e.g.
  //   require surrounding backticks/quotes specifically, matching how finding text usually
  //   quotes filenames) or loosen back towards the old substring behavior.
  function isBoundedMatch(text, needle) {
    const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(^|[^\\w.])${escaped}($|[^\\w.])`).test(text);
  }

  // D180: repeated-pattern:duplicate-definition findings legitimately have file: null (see
  // judgment/score_findings.py's find_duplicate_definitions()) -- the real files ARE named
  // in the finding's free-text description though (Python f-string list/dict repr). Rather
  // than parsing that repr syntax, this checks each already-known real filename for whether
  // its basename appears as a boundary-delimited match in the finding text (D-fix7).
  function findReferencedFiles(files, findingText) {
    if (!findingText) return [];
    return Object.keys(files).filter((relPath) => isBoundedMatch(findingText, relPath.split("/").pop()));
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

  // D-fix1 (연주 audit A3, 2026-07-23): ZIP entry names are attacker-controlled and were
  // used verbatim as Pyodide FS paths in writeTargetFiles() ("/target/" + relPath) with no
  // traversal check -- a crafted entry like "../lib/judgment/score_findings.py" could
  // escape /target and overwrite the real grading logic loaded into /lib moments earlier by
  // ensurePipelineSource().
  //   WHY: reject at parse time (before the path ever enters `files`) rather than only
  //   guarding writeTargetFiles(), so every downstream consumer of `files`
  //   (resolveConnectableFile, findReferencedFiles, etc.) never sees a path outside the
  //   intended tree either. writeTargetFiles() also re-checks (defense-in-depth) in case a
  //   future caller populates `files` some other way.
  //   COST: a legitimate entry name that happens to contain a literal ".." path segment
  //   (no known real-world case observed in any corpus this project has scanned) would also
  //   be rejected.
  //   EXIT: if Pyodide FS's own mkdirTree/writeFile ever gain built-in traversal
  //   protection, this becomes redundant defense-in-depth, not incorrect -- safe to keep
  //   either way.
  function isUnsafeZipPath(name) {
    return !name || name.startsWith("/") || name.split("/").some((seg) => seg === "..");
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
      // D-fix1: reject path-traversal entries before any other categorization -- see
      // isUnsafeZipPath()'s comment above.
      if (isUnsafeZipPath(entry.name)) {
        skippedExtCounts["(안전하지 않은 경로)"] = (skippedExtCounts["(안전하지 않은 경로)"] || 0) + 1;
        continue;
      }
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
    // D214: the no-PAT branch used to say "PAT을 입력하면 해결됩니다" with no pointer to
    // WHERE or WHICH permission -- a real team member hit this and had no idea where the
    // field was or what scope to pick. This message renders as plain textContent (not
    // HTML), so the URL is included as copy-pasteable text, not a clickable link -- the
    // actual clickable link + scope guidance lives in submission.html's GitHub PAT field
    // hint (D214), this just repeats the essential "where/what" at the point of failure.
    return new Error(pat
      ? `GitHub API 요청 한도 초과 (PAT 기준 5,000회/시간) — ${until} 재시도해도 계속 실패합니다`
      : `GitHub API 요청 한도 초과 (비인증 IP당 60회/시간, 같은 네트워크 사용자끼리 공유) — ${until} 재시도해도 계속 실패합니다. 위 "연결 설정" 패널의 GitHub PAT 칸에 토큰을 입력하면 한도가 5,000회/시간으로 늘어나 바로 해결됩니다 (발급: github.com/settings/tokens/new, repo 스코프 체크)`);
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
    // D-fix6 (연주 audit A2, 2026-07-23): this truncation happens on GitHub's own tree
    // response BEFORE isSkippedPath() runs below -- if the repo has build artifacts
    // committed (they'd normally be skipped) sorted ahead of real source in the tree,
    // GitHub's own item-count cap can be spent on those before source ever gets returned.
    // Cannot be fixed here (GitHub API's own limit, not something this fetch controls) --
    // message upgraded to at least explain the likely cause + remedy, mirroring D214's
    // "where/what" guidance pattern for the PAT rate-limit message above.
    if (tree.truncated) onProgress("경고: repo가 커서 GitHub API가 파일 목록을 일부만 반환함 -- 빌드 산출물(node_modules 등)이 커밋돼 있으면 그게 먼저 소진시켰을 수 있음. 산출물을 .gitignore 처리하거나 ZIP 업로드로 전환 권장");

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
    // D200: also return the RESOLVED branch (was already computed above when the caller
    // passed none, just never handed back) -- P03's live fact-check tools need to know
    // exactly which branch was actually scanned, not re-guess/re-resolve it themselves.
    return { files, branch };
  }

  async function ensurePyodide(onProgress) {
    pyodide = await LabPyodide.get(onProgress);
    return pyodide;
  }

  async function ensurePipelineSource(onProgress) {
    if (LabPyodide.isLoaded("p02")) return;
    onProgress("파이프라인 원본 코드 로드 중 (이 저장소 내 cognition/judgment 사본)...");
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
      if (isUnsafeZipPath(relPath)) continue; // D-fix1 defense-in-depth, see isUnsafeZipPath()
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
    // D-tierb1: "p02-3"(Tier B 위험 스캔) 매핑 삭제. 남은 id는 **일부러 재번호하지 않는다** --
    // stage id는 LabApp.getOverride/Supabase runs.overrides에 이미 저장된 키라, p02-4를
    // p02-3으로 당기면 과거 run의 override 기록이 조용히 다른 단계에 붙는다. 비어 있는 p02-3은
    // 결번(缺番)이며 그 자체가 "여기 Tier B가 있었다"는 이력이다.
    const moduleForStage = { "p02-1": "two_tier_scan", "p02-2": "two_tier_scan", "p02-4": "score_findings", "p02-5": "importance_rank" };
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
      // D200: null for ZIP uploads -- there is no repo identity to re-fetch against, and
      // P03's fact-check tools must treat that as an undetectable no-op, not an error.
      let repoRef = null;
      if (input.method === "pat") {
        const repoInput = input.repoInput || "";
        const branch = (input.branch || "").trim() || null;
        if (!repoInput.trim()) throw new Error("repo를 입력하세요 (owner/repo)");
        const { owner, repo } = parseRepoInput(repoInput);
        const pat = LabConfig.get("github-pat");
        hooks.onProgress(`${owner}/${repo} (${branch || "기본 브랜치"}) 가져오는 중...`);
        const fetched = await fetchGithubRepo(owner, repo, branch, pat, (msg) => hooks.onProgress(msg));
        files = fetched.files;
        repoRef = { owner, repo, branch: fetched.branch };
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
      return { result, files, repoRef };
    } catch (err) {
      hooks.onRunEnd(new Date() - startedAt);
      console.error(err);
      hooks.onStatus(`오류: ${err.message}`, "error");
      hooks.onProgress(`오류: ${err.message}`);
      await LabApp.saveFailedRun("p02", null, err, startedAt, hooks.onProgress);
      throw err;
    }
  }

  // D-fix4 (연주 audit I1, 2026-07-23): a Supabase save failure used to append exactly one
  // onProgress log line -- easy to miss in a scrolling log, no retry for what's often a
  // transient network blip, and once the tab/session ends the result is genuinely gone
  // (session-state.js only persists to sessionStorage, not any durable store). From a
  // reviewer's later perspective this made a failed save indistinguishable from "the run
  // never happened."
  //   WHY: (1) retry a couple of times first -- unlike an LLM call this is a fast, cheap
  //   Supabase write, so a short bounded retry costs little and recovers from exactly the
  //   transient-failure case this codebase already assumes elsewhere (worker/nvidia-proxy.js's
  //   own MAX_ATTEMPTS=3 pattern). (2) on final failure, also raise hooks.onStatus(...,
  //   "error") -- the same call run()'s own catch block already uses for a failed run, so
  //   this reuses an existing "the user will actually notice this" UI path instead of only
  //   the scrolling onProgress log.
  //   COST: a genuinely down Supabase project now costs up to 2 extra ~1s round-trips of
  //   latency before the user sees the final failure, instead of failing immediately.
  //   EXIT: retry count (3 total attempts) and delay (1s) are unmeasured/provisional, chosen
  //   to mirror this codebase's existing MAX_ATTEMPTS=3-style conventions rather than fit to
  //   real Supabase transient-failure data -- revisit if real failure logs show a different
  //   pattern. Duplicated in p03-engine.js's maybeSaveRun (separate IIFE, same reasoning --
  //   see that file's own D-fix4 comment).
  async function saveRunWithRetry(payload, attempts = 3, delayMs = 1000) {
    for (let i = 0; i < attempts; i++) {
      try {
        return await LabDB.saveRun(payload);
      } catch (err) {
        if (i === attempts - 1) throw err;
        await new Promise((resolve) => setTimeout(resolve, delayMs));
      }
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
      await saveRunWithRetry({
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
      hooks.onStatus(`DB 저장 실패 -- 팀 기록에 남지 않았습니다: ${err.message}`, "error");
    }
  }

  return {
    resolveConnectableFile, findFileByBasename, findReferencedFiles,
    parseRepoInput, fetchGithubRepo, parseZipFile, formatZipStatus,
    // D200: exported (logic unchanged) so P03's new live-fetch tools can reuse the exact
    // same D192 rate-limit DETECTION instead of re-implementing/drifting from it.
    githubRateLimitError,
    run, MAX_CONNECT_FILES, SRC_EXTS,
  };
})();
