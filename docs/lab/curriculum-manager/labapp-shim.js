// This page reuses p01-runner.js completely unmodified (see index.html script order:
// config.js -> db.js -> lab-core.js -> THIS FILE -> llm.js -> pyodide-shared.js ->
// pdfjs-loader.js -> p01-runner.js). lab-core.js already provides 13 of
// the 17 `LabApp.*` members p01-runner.js calls (it was extracted from the original
// docs/lab/app.js specifically to support DOM-independent reuse -- see its own header
// comment).
// It deliberately does NOT port the 7 members that were DOM-bound in the original
// app.js, because the newer engines don't need them (they take explicit hooks
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

  // D1 (2026-07-21): "취소" used to only ever navigate away (data-nav="list") while
  // P01Runner.run() kept executing untouched in the background -- clicking it did NOT
  // stop the pipeline. run() (p01-runner.js:514-820) has no AbortSignal/cancellation
  // parameter anywhere and is a shared file this project deliberately never modifies, so
  // real cancellation can't be added there. Instead: `LabApp.log`/`LabApp.setStatus` are
  // the two members run() calls constantly (after every chunk-analysis wave, every
  // refine iteration, every question-gen wave, and unconditionally right before both its
  // success and failure exits -- confirmed by reading run() end-to-end) and are already
  // owned by this shim. Making them throw a tagged error once cancellation is requested
  // turns "the next progress update" into a cancellation checkpoint, with NO edit to
  // p01-runner.js/llm.js needed.
  //   COST: not instant -- a checkpoint only fires at the next log/setStatus call, so a
  //     single long in-flight network wave (worst case: the refine parallel-fix stage,
  //     which calls neither between firing its Promise.all and reading the results) can
  //     delay the abort until that wave naturally finishes. index.html's click handler
  //     adds a second, always-correct layer on top of this: it discards ANY result that
  //     arrives after cancellation was requested, whether run() ended up throwing here or
  //     (in that one gap) resolving normally -- so a wrong/unwanted analysis can never
  //     get saved or shown even in the slow-checkpoint case.
  //   EXIT: if real usage shows the parallel-fix-stage gap matters in practice, the next
  //     step is a cancel-check inside fixGroup() itself (still shim/index.html-local,
  //     p01-runner.js already isolates that closure per D174 Phase 3).
  let cancelRequested = false;
  function checkCancelled() {
    if (!cancelRequested) return;
    const e = new Error("사용자가 분석을 취소함");
    e.isCancelled = true;
    throw e;
  }

  // Safe no-op when this page has no #log-<pipelineId> element (matches the original
  // app.js log()'s own `if (!el) return` guard) -- but if the page DOES provide one
  // (this page does, for p01), progress lines actually render there.
  function log(pipelineId, msg) {
    checkCancelled();
    const el = document.getElementById(`log-${pipelineId}`);
    if (!el) return;
    el.classList.remove("hidden");
    const line = document.createElement("div");
    line.textContent = `[${new Date().toISOString().slice(11, 19)}] ${msg}`;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  // Also stashes the last status text/kind in module state -- p01-runner.js's run()
  // never rethrows on failure (unlike the newer engines' run(), which does), so this is the
  // only way for the page to learn *why* takeP01Result() came back null after a failed
  // run. setStatus("...", "error") is always called in run()'s catch block right before
  // saveFailedRun(), so lastStatus is guaranteed set by the time run()'s promise settles.
  // Also cancel-checked (see D1 above) -- run()'s success path always calls this with
  // "완료" right before renderResults()/maybeSaveRun(), so this alone is enough to stop a
  // cancelled run from ever reaching its own save call, even when nothing else caught it
  // sooner. Throwing here also means the catch block's own setStatus("오류: ...") call
  // throws too when still cancelled, which correctly skips saveFailedRun() -- a
  // cancelled run shouldn't leave a "failed" row in the DB, and it now doesn't.
  function setStatus(pipelineId, text, kind) {
    checkCancelled();
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

  // Originally copied verbatim from docs/lab/app.js:69-91 -- already a local-only copy
  // (see file header), so free to diverge from the original without a sync concern.
  //
  // D1 (2026-07-24): 비고를 CURATED_MODELS의 정적 텍스트에서 팀 공유 DB 메모
  // (public.model_notes, experiments/web_lab/model_notes_schema.sql)로 확장. 정적
  // 텍스트는 여전히 "아무도 DB에 메모를 안 남긴 모델"의 fallback으로 남음.
  // LabApp.modelNotesCache는 index.html이 LabDB.fetchModelNotes()로 채움(로그인
  // 안 됐으면 빈 객체 -- 그 경우 이 함수는 정적 fallback만 보여주고 편집 UI도
  // 저장 시 LabDB.currentMember()가 알아서 "로그인이 필요합니다"로 막음).
  function renderModelToggle(container, groupSelector, noteSelector, getSelected, setSelected) {
    const group = container.querySelector(groupSelector);
    const note = container.querySelector(noteSelector);
    group.innerHTML = LabApp.MODEL_CHOICES.map((m) => {
      const cls = ["model-chip"];
      if (m.id === getSelected()) cls.push("active");
      if (m.tier === "bad") cls.push("warn");
      return `<button type="button" class="${cls.join(" ")}" data-model="${LabApp.escapeHtml(m.id)}">${LabApp.escapeHtml(m.label)}</button>`;
    }).join("");

    let editing = false;
    // D1-security: 팀원이 직접 입력하는 필드(비고 내용, 이메일)는 XSS 벡터라 innerHTML
    // 문자열조합+escapeHtml 대신 DOM 메서드(createElement+textContent)만 사용 --
    // textContent는 파서를 아예 안 거치므로 이스케이프 실수 자체가 구조적으로
    // 불가능(보안 훅이 innerHTML 패턴에 지적한 걸 계기로 이 방식으로 확정).
    function renderNoteArea() {
      const selectedId = getSelected();
      const m = LabApp.MODEL_CHOICES.find((x) => x.id === selectedId);
      const dbEntry = (LabApp.modelNotesCache || {})[selectedId];
      const displayNote = dbEntry ? dbEntry.note : (m ? m.note : "");
      note.className = "model-note" + (m && m.tier === "bad" ? " warn" : "");
      note.textContent = ""; // clear (no interpolation -- plain clear is always safe)

      // D-quality-badge (2026-07-24): 사용자 요청 -- P01-EQ codex 채점 결과를 공유 메모
      // 영역에 함께 표시(정렬 근거를 눈으로도 확인 가능하게). qualityScore가 실제
      // 숫자일 때만 그림(아직 미채점인 모델이 다수라 "PENDING" 같은 placeholder를
      // 보여주면 노이즈만 늘어남 -- 없으면 그냥 안 보여줌). 편집모드 여부와 무관하게
      // 항상 표시(점수는 note 텍스트 편집과 무관한 별도 정보라 토글에 안 숨김).
      const qs = dbEntry ? Number(dbEntry.qualityScore) : NaN;
      if (dbEntry && dbEntry.qualityScore !== null && dbEntry.qualityScore !== undefined && Number.isFinite(qs)) {
        const badge = document.createElement("span");
        badge.className = `note-quality band-${dbEntry.qualityBand || "?"}`;
        badge.textContent = `P01-EQ ${dbEntry.qualityBand || ""} · ${qs.toFixed(2)}`.replace("  ", " ");
        if (dbEntry.qualityAxes && typeof dbEntry.qualityAxes === "object") {
          const parts = Object.entries(dbEntry.qualityAxes)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => `${k}:${v.toFixed(2)}`);
          if (parts.length) badge.title = parts.join(" ");
        }
        note.appendChild(badge);
      }

      if (!editing) {
        const textSpan = document.createElement("span");
        textSpan.className = "note-text";
        textSpan.textContent = displayNote || "(비고 없음)";
        note.appendChild(textSpan);

        if (dbEntry) {
          const caption = document.createElement("div");
          caption.className = "note-caption";
          caption.textContent = `최근 수정: ${dbEntry.updatedByEmail || "알 수 없음"} · ${new Date(dbEntry.updatedAt).toLocaleString("ko-KR")}`;
          note.appendChild(caption);
        }

        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "btn ghost note-edit-btn";
        editBtn.textContent = "메모 편집";
        editBtn.addEventListener("click", () => { editing = true; renderNoteArea(); });
        note.appendChild(editBtn);
        return;
      }

      const textarea = document.createElement("textarea");
      textarea.className = "note-textarea";
      textarea.rows = 3;
      textarea.value = displayNote; // DOM 프로퍼티 -- 파서를 거치지 않음
      note.appendChild(textarea);

      const actions = document.createElement("div");
      actions.className = "note-edit-actions";
      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn note-save-btn";
      saveBtn.textContent = "저장";
      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn ghost note-cancel-btn";
      cancelBtn.textContent = "취소";
      const statusEl = document.createElement("span");
      statusEl.className = "note-save-status";
      actions.appendChild(saveBtn);
      actions.appendChild(cancelBtn);
      actions.appendChild(statusEl);
      note.appendChild(actions);

      cancelBtn.addEventListener("click", () => { editing = false; renderNoteArea(); });
      saveBtn.addEventListener("click", async () => {
        const value = textarea.value;
        saveBtn.disabled = true;
        statusEl.textContent = "저장 중...";
        try {
          const result = await LabDB.saveModelNote(selectedId, value);
          LabApp.modelNotesCache = LabApp.modelNotesCache || {};
          LabApp.modelNotesCache[selectedId] = { note: value, updatedByEmail: result.updatedByEmail, updatedAt: result.updatedAt };
          editing = false;
          renderNoteArea();
        } catch (err) {
          statusEl.textContent = `저장 실패: ${err.message}`;
          saveBtn.disabled = false;
        }
      });
    }
    const updateNote = () => { editing = false; renderNoteArea(); }; // 모델 전환 시 편집상태 초기화

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
  // the newer engines never call it, they return data directly), so this
  // doesn't affect their behavior at all. Returns "" since the HTML is discarded
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
    // Call once right before starting a new P01Runner.run() -- clears any leftover flag
    // from a previous cancelled run so it can't immediately abort the new one too.
    resetCancel() {
      cancelRequested = false;
    },
    // Called by the upload tab's "취소" button while a run is in flight. Doesn't stop
    // any already-in-flight network request, but guarantees run()'s promise will
    // eventually reject via checkCancelled() above instead of quietly succeeding.
    requestCancel() {
      cancelRequested = true;
    },
    isCancelled() {
      return cancelRequested;
    },
  };
})();
