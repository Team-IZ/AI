// This page reuses p01-runner.js completely unmodified (see index.html script order:
// config.js -> db.js -> lab-core.js -> THIS FILE -> llm.js -> pyodide-shared.js ->
// pdfjs-loader.js -> p01-runner.js -> p02-engine.js). lab-core.js already provides 13 of
// the 17 `LabApp.*` members p01-runner.js calls (it was extracted from the original
// docs/lab/app.js specifically to support DOM-independent reuse -- see its own header
// comment and docs/lab/trainee/*.html, which already reuses it for P02Engine/P03Engine).
// It deliberately does NOT port the 7 members that were DOM-bound in the original
// app.js, because P02Engine/P03Engine don't need them (they take explicit hooks
// instead). p01-runner.js was never ported to that hooks style, so it still calls all 7:
// log, setStatus, startTimer, stopTimer, showResults, registerRunner, renderModelToggle.
// Without them, `LabApp.registerRunner("p01", {renderInput, run})` at the bottom of
// p01-runner.js's IIFE (module-load time, synchronous) throws immediately and
// `P01Runner` never gets defined at all.
//
// This file adds exactly those 7, plus one intentional override (jsonResultBlock, see
// below) -- by mutating the existing `LabApp` object lab-core.js declared (classic
// <script> top-level `const` isn't a `window` property, but it IS visible to later
// classic scripts in the same document, and object properties on it are freely
// reassignable). Must stay attached to the name `LabApp` specifically: db.js's
// saveRun() calls `window.LabApp.getManifest()` internally to stamp manifest_version on
// every saved run.
//
// The critical piece: P01Runner.run() (docs/lab/p01-runner.js:452-730) has NO return
// statement -- the result object it builds only ever reaches renderResults(result)
// (a private function, not part of P01Runner's public {renderInput, run} API), which in
// turn calls `LabApp.jsonResultBlock("원본 JSON", result, "p01-result.json")` right
// before `LabApp.showResults(html)`. Overriding jsonResultBlock to capture its `obj`
// argument is the only safe way for this page to ever see a completed run's result.
(function () {
  const timers = {};
  let capturedP01Result = null;
  let lastStatus = { text: "", kind: "" };

  // Safe no-op when this page has no #log-<pipelineId> element (matches the original
  // app.js log()'s own `if (!el) return` guard) -- but if the page DOES provide one
  // (this page does, for p01), progress lines actually render there.
  function log(pipelineId, msg) {
    const el = document.getElementById(`log-${pipelineId}`);
    if (!el) return;
    el.classList.remove("hidden");
    const line = document.createElement("div");
    line.textContent = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  // Also stashes the last status text/kind in module state -- p01-runner.js's run()
  // never rethrows on failure (unlike p02-engine.js's run(), which does), so this is the
  // only way for the page to learn *why* takeP01Result() came back null after a failed
  // run. setStatus("...", "error") is always called in run()'s catch block right before
  // saveFailedRun(), so lastStatus is guaranteed set by the time run()'s promise settles.
  function setStatus(pipelineId, text, kind) {
    lastStatus = { text, kind };
    const el = document.getElementById(`status-${pipelineId}`);
    if (!el) return;
    el.textContent = text;
    el.className = "run-status" + (kind ? ` ${kind}` : "");
  }

  function startTimer(pipelineId) {
    stopTimer(pipelineId);
    const startMs = Date.now();
    const el = document.getElementById(`timer-${pipelineId}`);
    if (el) el.textContent = "00:00";
    const intervalId = setInterval(() => {
      if (el) el.textContent = LabApp.formatElapsed(Date.now() - startMs);
    }, 1000);
    timers[pipelineId] = { startMs, intervalId };
    return startMs;
  }

  function stopTimer(pipelineId) {
    const t = timers[pipelineId];
    if (!t) return 0;
    clearInterval(t.intervalId);
    delete timers[pipelineId];
    const elapsedMs = Date.now() - t.startMs;
    const el = document.getElementById(`timer-${pipelineId}`);
    if (el) el.textContent = LabApp.formatElapsed(elapsedMs);
    return elapsedMs;
  }

  // p01-runner.js only ever calls LabApp.registerRunner("p01", {renderInput, run}) once,
  // at module-load time, and this page never reads the registry back (it holds its own
  // reference to the global `P01Runner` instead) -- storing it is enough to satisfy the
  // call, nothing downstream here depends on retrieving it.
  const runners = {};
  function registerRunner(pipelineId, runner) { runners[pipelineId] = runner; }

  // renderResults(result) (private to p01-runner.js) builds an HTML string in the
  // original tool's dark-theme layout and hands it here -- this page draws its own
  // structure/mapping tables straight from the result object instead, so that HTML is
  // simply discarded.
  function showResults() {}

  // Copied verbatim from docs/lab/app.js:69-91 -- fully self-contained (container +
  // explicit selectors + getter/setter callbacks, no fixed global IDs), so it's safe to
  // reuse as-is with this page's own #p01-model-group/#p01-model-note elements.
  function renderModelToggle(container, groupSelector, noteSelector, getSelected, setSelected) {
    const group = container.querySelector(groupSelector);
    const note = container.querySelector(noteSelector);
    group.innerHTML = LabApp.MODEL_CHOICES.map((m) => {
      const cls = ["model-chip"];
      if (m.id === getSelected()) cls.push("active");
      if (m.tier === "bad") cls.push("warn");
      return `<button type="button" class="${cls.join(" ")}" data-model="${LabApp.escapeHtml(m.id)}">${LabApp.escapeHtml(m.label)}</button>`;
    }).join("");
    const updateNote = () => {
      const m = LabApp.MODEL_CHOICES.find((x) => x.id === getSelected());
      note.textContent = m ? m.note : "";
      note.className = "model-note" + (m && m.tier === "bad" ? " warn" : "");
    };
    group.querySelectorAll(".model-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        setSelected(btn.dataset.model);
        group.querySelectorAll(".model-chip").forEach((b) => b.classList.toggle("active", b === btn));
        updateNote();
      });
    });
    updateNote();
  }

  // Intentional override of lab-core.js's own jsonResultBlock -- see file header. Only
  // p01-runner.js's private renderResults() ever calls this (grep-confirmed:
  // p02-engine.js/p03-engine.js never call it, they return data directly), so this
  // doesn't affect P02Engine's behavior at all. Returns "" since the HTML is discarded
  // anyway (showResults() above is a no-op).
  function jsonResultBlock(title, obj) {
    capturedP01Result = obj;
    return "";
  }

  Object.assign(LabApp, {
    log, setStatus, startTimer, stopTimer, registerRunner, showResults, renderModelToggle, jsonResultBlock,
  });

  // This page's own read side of the capture above -- takeP01Result() consumes (and
  // clears) whatever the most recent run produced, so a second run can't accidentally
  // return a stale result if it fails before reaching jsonResultBlock again.
  window.LabAppShim = {
    takeP01Result() {
      const r = capturedP01Result;
      capturedP01Result = null;
      return r;
    },
    lastStatus() {
      return lastStatus;
    },
  };
})();
