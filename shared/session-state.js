// New module (no equivalent in the single-page Pipeline Lab) -- the sessionStorage
// handoff between this repo's 3 separate pages (submission.html -> session.html ->
// result.html), replacing what used to be a same-page P02Runner->P03Runner direct call
// with module-level state (loadFindingFromP02/pendingCodeContexts in the original
// p03-runner.js).
//
// Deliberately never stores the full scanned `files` map -- the original app avoided this
// too, by keeping `files` only as an in-memory closure variable captured by a click
// handler, never stringified. Only the already-trimmed pieces (finding + the resolved
// codeContexts, capped at P02Engine.MAX_CONNECT_FILES; the completed interview result)
// ever cross a page boundary here. sessionStorage's per-origin quota (~5-10MB) is tighter
// than the original app's own 500,000-char inline-JSON safety cap (D162), and this
// project has already been bitten twice by silent truncation (D153, D162) -- so a quota
// failure here is surfaced, not swallowed.
const SessionState = (() => {
  const SUBMISSION_KEY = "teamiz_p02_submission";
  const RESULT_KEY = "teamiz_p03_result";

  function safeSet(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
      return { ok: true };
    } catch (err) {
      // Most likely QuotaExceededError (or sessionStorage unavailable in a locked-down
      // context) -- report it explicitly rather than pretending the save succeeded.
      return { ok: false, error: err };
    }
  }

  function safeGet(key) {
    try {
      const raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null; // malformed JSON -- treated the same as "nothing saved"
    }
  }

  // finding: the P02 finding object as-is. codeContexts: [{path, content}], already
  // resolved+capped by P02Engine.resolveConnectableFile/MAX_CONNECT_FILES -- never the
  // full files map.
  function saveSubmission({ finding, codeContexts }) {
    return safeSet(SUBMISSION_KEY, { finding, codeContexts: codeContexts || [], model: null });
  }

  // Optional 3rd field so submission.html can also hand session.html the model chosen in
  // its own connection-settings panel, if the page wires it that way; session.html falls
  // back to the manifest default itself if this is absent (same fallback the original
  // renderInput() did before selecting a model).
  function saveSubmissionWithModel({ finding, codeContexts, model }) {
    return safeSet(SUBMISSION_KEY, { finding, codeContexts: codeContexts || [], model: model || null });
  }

  // Returns { finding, codeContexts, model } or null if nothing valid was saved (direct
  // navigation to session.html, expired/cleared sessionStorage, or a malformed value) --
  // the page must fall back to a "제출 단계로 돌아가세요" state in that case, not crash.
  function loadSubmission() {
    const v = safeGet(SUBMISSION_KEY);
    if (!v || typeof v !== "object" || !v.finding) return null;
    return { finding: v.finding, codeContexts: Array.isArray(v.codeContexts) ? v.codeContexts : [], model: v.model || null };
  }

  // result: the object P03Engine.run() resolved with ({finding, verdict, turns,
  // transcript, grades, rubric_overridden}) -- saved as-is, already fully graded and
  // already saved to Supabase by the time session.html calls this (run() does both
  // before returning).
  function saveInterviewResult(result) {
    return safeSet(RESULT_KEY, result);
  }

  function loadInterviewResult() {
    const v = safeGet(RESULT_KEY);
    if (!v || typeof v !== "object" || !v.grades) return null;
    return v;
  }

  return { saveSubmission, saveSubmissionWithModel, loadSubmission, saveInterviewResult, loadInterviewResult };
})();
