// P01 needs the NVIDIA key + proxy (unlike P02). PDF text extraction uses pdf.js instead
// of the real pipeline's `pdftotext -layout` -- every run is tagged extractor:"pdfjs" so
// results are never silently treated as byte-comparable with a CLI run (PLAN.md P01
// caveat). The prompts themselves are identical to the real pipeline (read from the
// same manifest P02/P03 use, editable the same way).
const P01Runner = (() => {
  let pdfBytes = null;
  let pdfPassword = "";
  let pdfFileName = null; // D175: captured so input_meta.source_filename can identify which document a run analyzed -- previously never saved anywhere
  let selectedModel = null; // initialized from manifest.shared.default_model on first render, then sticky across tab switches

  // D163 (2026-07-15): a real run's refine stage hit 3 straight NVIDIA HTTP 524s right
  // after the 26-way parallel chunk burst finished -- reproducing the identical refine
  // request standalone (no preceding burst) succeeded cleanly in 94.4s, pointing at
  // residual NVIDIA-side congestion from the burst rather than anything wrong with the
  // refine request itself (see README's 524 investigation notes). User asked for a fixed
  // cooldown between the burst finishing and refine starting to let that congestion clear.
  // 60s reuses worker/nvidia-proxy.js's own RATE_LIMIT_RETRY_DELAY_SECONDS=60 (D159's
  // already-established "wait out an NVIDIA load window" constant) rather than a fresh
  // guess -- user's own choice of "1분" lines up with that existing precedent.
  const POST_CHUNK_COOLDOWN_MS = 60_000;

  // D168 (2026-07-15): a real 251-page/26-chunk run hit 5x 429 and 2x 524 across the
  // chunk-analysis burst -- D156's Promise.all() fires all 26 requests (against a single
  // NVIDIA key) in the same instant. nvidia-keypool-guard.py's documented ~40rpm free-tier
  // ceiling / worker's (then-)MAX_ATTEMPTS=3 ≈ 13.3 -- originally capped at 8 (well under
  // that) to cover the pathological every-chunk-retries-3-times-within-the-same-burst
  // case (8*3=24 under 40). Same pattern p02-runner.js's fetchGithubRepo() already uses
  // for GitHub's own rate limit (CONCURRENCY=6 there) -- reused here, not a new one-off
  // idea, just derived from THIS API's own documented ceiling instead of copying that
  // unrelated constant.
  //
  // Raised to 40 (2026-07-15, same day, via an intermediate 13): D169 changed what "worst
  // case" means here -- chunk-analysis retries no longer stack inside the same wave (each
  // attempt is now maxAttempts:1; a retry only happens in a LATER round,
  // ROUND_RETRY_DELAY_MS=60s away). So the *3 that originally justified capping well under
  // 13.3 no longer applies within a single round, and the user pushed the reasoning to its
  // actual conclusion: nvidia-keypool-guard.py's own ~40rpm ceiling IS the number, not
  // something to divide down further.
  //   WHY 40 and not something still under it: post-D169, being "too aggressive" here is
  //     cheap -- any 429s a 40-wide burst causes just become retryable failures the round
  //     loop above already retries 60s later, not a hard failure. The ~40 is still labeled
  //     an approximation (observed 429 history, not a documented contractual limit) and a
  //     teammate's tab or P03 sharing this same key isn't visible to this number -- but the
  //     user weighed that and chose to spend the whole documented budget on this burst
  //     rather than reserve headroom for a scenario (concurrent same-key usage) that isn't
  //     happening in this tool's actual current usage.
  //   COST: zero margin if the ~40 figure is even slightly optimistic, or if something else
  //     is genuinely hitting this same key at the same moment -- expect more 429s to show up
  //     as normal (not alarming) round-2/round-3 activity in the log versus 13.
  //   EXIT: if 429s become the common case rather than the rare one, that's this margin
  //     being wrong in practice -- pull the number back down (13 already proved itself a
  //     working middle ground) rather than adding yet another retry layer on top.
  const CHUNK_CONCURRENCY = 40;

  // D174 Phase 3 (2026-07-15): which of p01-3's issue_type values (see D174 Phase 2's
  // manifest note) are safe to route to the p01-3b apply stage at all. duplicate_exact and
  // unit-boundary issues are restructuring within already-cited real content -- safe for a
  // model to attempt (still gated by validateUnitMapGrounding() afterward). missing_pages
  // is deliberately excluded: "fixing" it would mean inventing a page number, exactly the
  // grounding risk this whole feature has to avoid. coverage_gap_failed_chunk is D169's
  // failed-chunk gap -- per Codex's plan, explicitly out of scope (missing content stays
  // missing, not fabricated to look complete).
  const ACTIONABLE_ISSUE_TYPES = new Set(["duplicate_exact", "overbroad_unit", "boundary_split_needed"]);

  // D169 (2026-07-15): D168 kept the worker's per-job independent retry (D-I) but capped
  // how many jobs could be in flight at once. User asked to go further -- move retry
  // ownership for chunk-analysis to the client entirely, since it (unlike any single
  // worker job) can see every chunk's outcome and coordinate them, instead of 26 jobs each
  // independently guessing when to retry. Each chunk-analysis call now goes out with
  // x-max-attempts:1 (worker attempts it exactly once, see worker/nvidia-proxy.js), and
  // this function collects whichever ones failed in a RETRYABLE way (job.retryable, not a
  // hard client error) and resubmits just those in the next round -- concurrency-capped
  // the same way as the first pass. MAX_RETRY_ROUNDS=3 (1 initial + 2 retry rounds)
  // mirrors the worker's own former MAX_ATTEMPTS=3 total-attempts convention, just
  // coordinated now instead of per-job independent. ROUND_RETRY_DELAY_MS reuses D159's
  // RATE_LIMIT_RETRY_DELAY_SECONDS=60 -- user's own "1분 단위" request lines up with that
  // same existing value, not a fresh guess.
  const MAX_RETRY_ROUNDS = 3;
  const ROUND_RETRY_DELAY_MS = 60_000;

  // D182: MODEL_CHOICES + the toggle-rendering logic moved to app.js (LabApp.MODEL_CHOICES/
  // renderModelToggle) so P03 can use the identical selector -- see app.js for the full
  // rationale comment (D116/D119/D120/D-G tier evidence), unchanged here, just relocated.

  function renderInput(container) {
    if (!selectedModel) selectedModel = (LabApp.getManifest().shared || {}).default_model;
    container.innerHTML = `
      <div class="input-panel">
        <h3>교안 PDF 입력</h3>
        <div class="dropzone" id="p01-dropzone">여기로 PDF를 드래그하거나 클릭해서 선택</div>
        <input type="file" id="p01-pdf-input" accept=".pdf" class="hidden">
        <div class="input-row">
          <input type="password" id="p01-pdf-password" placeholder="PDF 암호 (있는 경우, 서버로 전송되지 않음)">
        </div>
        <p id="p01-pdf-status" style="color:var(--ink-faint); font-size:0.72rem; margin-top:6px;"></p>
        <p style="color:var(--status-blocked); font-size:0.72rem; margin-top:4px;">
          텍스트 추출은 pdf.js를 쓴다 — 원본 파이프라인의 pdftotext -layout과 완전히 동일한 결과는 아니다
          (모든 실행 기록에 extractor: "pdfjs"로 표시됨).
        </p>
        <div class="field-label" style="margin-top:14px;">모델 선택 (${LabApp.MODEL_CHOICES.length}종 — 3개 프롬프트 단계+JSON 복구 전부에 적용)</div>
        <div class="model-toggle-group" id="p01-model-group"></div>
        <p class="model-note" id="p01-model-note"></p>
      </div>`;

    const dropzone = container.querySelector("#p01-dropzone");
    const fileInput = container.querySelector("#p01-pdf-input");
    const passwordInput = container.querySelector("#p01-pdf-password");
    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
      if (e.dataTransfer.files[0]) handlePdfFile(e.dataTransfer.files[0], container);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files[0]) handlePdfFile(fileInput.files[0], container);
    });
    passwordInput.addEventListener("input", () => { pdfPassword = passwordInput.value; });
    renderModelToggle(container);
  }

  function renderModelToggle(container) {
    LabApp.renderModelToggle(container, "#p01-model-group", "#p01-model-note", () => selectedModel, (v) => { selectedModel = v; });
  }

  async function handlePdfFile(file, container) {
    const status = container.querySelector("#p01-pdf-status");
    status.textContent = `읽는 중: ${file.name}...`;
    pdfBytes = new Uint8Array(await file.arrayBuffer());
    pdfFileName = file.name;
    status.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)}MB) 로드됨 — 실행 버튼을 누르면 페이지 수를 확인한다`;
  }

  async function waitForPdfJs() {
    if (window.pdfjsLib) return;
    if (window.pdfjsLibLoadError) throw window.pdfjsLibLoadError;
    await new Promise((resolve) => window.addEventListener("pdfjs-ready", resolve, { once: true }));
    if (window.pdfjsLibLoadError) throw window.pdfjsLibLoadError;
  }

  async function extractPages(onProgress) {
    await waitForPdfJs();
    const doc = await window.pdfjsLib.getDocument({ data: pdfBytes.slice(), password: pdfPassword || undefined }).promise;
    const pages = [];
    for (let i = 1; i <= doc.numPages; i++) {
      const page = await doc.getPage(i);
      const content = await page.getTextContent();
      const text = content.items.map((it) => it.str).join(" ");
      pages.push(text);
      if (i % 10 === 0 || i === doc.numPages) onProgress(`페이지 텍스트 추출 ${i}/${doc.numPages}`);
    }
    return pages;
  }

  // D155 (2026-07-15): max_chunks removed -- it used to cap this at a "smoke test" of 3
  // chunks by default, which a user could raise to null for a full run, but nothing
  // stopped it from silently staying at 3 for a real document. User asked for this to be
  // structural: chunk count is always ceil(pages/chunkSize), covering the whole PDF, with
  // no control anywhere that could leave a run silently partial.
  function buildChunks(pages, chunkSize) {
    const chunks = [];
    for (let start = 1; start <= pages.length; start += chunkSize) {
      const end = Math.min(start + chunkSize - 1, pages.length);
      const text = pages.slice(start - 1, end).join("\n");
      chunks.push({ start, end, range: `${start}-${end}`, text });
    }
    return chunks;
  }

  function makeUnitMap(chunkResults) {
    const unitMap = {};
    for (const chunk of chunkResults) {
      for (const unit of chunk.units || []) {
        const unitId = String(unit.unit_id || "unknown");
        if (!unitMap[unitId]) {
          unitMap[unitId] = { unit_id: unitId, unit_title: unit.unit_title || "", source_pages: [], concepts: [], code_examples: [], cautions: [] };
        }
        unitMap[unitId].source_pages.push(...(unit.source_pages || []));
      }
      // D1 (2026-07-21): concept.unit_id added to the p01-2 schema specifically so a
      // chunk spanning multiple units routes each concept to the right one -- previously
      // EVERY concept in a chunk was forced onto chunk.units[0] regardless of which unit
      // it actually described. Falls back to that same chunk.units[0] behavior only when
      // the model didn't supply a valid unit_id (defensive, not the expected path).
      const chunkUnitIds = new Set((chunk.units || []).map((u) => String(u.unit_id || "unknown")));
      const fallbackUnit = (chunk.units && chunk.units[0] && String(chunk.units[0].unit_id)) || "unknown";
      for (const concept of chunk.concepts || []) {
        const declaredUnit = concept.unit_id !== undefined && concept.unit_id !== null ? String(concept.unit_id) : null;
        const targetUnit = declaredUnit && chunkUnitIds.has(declaredUnit) ? declaredUnit : fallbackUnit;
        if (!unitMap[targetUnit]) continue;
        const item = { name: concept.name || "unnamed", summary: concept.summary || "", evidence: concept.evidence || "", source_pages: concept.source_pages || [], chunk_range: chunk.chunk_range };
        const kind = concept.kind || "concept";
        const bucket = kind === "code_example" ? "code_examples" : kind === "caution" ? "cautions" : "concepts";
        unitMap[targetUnit][bucket].push(item);
      }
    }
    for (const u of Object.values(unitMap)) u.source_pages = [...new Set(u.source_pages)].sort((a, b) => a - b);
    return unitMap;
  }

  // D174 Phase 1 (2026-07-15): D172's refine loop re-audits an unchanged unit_map every
  // round (refine_once() only ever produces a report, never edits unit_map) -- Codex's
  // plan (asked for by the user) for closing that gap starts with the cheapest,
  // zero-risk win: deterministic cleanup that can't corrupt grounding, run BEFORE the
  // model ever sees the data. Exact-duplicate concepts (same name + same page set) are
  // genuinely redundant, not a judgment call. An item with zero source_pages is, by this
  // whole tool's own premise, not a real finding -- it's dropped rather than kept and
  // silently treated as grounded (counted+logged so the drop is visible, not silent).
  //   WHY: safe to run unconditionally, no LLM call, no risk of inventing content.
  //   COST: none observed -- purely subtractive (dedup, drop-ungrounded), never adds data.
  //   EXIT: if exact-name+page matching proves too narrow (near-duplicates with slightly
  //     different names slip through), that's Phase 3's job (duplicate_exact issue_type
  //     routed to the model apply stage), not a reason to make this pass fuzzier.
  function normalizeUnitMap(unitMap, pipelineId) {
    let droppedUngrounded = 0;
    let droppedDuplicates = 0;
    const normalized = {};
    for (const [unitId, unit] of Object.entries(unitMap)) {
      const dedupeItems = (items) => {
        const seen = new Set();
        const result = [];
        for (const item of items || []) {
          const pages = [...new Set(item.source_pages || [])].sort((a, b) => a - b);
          if (!pages.length) { droppedUngrounded += 1; continue; }
          const key = `${item.name || ""}::${pages.join(",")}`;
          if (seen.has(key)) { droppedDuplicates += 1; continue; }
          seen.add(key);
          result.push({ ...item, source_pages: pages });
        }
        return result;
      };
      normalized[unitId] = {
        ...unit,
        source_pages: [...new Set(unit.source_pages || [])].sort((a, b) => a - b),
        concepts: dedupeItems(unit.concepts),
        code_examples: dedupeItems(unit.code_examples),
        cautions: dedupeItems(unit.cautions),
      };
    }
    if ((droppedUngrounded || droppedDuplicates) && pipelineId) {
      LabApp.log(pipelineId, `unit_map 정리: 근거 없는 항목 ${droppedUngrounded}개 제외, 중복 항목 ${droppedDuplicates}개 병합`);
    }
    return normalized;
  }

  function countGroundedItems(unitMap) {
    return Object.values(unitMap).reduce(
      (sum, u) => sum + (u.concepts || []).length + (u.code_examples || []).length + (u.cautions || []).length, 0
    );
  }

  // D174 Phase 1/3: the one safety net every unit_map revision (Phase 3's model-driven
  // fix included) must pass before being accepted. Rejects rather than repairs -- an
  // invalid revision just means "the fix step failed this time", handled the same way any
  // other failed LLM call is (log it, keep the previous known-good state, move on).
  //   WHY: this pipeline's whole premise is page-grounded claims -- a fix step that only
  //     sees the already-summarized unit_map (not original chunk text) can invent or
  //     misattribute content while "cleaning up" structure. This is the check that catches
  //     that, not a court of last resort.
  //   COST: a real, useful restructuring that happens to also trip this (e.g. a legitimate
  //     20%+ reduction from merging genuinely-duplicate items) gets rejected too --
  //     conservative by design, false-rejects are a much cheaper mistake here than
  //     false-accepts of corrupted grounding.
  //   EXIT: if false-rejects turn out to be common in practice, loosen the item-count-drop
  //     threshold (currently 0.8, not measured -- a provisional guess) with real data, not
  //     by removing the check.
  function validateUnitMapGrounding(revisedMap, originalMap, successfulPages) {
    const originalCount = countGroundedItems(originalMap);
    const revisedCount = countGroundedItems(revisedMap);
    if (originalCount > 0 && revisedCount < originalCount * 0.8) {
      return { ok: false, reason: `근거 있는 항목 수 급감(${originalCount}개→${revisedCount}개, 20%+ 감소) — 내용 유실 의심` };
    }
    for (const [unitId, unit] of Object.entries(revisedMap)) {
      for (const group of ["concepts", "code_examples", "cautions"]) {
        for (const item of unit[group] || []) {
          if (!item.source_pages || !item.source_pages.length) {
            return { ok: false, reason: `유닛 ${unitId}의 "${item.name}"에 source_pages 없음` };
          }
          for (const page of item.source_pages) {
            if (!successfulPages.has(page)) {
              return { ok: false, reason: `유닛 ${unitId}의 "${item.name}"이 분석되지 않은 페이지(${page})를 인용함` };
            }
          }
          if (!item.evidence) {
            return { ok: false, reason: `유닛 ${unitId}의 "${item.name}"에 evidence 없음` };
          }
        }
      }
    }
    return { ok: true };
  }

  // D174 Phase 3: grounding context for the apply stage -- the ORIGINAL per-chunk
  // analysis results (not the already-condensed unit_map) for whichever chunks overlap
  // the affected units' pages, so a fix can point back to real extracted evidence instead
  // of only seeing unit_map's own summary of itself.
  function chunkResultsForUnits(unitIds, unitMap, chunkState) {
    const pages = new Set();
    for (const uid of unitIds) {
      for (const p of (unitMap[uid] && unitMap[uid].source_pages) || []) pages.add(p);
    }
    return chunkState
      .filter((s) => s.result && !s.result.error)
      .filter((s) => {
        for (let p = s.chunk.start; p <= s.chunk.end; p++) if (pages.has(p)) return true;
        return false;
      })
      .map((s) => s.result);
  }

  function subsetUnitMap(unitMap, unitIds) {
    const subset = {};
    for (const id of unitIds) if (unitMap[id]) subset[id] = unitMap[id];
    return subset;
  }

  // D-P01-PARALLEL-FIX (2026-07-21): groups refine's actionableIssues by connected
  // components of affected_unit_ids (union-find) so run()'s fix stage can send one
  // p01-3b call per independent cluster in parallel instead of one call covering every
  // issue -- same wall-clock-overlap rationale D156 already established for chunk
  // analysis (NVIDIA per-call latency doesn't shrink with a smaller prompt, per D183's
  // own measurement, but N concurrent calls still finish sooner in wall-clock than N
  // sequential ones).
  //   WHY: a real 251-page run's single p01-3b call took 588s across 3 attempts and
  //     still failed (NVIDIA HTTP 524) -- when the audit's issues span independent unit
  //     clusters, splitting lets one cluster's failure/slowness not block another's.
  //   COST: a cluster's fix call sees only its own units as unit_map_json context (not
  //     the full map) once there are 2+ clusters, so cross-unit visibility narrows for
  //     issue types that don't already name every relevant unit in affected_unit_ids.
  //     Only duplicate_exact/overbroad_unit/boundary_split_needed are actionable (see
  //     ACTIONABLE_ISSUE_TYPES) and duplicate_exact always lists every unit it spans, so
  //     this is a narrow cost in practice, not eliminated.
  //   EXIT: run() only takes this path when groupIssuesByUnits() returns 2+ groups --
  //     the single-group case (every real run observed so far) is untouched: full
  //     unit_map context, one call, identical to the pre-parallel behavior. To disable
  //     entirely, make groupIssuesByUnits always return one group.
  // Issues with no affected_unit_ids (existing "미상"/unknown fallback) can't be scoped
  // safely, so they always collect into their own unitIds:[] group, which fixGroup()
  // below treats as "use the full map" regardless of how many groups there are.
  function groupIssuesByUnits(issues) {
    const withUnits = issues.filter((iss) => iss.affected_unit_ids && iss.affected_unit_ids.length);
    const withoutUnits = issues.filter((iss) => !iss.affected_unit_ids || !iss.affected_unit_ids.length);

    const parent = new Map();
    function find(x) { while (parent.get(x) !== x) x = parent.get(x); return x; }
    function union(a, b) { const ra = find(a), rb = find(b); if (ra !== rb) parent.set(ra, rb); }
    for (const iss of withUnits) {
      for (const id of iss.affected_unit_ids) if (!parent.has(id)) parent.set(id, id);
      for (let k = 1; k < iss.affected_unit_ids.length; k++) union(iss.affected_unit_ids[0], iss.affected_unit_ids[k]);
    }

    const buckets = new Map(); // root unit id -> {unitIds:Set, issues:[]}
    for (const iss of withUnits) {
      const root = find(iss.affected_unit_ids[0]);
      if (!buckets.has(root)) buckets.set(root, { unitIds: new Set(), issues: [] });
      const b = buckets.get(root);
      iss.affected_unit_ids.forEach((id) => b.unitIds.add(id));
      b.issues.push(iss);
    }
    const groups = [...buckets.values()].map((b) => ({ unitIds: [...b.unitIds], issues: b.issues }));
    if (withoutUnits.length) groups.push({ unitIds: [], issues: withoutUnits });
    return groups;
  }

  // Scoped to a single-unit slice of unitMap (e.g. {[unitId]: unit}) by callers that
  // want per-unit question generation -- see D173 below.
  function buildGraphNodes(unitMap) {
    const nodes = [];
    for (const [unitId, unit] of Object.entries(unitMap)) {
      nodes.push({ id: `unit:${unitId}`, label: `Unit ${unitId} ${unit.unit_title}`.trim(), type: "unit", source_pages: unit.source_pages });
      for (const [group, relation] of [["concepts", "concept"], ["code_examples", "code_example"], ["cautions", "caution"]]) {
        (unit[group] || []).forEach((item, idx) => {
          nodes.push({ id: `${group}:${unitId}:${idx + 1}`, label: item.name, type: relation, source_pages: item.source_pages, summary: item.summary });
        });
      }
    }
    return nodes;
  }

  // D173 (2026-07-15): the real pipeline's build_graph() (scripts/java_curriculum_nvidia_
  // pipeline.py:451) returns BOTH nodes and links (contains_unit/teaches/shows_code/warns/
  // sourced_by/audits/found_issue/issue_page relations) -- this web tool's port
  // (buildGraphNodes above) only ever built the flat node list, dropping every
  // relationship, and the result wasn't even included in the saved/displayed output at
  // all. User asked directly whether the graph relationships were missing -- confirmed by
  // reading the real function: yes, both the edges themselves and the graph's presence in
  // the result were gaps. This ports build_graph()'s full logic (same id scheme, same
  // relation names) so the result JSON actually matches what the real pipeline produces,
  // not just a node list.
  function buildGraph(unitMap, audits) {
    const nodes = [];
    const links = [];
    const seen = new Set();
    function addNode(id, label, type, attrs) {
      if (seen.has(id)) return;
      seen.add(id);
      nodes.push({ id, label, type, ...(attrs || {}) });
    }
    function addLink(source, target, relation, attrs) {
      links.push({ source, target, relation, ...(attrs || {}) });
    }
    addNode("doc:curriculum", "curriculum", "document");
    for (const [unitId, unit] of Object.entries(unitMap)) {
      const uid = `unit:${unitId}`;
      addNode(uid, `Unit ${unitId} ${unit.unit_title || ""}`.trim(), "unit", { source_pages: unit.source_pages || [] });
      addLink("doc:curriculum", uid, "contains_unit");
      for (const [group, relation] of [["concepts", "teaches"], ["code_examples", "shows_code"], ["cautions", "warns"]]) {
        (unit[group] || []).forEach((item, idx) => {
          const cid = `${group}:${unitId}:${idx + 1}`;
          const pages = item.source_pages || [];
          addNode(cid, item.name || cid, group.endsWith("s") ? group.slice(0, -1) : group, {
            summary: item.summary || "", evidence: item.evidence || "", source_pages: pages, chunk_range: item.chunk_range || "",
          });
          addLink(uid, cid, relation);
          for (const page of pages) {
            const pid = `page:${page}`;
            addNode(pid, `p${page}`, "page", { page });
            addLink(cid, pid, "sourced_by");
          }
        });
      }
    }
    (audits || []).forEach((audit, idx) => {
      const aid = `audit:${audit.iteration || idx + 1}`;
      addNode(aid, `refine iteration ${audit.iteration}`, "refine_audit", { status: audit.status });
      addLink(aid, "doc:curriculum", "audits");
      (audit.issues || []).forEach((issue, iidx) => {
        const iid = `${aid}:issue:${iidx + 1}`;
        const pages = issue.source_pages || [];
        addNode(iid, issue.issue || iid, "refine_issue", { severity: issue.severity, source_pages: pages });
        addLink(aid, iid, "found_issue");
        for (const page of pages) {
          const pid = `page:${page}`;
          addNode(pid, `p${page}`, "page", { page });
          addLink(iid, pid, "issue_page");
        }
      });
    });
    return {
      directed: true, multigraph: false,
      graph: { name: "curriculum_page_grounded_graph", schema: "graphify-compatible node-link" },
      nodes, links,
    };
  }

  // D158 (2026-07-15): a real 251-page/26-chunk run hit 2 chunks that failed JSON
  // parsing ("Expected ',' or ']' after array element" -- the signature of a response
  // cut off mid-array) even after the repair-prompt fallback below. The repair prompt
  // asks the model to fix malformed JSON, but if the real cause is "ran out of
  // max_tokens before finishing", asking it to reformat the same truncated text at the
  // same token budget can't help -- it needs more room, not a second opinion. Now
  // branches on the actual finishReason instead of guessing: a real length cutoff
  // retries the ORIGINAL prompt at 2x max_tokens; anything else (a genuine malformed-
  // JSON mistake, finishReason "stop") goes through the existing repair-prompt path,
  // unchanged.
  // D165 (2026-07-15): a real run's p01-4 (question generation, 6-unit graph,
  // stepfun-ai/step-3.5-flash) got finish_reason=length at 1800, retried once at 3600
  // per D158 above -- and STILL got length-truncated, so the one-shot retry gave up and
  // the whole stage silently fell back to an empty result. Rather than guess a new fixed
  // ceiling for this one stage (the same evidence says a bigger document/unit count could
  // make even that wrong again), keep doubling while the model keeps reporting
  // finish_reason=length -- same signal-driven principle as D158, just applied until the
  // signal actually stops instead of only once. Capped at MAX_LENGTH_DOUBLINGS to match
  // worker/nvidia-proxy.js's own MAX_ATTEMPTS=3 convention (1 original + 2 doublings = 3
  // total attempts), not doubled forever -- an unbounded ceiling would trade truncation
  // failures for a different one (very large max_tokens means very long non-streaming
  // generation, which the 524 investigation already found raises timeout risk).
  const MAX_LENGTH_DOUBLINGS = 2;
  // D169: opts.maxAttempts forwarded to every LabLLM.chatJSON call this function makes
  // (initial, length-doubling retries, and the repair-prompt fallback alike) -- undefined
  // (the default, used by refine/question-gen callers below) preserves the worker's
  // existing MAX_ATTEMPTS=3 auto-retry untouched.
  async function callPromptStage(stageId, values, opts = {}) {
    const system = LabApp.resolveTemplate("p01", stageId, "system");
    const template = LabApp.resolveTemplate("p01", stageId, "user_template");
    const userMsg = LabApp.fillTemplate(template, values);
    const baseMaxTokens = LabApp.resolveParam("p01", stageId, "max_tokens") || 1800;
    const model = LabApp.resolveParam("p01", stageId, "model") || selectedModel;
    const messages = [{ role: "system", content: system }, { role: "user", content: userMsg }];
    const maxAttempts = opts.maxAttempts;

    let maxTokens = baseMaxTokens;
    let choice = await LabLLM.chatJSON({ model, messages, maxTokens, maxAttempts });
    try {
      return LabLLM.extractJsonObject(choice.content);
    } catch (e) {
      if (choice.finishReason === "length") {
        for (let attempt = 1; attempt <= MAX_LENGTH_DOUBLINGS; attempt++) {
          maxTokens *= 2;
          choice = await LabLLM.chatJSON({ model, messages, maxTokens, maxAttempts });
          try {
            return LabLLM.extractJsonObject(choice.content);
          } catch (e2) {
            if (choice.finishReason !== "length") {
              throw new Error(`응답 잘림 재시도 중(max_tokens ${maxTokens}) 다른 사유로 파싱 실패: ${e2.message}`);
            }
            // still length-truncated -- loop again at a higher budget unless out of attempts
          }
        }
        throw new Error(`응답이 계속 잘림(finish_reason=length) → max_tokens ${maxTokens}까지 올려도 여전히 파싱 실패`);
      }
      // mirrors chat_json()'s repair-prompt fallback in the real pipeline -- for
      // finishReason other than "length" (a genuine malformed-JSON mistake, not a
      // token-budget problem)
      const repairStage = LabApp.getStage("p01", "p01-json-repair");
      const repairMsg = LabApp.fillTemplate(repairStage.user_template, { malformed_content: (choice.content || "").slice(0, 14000) });
      const repaired = await LabLLM.chatJSON({ model, messages: [{ role: "system", content: repairStage.system }, { role: "user", content: repairMsg }], maxTokens: baseMaxTokens, maxAttempts });
      return LabLLM.extractJsonObject(repaired.content);
    }
  }

  // D-skipQG (2026-07-22): opts.skipQuestionGen (default false/omitted) lets a caller
  // stop right after unit_map is finalized, skipping the per-unit question-generation
  // wave entirely -- added for curriculum-manager, whose "구조" table only ever reads
  // unit_map (never questions), so that whole LLM wave used to run and be thrown away
  // unread. Every existing caller (the main P01 tab, any other page) passes no opts and
  // is completely unaffected -- this option only ever REMOVES work, never changes what
  // gets produced when it's left off.
  async function run(opts = {}) {
    const pipelineId = "p01";
    LabApp.setStatus(pipelineId, "실행 중...", "running");
    const startedAt = new Date();
    LabApp.startTimer(pipelineId);
    try {
      if (!pdfBytes) throw new Error("PDF를 먼저 업로드하세요");
      if (!LabConfig.get("nvidia-key") || !LabConfig.get("proxy-url")) {
        throw new Error("P01은 LLM 호출이 필요합니다 — 상단 연결 설정에 NVIDIA 키와 프록시 URL을 입력하세요");
      }
      const courseLabel = LabApp.resolveParam("p01", "p01-2", "course_label") || "Java";
      const chunkSize = LabApp.resolveParam("p01", "p01-1", "chunk_size") || 10;

      LabApp.log(pipelineId, `모델: ${selectedModel}`);
      LabApp.log(pipelineId, "PDF 텍스트 추출 중 (pdf.js)...");
      const pages = await extractPages((msg) => LabApp.log(pipelineId, msg));
      const chunks = buildChunks(pages, chunkSize);
      LabApp.log(pipelineId, `${pages.length}페이지 → ${chunks.length}개 청크 (chunk_size=${chunkSize}, 전체 문서)`);

      // D156 (2026-07-15): chunks analysed in parallel instead of one-at-a-time -- they
      // don't depend on each other (makeUnitMap() below just aggregates whatever comes
      // back, order doesn't matter), so nothing downstream assumes these arrive
      // sequentially.
      //
      // D169 (2026-07-15): D168 capped how many chunk jobs could be in flight at once
      // (CHUNK_CONCURRENCY) but still let each job retry independently inside the worker
      // (D-I) -- 26 uncoordinated jobs each guessing when to retry. User asked to move
      // retry ownership to the client instead, since THIS function can see every chunk's
      // outcome and coordinate them. Each chunk-analysis call now goes out with
      // maxAttempts:1 (worker tries it exactly once -- see worker/nvidia-proxy.js), and
      // chunkState tracks every chunk's status across rounds: round 1 attempts all of
      // them, and each following round retries only whichever ones failed in a way the
      // worker marked `retryable` (a genuine NVIDIA/network hiccup, not e.g. a malformed-
      // request client error) -- up to MAX_RETRY_ROUNDS total, with a shared
      // ROUND_RETRY_DELAY_MS pause between rounds so a retry round doesn't immediately
      // re-hit whatever just rate-limited the previous one. Concurrency within any single
      // round's wave is still capped at CHUNK_CONCURRENCY, processed with a real `for`
      // loop (not `forEach(async...)`, which wouldn't actually wait between iterations and
      // would silently defeat the cap -- confirmed via an independent Codex read of this
      // exact code before implementing).
      const chunkState = chunks.map((chunk) => ({ chunk, result: null, err: null, retryable: false }));
      for (let round = 1; round <= MAX_RETRY_ROUNDS; round++) {
        const targets = chunkState.filter((s) => !s.result && (round === 1 || s.retryable));
        if (!targets.length) break;
        // D171 (2026-07-15): "26개를 40개씩 나눠"는 정말 나눌 때(targets.length >
        // CHUNK_CONCURRENCY)만 말이 됨 -- 전체가 한 파도에 다 들어가는 흔한 경우(D170
        // 이후 40 이하 문서는 전부 이 경우)에도 항상 "나눠"라고 말해서 실제로 안 일어나는
        // 일을 하는 것처럼 보였음. 파도가 실제로 여러 개일 때만 그 표현을 쓰고, 하나면
        // 그냥 "동시 분석"이라고 정확히 말함.
        const waveCount = Math.ceil(targets.length / CHUNK_CONCURRENCY);
        LabApp.log(pipelineId, round === 1
          ? (waveCount > 1
              ? `청크 ${targets.length}개를 ${CHUNK_CONCURRENCY}개씩 나눠 분석 시작: p${targets.map((s) => s.chunk.range).join(", p")}`
              : `청크 ${targets.length}개 동시 분석 시작: p${targets.map((s) => s.chunk.range).join(", p")}`)
          : `청크 재시도 라운드 ${round}/${MAX_RETRY_ROUNDS} (${targets.length}개): p${targets.map((s) => s.chunk.range).join(", p")}`);
        for (let i = 0; i < targets.length; i += CHUNK_CONCURRENCY) {
          const wave = targets.slice(i, i + CHUNK_CONCURRENCY);
          await Promise.all(wave.map(async (state) => {
            const chunk = state.chunk;
            try {
              const result = await callPromptStage("p01-2", {
                course_label: courseLabel, chunk_range: chunk.range, chunk_start: chunk.start, chunk_end: chunk.end,
                chunk_text: chunk.text.slice(0, 18000),
              }, { maxAttempts: 1 });
              result.chunk_range = result.chunk_range || chunk.range;
              state.result = result;
              LabApp.log(pipelineId, `청크 p${chunk.range} 완료`);
            } catch (err) {
              state.err = err;
              state.retryable = !!err.retryable;
              LabApp.log(pipelineId, `청크 p${chunk.range} 실패${state.retryable ? " (다음 라운드에 재시도)" : ""}: ${err.message}`);
            }
          }));
        }
        const stillRetryable = chunkState.filter((s) => !s.result && s.retryable);
        if (!stillRetryable.length) break; // nothing left worth retrying
        if (round < MAX_RETRY_ROUNDS) {
          LabApp.log(pipelineId, `${stillRetryable.length}개 재시도 대기 중 (${ROUND_RETRY_DELAY_MS / 1000}초)...`);
          await new Promise((resolve) => setTimeout(resolve, ROUND_RETRY_DELAY_MS));
        }
      }

      const chunkResults = chunkState.map((s) => s.result || {
        chunk_range: s.chunk.range, units: [], concepts: [], error: String(s.err ? s.err.message : "알 수 없는 오류"),
      });
      const failedChunks = chunkResults.filter((c) => c.error);
      if (failedChunks.length) {
        LabApp.log(pipelineId, `⚠ 청크 ${failedChunks.length}개 분석 실패 (p${failedChunks.map((c) => c.chunk_range).join(", p")}) -- unit_map에서 빠짐`);
      }
      // `let` (not `const`): Phase 3's apply stage below can replace this with a
      // validated revision between refine rounds.
      let unitMap = normalizeUnitMap(makeUnitMap(chunkResults.filter((c) => !c.error)), pipelineId);
      LabApp.log(pipelineId, `unit_map 생성: 유닛 ${Object.keys(unitMap).length}개`);
      // Every page any successfully-analyzed chunk actually covered -- the grounding
      // fence Phase 3's validation checks revised source_pages against, so a fix can
      // never cite a page nothing here actually looked at.
      const successfulPages = new Set(
        chunkState.filter((s) => s.result).flatMap((s) => {
          const pages = [];
          for (let p = s.chunk.start; p <= s.chunk.end; p++) pages.push(p);
          return pages;
        })
      );

      // D163: give NVIDIA-side congestion from the burst above a chance to clear before
      // firing the next (sequential, single) request -- see the constant's own comment.
      // D-skipRefine: that "next request" is specifically refine's p01-3 call below, so
      // skip the wait too when opts.skipRefine means there won't be one -- otherwise this
      // burns 60s protecting a request that was never going to happen.
      if (!opts.skipRefine) {
        LabApp.log(pipelineId, `NVIDIA 서버 부하 완화 대기 중 (${POST_CHUNK_COOLDOWN_MS / 1000}초 쿨다운)...`);
        await new Promise((resolve) => setTimeout(resolve, POST_CHUNK_COOLDOWN_MS));
      }

      // D172 (2026-07-15): the real pipeline (scripts/java_curriculum_nvidia_pipeline.py,
      // --refine-iters, default 2) runs a fixed count and never actually reads
      // checklist.question_generation_ready anywhere -- confirmed by reading that source
      // directly, including its own exception-fallback audit which hardcodes
      // "question_generation_ready": True. So a fixed loop wasn't a web-tool bug, it's an
      // exact port of the real pipeline's actual (if confusingly-named) behavior. User
      // explicitly asked for the web tool to diverge here: keep looping until the
      // checklist says it's actually ready, not just N times. This is the first place
      // this file's P01 stages intentionally behave differently from the real pipeline --
      // everywhere else (prompts, params, chunk/refine/question-gen shapes) still mirrors
      // it exactly.
      //   WHY: user's stated intent for this one gate, verified against real pipeline
      //     behavior first rather than assumed.
      //   COST: this only changes the STOP condition, not what refine actually does --
      //     refine_once() only ever produces an audit report, it never modifies unit_map,
      //     so if the same structural issues (e.g. "unit_boundaries_clear: false" because
      //     one giant chunk-derived unit spans the whole document) hold across iterations,
      //     re-auditing the SAME unchanged unit_map may just repeat similar verdicts and
      //     still hit maxRefineIters without ever passing -- looping alone can't fix
      //     something only an "apply the suggested fixes back to unit_map" step could.
      //   EXIT: if this reliably maxes out without passing on real documents, the next
      //     step is making refine's issues/suggested_fix actually revise unit_map between
      //     iterations, not raising maxRefineIters further.
      // D174 Phase 3 (2026-07-15): extends D172's checklist loop with the actual "apply
      // the fix" step Codex's plan called for. Per round: audit the CURRENT unitMap; if
      // ready, stop; otherwise pull out the actionable issues (ACTIONABLE_ISSUE_TYPES)
      // and, if there's at least one AND another audit round remains to benefit from it,
      // call p01-3b with unitMap + those issues + real chunk-level evidence for the
      // affected units. The result is normalized (Phase 1) and run through
      // validateUnitMapGrounding() -- only a PASSING revision replaces unitMap; a failing
      // one is logged and discarded, next round just re-audits the unchanged map (same as
      // D172's original behavior when no fix is attempted). fixLog (Phase 4) records every
      // attempt, applied or not, for the final result/DB.
      // D-skipRefine (2026-07-22): opts.skipRefine (default false/omitted) lets a caller
      // stop right after chunk-analysis's unit_map is built, skipping the whole audit+fix
      // loop below. Added for curriculum-manager alongside skipQuestionGen, after directly
      // reading p01-3b's own note: "no real-pipeline equivalent -- refine_once() [the
      // original CLI's function] does not modify unit_map". The auto-fix loop is a
      // web-tool-only polish layer on top of what chunk-analysis's makeUnitMap() already
      // produces, not something unit_map's basic validity depends on -- buildGraph()
      // already treats `audits` as optional ((audits || []).forEach(...), confirmed by
      // reading it) so an empty array here changes nothing about graph generation.
      //   WHY: curriculum-manager only ever reads unit_map (never refine_audits/
      //     refine_fixes), and real usage showed this loop can be expensive without
      //     helping: a real 181-page document had all 4 auto-fix attempts rejected by
      //     validateUnitMapGrounding (evidence dropped 20%+ each time, same unit group
      //     every retry with nothing changed in between to make a retry more likely to
      //     succeed), burning ~22 minutes for zero net effect on the final unit_map.
      //   COST: loses refine's occasional genuine improvement too (a small clean test
      //     document had 2 fixes actually applied and passed the checklist in 3 rounds) --
      //     this option trades that upside away along with the downside.
      //   EXIT: if curriculum-manager ever starts reading refine_audits/refine_fixes, or
      //     large-document reliability improves (e.g. a future fix caps retries per unit
      //     group instead of blindly repeating an already-rejected one), reconsider
      //     defaulting this off instead of on for that caller.
      const audits = [];
      const fixLog = [];
      // D-refineSnapshot (2026-07-22): opts.captureRefineSnapshots (default false) records
      // a deep-cloned unitMap after iteration 0 (the chunk-analysis baseline, before any
      // refine) and after every refine iteration that actually runs -- lets a caller
      // measure refine's real per-iteration effect (graph density, empty-unit count, etc.
      // via buildGraph() on each snapshot) from ONE pipeline run instead of re-running
      // chunk-analysis N times, which would confound the comparison with chunk-analysis's
      // own run-to-run variability (each chunk is an independent, context-less LLM call).
      // No effect on any existing caller: default off, and capturing is pure bookkeeping
      // that never touches unitMap/control flow.
      const refineSnapshots = [];
      function snapshotRefineState(iteration) {
        if (!opts.captureRefineSnapshots) return;
        refineSnapshots.push({ iteration, unitMap: JSON.parse(JSON.stringify(unitMap)) });
      }
      snapshotRefineState(0);
      if (opts.skipRefine) {
        LabApp.log(pipelineId, "refine 단계 건너뜀 (skipRefine)");
      } else {
        const maxRefineIters = LabApp.resolveParam("p01", "p01-3", "refine_iters") || 5;
        let refineReady = false;
        for (let i = 1; i <= maxRefineIters; i++) {
          // try/finally (not a plain statement after the loop body) so the snapshot is
          // taken exactly once per iteration regardless of which of this loop's several
          // continue/break exits was taken -- finally always runs before control leaves.
          try {
          LabApp.log(pipelineId, `refine 반복 ${i}/${maxRefineIters}...`);
          let audit;
          try {
            audit = await callPromptStage("p01-3", {
              course_label: courseLabel, iteration: i, unit_map_json: JSON.stringify(unitMap).slice(0, 24000),
            });
            audits.push(audit);
          } catch (err) {
            LabApp.log(pipelineId, `refine ${i} 실패: ${err.message}`);
            continue;
          }
          if (audit.checklist && audit.checklist.question_generation_ready === true) {
            refineReady = true;
            LabApp.log(pipelineId, `refine checklist 통과(question_generation_ready) — ${i}회 만에 종료`);
            break;
          }

          const actionableIssues = (audit.issues || []).filter((iss) => ACTIONABLE_ISSUE_TYPES.has(iss.issue_type));
          if (!actionableIssues.length) continue; // nothing this stage can safely act on
          if (i >= maxRefineIters) continue; // no next audit round left to benefit from a fix

          const affectedUnitIds = [...new Set(actionableIssues.flatMap((iss) => iss.affected_unit_ids || []))];
          const groups = groupIssuesByUnits(actionableIssues);
          const useFullMapContext = groups.length <= 1; // see groupIssuesByUnits' EXIT note
          if (groups.length > 1) {
            LabApp.log(pipelineId, `refine 이슈 ${actionableIssues.length}건을 독립 유닛그룹 ${groups.length}개로 나눠 병렬 수정 시도 (${groups.map((g) => g.unitIds.join("+") || "미상").join(", ")})...`);
          } else {
            LabApp.log(pipelineId, `refine 이슈 ${actionableIssues.length}건(유닛 ${affectedUnitIds.join(", ") || "미상"}) 자동 수정 시도...`);
          }

          // Closes over unitMap/courseLabel/chunkState/successfulPages/pipelineId --
          // deliberately a local function (not top-level like groupIssuesByUnits/
          // subsetUnitMap) since none of those make sense outside a single run().
          async function fixGroup(group) {
            const needsFullMap = useFullMapContext || !group.unitIds.length;
            const scopedUnitIds = needsFullMap ? Object.keys(unitMap) : group.unitIds;
            const scopedUnitMap = needsFullMap ? unitMap : subsetUnitMap(unitMap, group.unitIds);
            const fixResult = await callPromptStage("p01-3b", {
              course_label: courseLabel,
              unit_map_json: JSON.stringify(scopedUnitMap).slice(0, 24000),
              issues_json: JSON.stringify(group.issues).slice(0, 8000),
              source_chunks_json: JSON.stringify(chunkResultsForUnits(scopedUnitIds, unitMap, chunkState)).slice(0, 16000),
            });
            const revised = normalizeUnitMap(fixResult.revised_unit_map || {});
            const validation = validateUnitMapGrounding(revised, scopedUnitMap, successfulPages);
            return { group, revised, validation, fixResult };
          }

          const results = await Promise.all(groups.map((g) => fixGroup(g).catch((err) => ({ group: g, error: err }))));
          for (const r of results) {
            const label = r.group.unitIds.join(",") || "미상";
            if (r.error) {
              fixLog.push({ iteration: i, applied: false, reason: r.error.message, changes: [], units: r.group.unitIds });
              LabApp.log(pipelineId, `refine 수정 시도 실패(유닛 ${label}): ${r.error.message}`);
              continue;
            }
            if (r.validation.ok) {
              // revised_unit_map's prompt contract requires every unit it was SHOWN back
              // (full map when needsFullMap, else just this group's units) -- a unit from
              // this group missing in the response means the model merged/renamed it away,
              // so drop the stale key before merging the rest in. Units outside this group
              // are never touched here, which is exactly what makes the groups safe to run
              // concurrently.
              for (const oldId of r.group.unitIds) if (!(oldId in r.revised)) delete unitMap[oldId];
              Object.assign(unitMap, r.revised);
              fixLog.push({ iteration: i, applied: true, changes: r.fixResult.changes || [], unresolved_issues: r.fixResult.unresolved_issues || [], units: r.group.unitIds });
              LabApp.log(pipelineId, `refine 수정 적용됨(유닛 ${label}): ${(r.fixResult.changes || []).length}건 (미해결 ${(r.fixResult.unresolved_issues || []).length}건)`);
            } else {
              fixLog.push({ iteration: i, applied: false, reason: r.validation.reason, changes: [], units: r.group.unitIds });
              LabApp.log(pipelineId, `⚠ refine 수정 거부됨(유닛 ${label}, 근거 검증 실패): ${r.validation.reason}`);
            }
          }
          } finally {
            snapshotRefineState(i);
          }
        }
        if (!refineReady) {
          LabApp.log(pipelineId, `⚠ refine 최대 반복(${maxRefineIters}) 도달 -- checklist 통과 못 함, 마지막 상태로 질문 생성 진행`);
        }
      }

      // D173: graph_generated is a plain code-level fact (did buildGraph() run
      // successfully), not something an LLM judges -- kept separate from p01-3's own
      // audit checklist (which is a subjective read of unit_map content) rather than
      // folded into it, since refine's call doesn't even receive graph data to judge.
      let graph = null;
      let graphGenerated = false;
      try {
        graph = buildGraph(unitMap, audits);
        graphGenerated = true;
      } catch (err) {
        LabApp.log(pipelineId, `그래프 생성 실패: ${err.message}`);
      }

      // D173: real unit_map data has genuinely distinct units (confirmed via direct DB
      // query on real runs -- e.g. 6 to 13+ unit_ids per document, some with sub-splits
      // like "03-1"/"04-2"), but the old single graph_nodes_json call handed the model
      // the WHOLE multi-unit graph at once and let it decide how to spread questions --
      // in practice it clustered on whichever unit it looked at first (usually "01").
      // Looping per unit and scoping each call's graph_nodes_json to just that unit's own
      // nodes forces real coverage: total question count now scales with how many units
      // actually exist instead of depending on the model's own (apparently uneven)
      // judgment. Same wave/round-retry shape as D168/D169's chunk loop (maxAttempts:1,
      // only retryable failures carry into the next round, CHUNK_CONCURRENCY/
      // MAX_RETRY_ROUNDS/ROUND_RETRY_DELAY_MS reused rather than new constants) --
      // written out separately rather than extracted into a shared helper, since the
      // chunk loop's result-shape handling (chunk_range/error fields feeding
      // makeUnitMap) is different enough that sharing risked destabilizing already-
      // verified D168/D169 behavior for a same-day change.
      let questions = { questions: [] };
      if (opts.skipQuestionGen) {
        LabApp.log(pipelineId, "질문 생성 단계 건너뜀 (skipQuestionGen)");
      } else {
        const unitEntries = Object.entries(unitMap);
        LabApp.log(pipelineId, `유닛 ${unitEntries.length}개별 질문 생성 시작: ${unitEntries.map(([id]) => id).join(", ")}`);
        const unitQState = unitEntries.map(([unitId, unit]) => ({ unitId, unit, result: null, err: null, retryable: false }));
        for (let round = 1; round <= MAX_RETRY_ROUNDS; round++) {
          const targets = unitQState.filter((s) => !s.result && (round === 1 || s.retryable));
          if (!targets.length) break;
          if (round > 1) LabApp.log(pipelineId, `질문 생성 재시도 라운드 ${round}/${MAX_RETRY_ROUNDS} (${targets.length}개 유닛)`);
          for (let i = 0; i < targets.length; i += CHUNK_CONCURRENCY) {
            const wave = targets.slice(i, i + CHUNK_CONCURRENCY);
            await Promise.all(wave.map(async (state) => {
              const unitNodes = buildGraphNodes({ [state.unitId]: state.unit });
              try {
                const result = await callPromptStage("p01-4", {
                  course_label: courseLabel, graph_nodes_json: JSON.stringify(unitNodes).slice(0, 24000),
                }, { maxAttempts: 1 });
                state.result = result;
                LabApp.log(pipelineId, `유닛 ${state.unitId} 질문 ${(result.questions || []).length}개 생성 완료`);
              } catch (err) {
                state.err = err;
                state.retryable = !!err.retryable;
                LabApp.log(pipelineId, `유닛 ${state.unitId} 질문 생성 실패${state.retryable ? " (다음 라운드에 재시도)" : ""}: ${err.message}`);
              }
            }));
          }
          const stillRetryable = unitQState.filter((s) => !s.result && s.retryable);
          if (!stillRetryable.length) break;
          if (round < MAX_RETRY_ROUNDS) {
            LabApp.log(pipelineId, `${stillRetryable.length}개 유닛 질문 생성 재시도 대기 중 (${ROUND_RETRY_DELAY_MS / 1000}초)...`);
            await new Promise((resolve) => setTimeout(resolve, ROUND_RETRY_DELAY_MS));
          }
        }
        const failedUnits = unitQState.filter((s) => !s.result);
        if (failedUnits.length) {
          LabApp.log(pipelineId, `⚠ 유닛 ${failedUnits.length}개 질문 생성 실패 (${failedUnits.map((s) => s.unitId).join(", ")})`);
        }
        questions = { questions: unitQState.flatMap((s) => (s.result && s.result.questions) || []) };
      }

      const finishedAt = new Date();
      LabApp.stopTimer(pipelineId);
      LabApp.setStatus(pipelineId, "완료", "done");
      const result = {
        unit_map: unitMap, refine_audits: audits, refine_fixes: fixLog, questions, chunk_count: chunks.length, extractor: "pdfjs",
        failed_chunks: failedChunks.map((c) => ({ chunk_range: c.chunk_range, error: c.error })),
        graph, graph_generated: graphGenerated,
        // Not persisted by maybeSaveRun() below (deliberately excluded from its artifacts
        // list -- experiment-only data, empty [] for every normal caller since opts.
        // captureRefineSnapshots defaults off).
        refine_snapshots: refineSnapshots,
      };
      renderResults(result);
      await maybeSaveRun(result, startedAt, finishedAt);
    } catch (err) {
      LabApp.stopTimer(pipelineId);
      console.error(err);
      LabApp.setStatus(pipelineId, `오류: ${err.message}`, "error");
      LabApp.log(pipelineId, `오류: ${err.message}`);
      await LabApp.saveFailedRun("p01", selectedModel, err, startedAt);
    }
  }

  function renderResults(result) {
    const graphSummary = result.graph_generated
      ? `그래프 <b>${result.graph.nodes.length}</b>노드/<b>${result.graph.links.length}</b>관계`
      : `그래프 <b style="color:var(--status-blocked);">생성 실패</b>`;
    let html = `<p>유닛 <b>${Object.keys(result.unit_map).length}</b>개 · 질문 <b>${(result.questions.questions || []).length}</b>개 · 청크 <b>${result.chunk_count}</b>개
      · ${graphSummary} · <code>extractor: pdfjs</code></p>`;
    // D168: failed chunks used to only ever show up in the scrolling progress log --
    // easy to miss once the run finishes and the log isn't the thing on screen anymore.
    // Surfaced here too so an incomplete unit_map is never silently mistaken for a
    // complete one.
    if (result.failed_chunks && result.failed_chunks.length) {
      html += `<p style="color:var(--status-blocked);">⚠ 청크 ${result.failed_chunks.length}개 분석 실패 -- 이 페이지 범위는 unit_map에서 빠짐:
        ${result.failed_chunks.map((c) => `<code>p${LabApp.escapeHtml(c.chunk_range)}</code>`).join(", ")}</p>`;
    }
    // D174 Phase 4: applied/rejected auto-fix attempts, same "never leave it only in the
    // scrolling log" principle as D168's failed-chunks display above.
    if (result.refine_fixes && result.refine_fixes.length) {
      html += `<div class="field-label" style="margin-top:10px;">refine 자동수정 시도 (${result.refine_fixes.length}건)</div>`;
      for (const fix of result.refine_fixes) {
        const color = fix.applied ? "var(--status-ok)" : "var(--status-blocked)";
        const label = fix.applied ? `적용됨 · ${fix.changes.length}건 변경` : `거부됨 · ${LabApp.escapeHtml(fix.reason || "")}`;
        html += `<p style="color:${color}; font-size:0.8rem; margin:4px 0;">반복 ${fix.iteration}: ${label}</p>`;
        if (fix.applied && fix.changes.length) {
          html += `<ul style="margin:0 0 6px; padding-left:20px; font-size:0.78rem; color:var(--ink-dim);">
            ${fix.changes.map((c) => `<li>${LabApp.escapeHtml(c)}</li>`).join("")}</ul>`;
        }
      }
    }
    for (const q of (result.questions.questions || []).slice(0, 10)) {
      html += `<div class="finding-card">
        <div class="fid">${LabApp.escapeHtml(q.unit || "")} · pages: ${LabApp.escapeHtml(JSON.stringify(q.source_pages || []))}</div>
        <div>${LabApp.escapeHtml(q.question || "")}</div>
      </div>`;
    }
    html += LabApp.jsonResultBlock("원본 JSON", result, "p01-result.json");
    LabApp.showResults(html);
  }

  async function maybeSaveRun(result, startedAt, finishedAt) {
    if (!LabDB.isConfigured()) {
      LabApp.log("p01", "Supabase 미설정 — 결과는 화면에만 표시됨");
      return;
    }
    try {
      await LabDB.saveRun({
        pipeline: "p01",
        model: selectedModel,
        input_meta: {
          extractor: "pdfjs", chunk_count: result.chunk_count, failed_chunk_count: result.failed_chunks.length,
          unit_count: Object.keys(result.unit_map).length, graph_generated: result.graph_generated,
          refine_fixes_applied: result.refine_fixes.filter((f) => f.applied).length,
          refine_fixes_rejected: result.refine_fixes.filter((f) => !f.applied).length,
          source_filename: pdfFileName, // D175: previously never captured anywhere -- runs saved before this ship have no way to know which document they analyzed
        },
        overrides: {},
        rubric_overridden: false,
        artifacts: [
          { kind: "unit_map", content: result.unit_map }, { kind: "questions", content: result.questions },
          { kind: "graph", content: result.graph_generated ? result.graph : { error: "graph_generated=false" } },
          { kind: "refine_fixes", content: result.refine_fixes },
        ],
        started_at: startedAt.toISOString(), finished_at: finishedAt.toISOString(),
      });
      LabApp.log("p01", `결과가 팀 DB에 저장됨 (소요시간 ${LabApp.formatElapsed(finishedAt - startedAt)})`);
    } catch (err) {
      LabApp.log("p01", `DB 저장 실패(결과는 화면에 남아있음): ${err.message}`);
    }
  }

  LabApp.registerRunner("p01", { renderInput, run });
  return { renderInput, run };
})();
