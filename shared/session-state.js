// New module (no equivalent in the single-page Pipeline Lab) -- the sessionStorage
// handoff between this repo's 3 separate pages (submission.html -> session.html ->
// result.html), replacing what used to be a same-page P02Runner->P03Runner direct call
// with module-level state (loadFindingFromP02/pendingCodeContexts in the original
// p03-runner.js).
//
// Deliberately never stores the full scanned `files` map IN SESSIONSTORAGE -- the original
// app avoided this too, by keeping `files` only as an in-memory closure variable captured
// by a click handler, never stringified. Only the already-trimmed pieces (finding + the
// resolved codeContexts, capped at P02Engine.MAX_CONNECT_FILES; the completed interview
// result) ever cross a page boundary via sessionStorage here. sessionStorage's per-origin
// quota (~5-10MB) is tighter than the original app's own 500,000-char inline-JSON safety
// cap (D162), and this project has already been bitten twice by silent truncation (D153,
// D162) -- so a quota failure here is surfaced, not swallowed.
//
// D210 (2026-07-22): the one exception -- saveZipFileMap()/loadZipFileMap() below DO
// persist the full ZIP-parsed `files` map, but through IndexedDB, not sessionStorage.
//   WHY: a ZIP upload has no GitHub identity, so P03's D200 live fact-check tool-loop
//   (list_files/read_file) was a structural no-op for every ZIP-sourced session -- every
//   L2+ follow-up only ever saw the static snippet captured at submission time, never
//   re-verified. Trainees noticed and asked for parity with GitHub-sourced sessions. The
//   ONLY way to give P03 something to browse/re-read after the submission.html->
//   session.html page navigation is to persist the parsed files somewhere durable across
//   that navigation -- sessionStorage is exactly what this file's own header just argued
//   against for this size of payload, so IndexedDB (order-of-magnitude higher quota, no
//   established quota-failure history in this codebase) is used instead.
//   COST: a second, async storage mechanism in a module that was otherwise fully
//   synchronous -- callers must await save/load. IndexedDB being unavailable (locked-down
//   private-browsing contexts) degrades to "no ZIP fact-check this session", same
//   observable behavior as before D210, not a new failure mode.
//   EXIT: if IndexedDB proves unreliable in practice, fall back to a capped subset
//   (e.g. P02Engine.LIST_FILES_MAX_ENTRIES-shaped) written into sessionStorage instead --
//   bounded fidelity over no fact-check at all, rather than reverting to zero.
const SessionState = (() => {
  const SUBMISSION_KEY = "teamiz_p02_submission";
  const RESULT_KEY = "teamiz_p03_result";
  const FINDINGS_KEY = "teamiz_p02_findings";
  // D-ground2 (2026-08-03): p02-6("코드 분석 문서")이 만들고 CodeLocate.locateSymbol로
  // 실제 제출 파일과 대조해 **검증에 성공한 것만** 남긴 decision point 배열. FINDINGS_KEY와
  // 같은 이유로 자체 키를 쓴다 -- 스캔 1회당 한 번 계산되고 모든 finding이 공유하며,
  // 뒤로가기/새 탭에서 finding 목록과 독립적으로 살아 있어야 한다.
  // 크기: 항목당 {title, file, symbol, why_it_matters, located} 정도의 짧은 문자열이라
  // sessionStorage로 충분하다(D210의 zipFiles처럼 IndexedDB가 필요한 크기가 아니다) --
  // 코드 조각 본문은 저장하지 않는다. P03이 located로 그때그때 잘라 쓴다.
  const DECISION_POINTS_KEY = "teamiz_p02_decision_points";
  // D200: {owner, repo, branch} from a GitHub-URL P02 scan, or null for ZIP uploads / no
  // scan yet. Own key (not folded into SUBMISSION_KEY/FINDINGS_KEY) because it's set once
  // per SCAN, not per finding -- every finding from the same scan shares the same repo.
  const REPO_KEY = "teamiz_p02_repo_context";

  // D-fix14 (연주 audit E2, 2026-07-23): a quota failure surfaced the raw browser
  // exception's own message -- technically not swallowed (already true before this fix),
  // but not obviously actionable to a trainee reading it either.
  //   WHY: recognize QuotaExceededError specifically (name across browsers, or legacy
  //   code 22) and attach a clearer Korean hint alongside the original error object -- the
  //   original `error` is still returned unchanged so nothing that already reads `.message`
  //   directly breaks, this only adds a `hint` a caller can prefer to show instead.
  //   COST: none -- additive field only.
  //   EXIT: if a caller wants this hint to fully replace the raw message, read `.hint` first
  //   and fall back to `.error.message`.
  function safeSet(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
      return { ok: true };
    } catch (err) {
      // Most likely QuotaExceededError (or sessionStorage unavailable in a locked-down
      // context) -- report it explicitly rather than pretending the save succeeded.
      const isQuota = err && (err.name === "QuotaExceededError" || err.code === 22);
      return {
        ok: false,
        error: err,
        hint: isQuota ? "저장 용량 초과 -- finding이 너무 많거나 근거 코드가 큽니다 (브라우저 세션 저장공간 한도)" : undefined,
      };
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
  // full files map. repoRef (D200): optional {owner, repo, branch}, null for ZIP uploads.
  // resume (D212): optional {runId, transcript}, null for a fresh interview -- see
  // p03-engine.js's D212 comment. Small enough (max 4 turns of text) for plain
  // sessionStorage, unlike D210's zipFiles which needed IndexedDB.
  // D-fix13 (연주 audit E1, 2026-07-23): sessionStorage is scoped per-tab -- opening the
  // submission->session link in a new tab, or bookmarking straight to session.html, always
  // hit the "제출 단계로 돌아가세요" fallback below even though the trainee had just picked
  // a finding a moment ago in a DIFFERENT tab. This file's own header already anticipated
  // this exact EXIT path for saveFindingsList ("swap sessionStorage for localStorage").
  //   WHY: mirror SUBMISSION_KEY into localStorage alongside sessionStorage on save, and
  //   have loadSubmission() fall back to the localStorage copy only when sessionStorage is
  //   empty -- sessionStorage stays the primary/preferred source (still correctly isolates
  //   two genuinely different in-flight submissions in two tabs in the common case), the
  //   mirror only kicks in for the specific "this tab has nothing" case E1 describes.
  //   COST: if two DIFFERENT submissions are genuinely in flight in two tabs at once (rare
  //   for a single-user local tool), a tab that never had its own sessionStorage entry could
  //   recover the OTHER tab's submission via the mirror instead of correctly showing "제출
  //   단계로 돌아가세요" -- judged acceptable given how rarely that scenario occurs versus
  //   how often the new-tab/bookmark case does.
  //   EXIT: if the cross-tab mix-up above turns out to matter in practice, drop the
  //   localStorage fallback and revisit a scoped alternative (e.g. a URL param carrying a
  //   session id) instead of reverting all the way to "no recovery."
  const LOCAL_MIRROR_KEY = "teamiz_p02_submission_mirror";

  function saveSubmissionPayload(payload) {
    const result = safeSet(SUBMISSION_KEY, payload);
    try {
      localStorage.setItem(LOCAL_MIRROR_KEY, JSON.stringify(payload));
    } catch (err) {
      // best-effort mirror only -- sessionStorage above is still the primary save path and
      // already reported ok/fail via `result`.
    }
    return result;
  }

  function saveSubmission({ finding, codeContexts, repoRef, resume }) {
    return saveSubmissionPayload({ finding, codeContexts: codeContexts || [], model: null, repoRef: repoRef || null, resume: resume || null });
  }

  // Optional 3rd field so submission.html can also hand session.html the model chosen in
  // its own connection-settings panel, if the page wires it that way; session.html falls
  // back to the manifest default itself if this is absent (same fallback the original
  // renderInput() did before selecting a model).
  function saveSubmissionWithModel({ finding, codeContexts, model, repoRef, resume }) {
    return saveSubmissionPayload({ finding, codeContexts: codeContexts || [], model: model || null, repoRef: repoRef || null, resume: resume || null });
  }

  // Returns { finding, codeContexts, model, repoRef, resume } or null if nothing valid was
  // saved (direct navigation to session.html, expired/cleared sessionStorage, or a
  // malformed value) -- the page must fall back to a "제출 단계로 돌아가세요" state in that
  // case, not crash. D200: repoRef defaults to null for rows saved before this field
  // existed (no migration needed -- `v.repoRef` is simply `undefined` on those, falls
  // through below). D212: same null-safe fallback for `resume`.
  function normalizeSubmission(v) {
    if (!v || typeof v !== "object" || !v.finding) return null;
    return {
      finding: v.finding,
      codeContexts: Array.isArray(v.codeContexts) ? v.codeContexts : [],
      model: v.model || null,
      repoRef: (v.repoRef && v.repoRef.owner && v.repoRef.repo) ? v.repoRef : null,
      resume: (v.resume && v.resume.runId && Array.isArray(v.resume.transcript)) ? v.resume : null,
    };
  }

  function loadSubmission() {
    const fromSession = normalizeSubmission(safeGet(SUBMISSION_KEY));
    if (fromSession) return fromSession;
    // D-fix13: fall back to the cross-tab localStorage mirror only when THIS tab's own
    // sessionStorage has nothing -- see LOCAL_MIRROR_KEY's own comment above for the tradeoff.
    try {
      const raw = localStorage.getItem(LOCAL_MIRROR_KEY);
      return raw ? normalizeSubmission(JSON.parse(raw)) : null;
    } catch (err) {
      return null;
    }
  }

  // D200: set once per scan (submission.html, right after P02Engine.run() resolves) --
  // its own key so it survives 뒤로가기 (D1) independent of FINDINGS_KEY/SUBMISSION_KEY.
  function saveRepoContext(repoRef) {
    return safeSet(REPO_KEY, repoRef || null);
  }

  function loadRepoContext() {
    const v = safeGet(REPO_KEY);
    if (!v || typeof v !== "object" || !v.owner || !v.repo) return null;
    return { owner: v.owner, repo: v.repo, branch: v.branch || null };
  }

  // D1 (뒤로가기): trainee feedback -- leaving session.html mid-interview navigated back
  // to submission.html's blank upload form, discarding the whole P02 finding list and
  // forcing a full re-scan just to check a *different* finding from the same run.
  //   WHY: submission.html's scan results (`result`/`files`) only ever lived in a
  //   click-handler closure, gone the instant the page unloads -- SUBMISSION_KEY above
  //   only ever held the ONE finding the user already picked, not the list they picked
  //   it from. The fix stores a small array of the SAME shape saveSubmission() already
  //   uses per-finding (finding + already-trimmed codeContexts), computed once right
  //   after the scan for every finding, not just the chosen one -- so returning to
  //   submission.html can redraw the full list without ever touching `files` again.
  //   COST: still deliberately excludes the full scanned `files` map (same reasoning as
  //   SUBMISSION_KEY above), so this is bytes-proportional to finding count x
  //   MAX_CONNECT_FILES, not to repo size -- acceptable, but a scan with many findings
  //   costs more sessionStorage than saving just one ever did. Overwritten wholesale by
  //   the next scan (no merge), so only the most recent run's findings are ever
  //   "resumable" -- matches how SUBMISSION_KEY already behaves.
  //   EXIT: if this ever needs to survive a browser restart (not just this tab's
  //   session), swap sessionStorage for localStorage here -- the safeSet/safeGet shape
  //   doesn't change, only which Storage object backs it.
  function saveFindingsList(list) {
    return safeSet(FINDINGS_KEY, list);
  }

  function loadFindingsList() {
    const v = safeGet(FINDINGS_KEY);
    return Array.isArray(v) ? v : null;
  }

  // D-ground2: saveFindingsList/loadFindingsList와 같은 모양 그대로 -- 실패는 삼키지 않고
  // safeSet의 {ok, error, hint}를 그대로 돌려주되, 호출부(submission.html)는 이 저장이
  // 실패해도 스캔 결과 표시를 막지 않는다. decision point가 없는 상태는 유효한 폴백이고
  // (P03이 기존 앞부분 슬라이스로 되돌아간다), 없는 근거를 억지로 보여주는 것보다 낫다.
  function saveDecisionPoints(points) {
    return safeSet(DECISION_POINTS_KEY, points);
  }

  // 배열이 아니면(저장 안 됨/이전 버전 스캔/손상) null -- p03-engine.js가 decisionPoints
  // 부재를 D-poc13 이전과 완전히 동일한 동작으로 취급하는 그 신호다.
  function loadDecisionPoints() {
    const v = safeGet(DECISION_POINTS_KEY);
    return Array.isArray(v) ? v : null;
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

  // D210: IndexedDB-backed persistence for a ZIP scan's full parsed `files` map -- see
  // this file's D210 header comment for the full WHY/COST/EXIT. One well-known record
  // ("current"), overwritten wholesale by each new ZIP scan (same "most recent run only"
  // shape FINDINGS_KEY/SUBMISSION_KEY already use above) -- there is never more than one
  // in-progress submission per tab, so no need to key by scan/session id.
  const ZIP_DB_NAME = "teamiz_p02_zip_cache";
  const ZIP_DB_STORE = "files";
  const ZIP_DB_KEY = "current";

  function openZipDb() {
    return new Promise((resolve, reject) => {
      if (typeof indexedDB === "undefined") { reject(new Error("IndexedDB 사용 불가")); return; }
      const req = indexedDB.open(ZIP_DB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(ZIP_DB_STORE)) req.result.createObjectStore(ZIP_DB_STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error("IndexedDB 열기 실패"));
    });
  }

  // Returns { ok: true } or { ok: false, error } -- same shape as safeSet() above, so
  // callers handle both the same way (surface, don't swallow -- this file's own header
  // principle for cross-page persistence failures).
  async function saveZipFileMap(files) {
    try {
      const db = await openZipDb();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(ZIP_DB_STORE, "readwrite");
        tx.objectStore(ZIP_DB_STORE).put(files, ZIP_DB_KEY);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error || new Error("IndexedDB 쓰기 실패"));
      });
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err };
    }
  }

  // D210: called for a GitHub-URL scan so a STALE map from an earlier ZIP scan in the
  // same tab can't leak into a later GitHub-sourced session (repoRef and zipFiles are
  // mutually exclusive by submission method -- a lingering old record here would be
  // wrong, not just redundant, if p03-engine.js is ever asked to pick between them).
  // Best-effort/fire-and-forget from the caller's perspective -- a failed clear just
  // leaves stale data that generateQuestion()'s repoRef-first precedence still overrides.
  async function clearZipFileMap() {
    try {
      const db = await openZipDb();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(ZIP_DB_STORE, "readwrite");
        tx.objectStore(ZIP_DB_STORE).delete(ZIP_DB_KEY);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error || new Error("IndexedDB 삭제 실패"));
      });
    } catch (err) {
      // best-effort -- see comment above
    }
  }

  // Returns the files map ({path: content}) or null (nothing saved, IndexedDB unavailable,
  // or this session never came from a ZIP scan) -- null is the same "fact-check source
  // unavailable" signal p03-engine.js already treats a null/absent repoRef as.
  async function loadZipFileMap() {
    try {
      const db = await openZipDb();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(ZIP_DB_STORE, "readonly");
        const req = tx.objectStore(ZIP_DB_STORE).get(ZIP_DB_KEY);
        req.onsuccess = () => resolve(req.result && typeof req.result === "object" ? req.result : null);
        req.onerror = () => reject(req.error || new Error("IndexedDB 읽기 실패"));
      });
    } catch (err) {
      return null;
    }
  }

  return {
    saveSubmission, saveSubmissionWithModel, loadSubmission, saveFindingsList, loadFindingsList,
    saveDecisionPoints, loadDecisionPoints,
    saveInterviewResult, loadInterviewResult, saveRepoContext, loadRepoContext,
    saveZipFileMap, loadZipFileMap, clearZipFileMap,
  };
})();
