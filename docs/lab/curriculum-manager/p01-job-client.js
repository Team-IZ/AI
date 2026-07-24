// D-P01ORCH (client side): talks to worker/p01-orchestrator/ instead of calling
// P01Runner.run() directly, for logged-in submissions -- see that Worker's own header
// comment for the full WHY. p01-runner.js is NOT modified; this file only adds its own
// independent listeners to the SAME DOM elements P01Runner.renderInput() creates
// (#p01-pdf-input/#p01-dropzone/#p01-pdf-password), the same "shim" principle
// labapp-shim.js already uses elsewhere in this project -- P01Runner's own private
// pdfBytes/pdfPassword closure is untouched and still used by the not-logged-in fallback
// path (index.html keeps calling P01Runner.run() directly when nobody's logged in, since
// RLS requires an authenticated member_id for any durable job to be written at all).
const P01JobClient = (() => {
  const ORCHESTRATOR_URL = "https://p01-orchestrator.popixoxipop.workers.dev";

  let pdfFile = null;
  let pdfPassword = "";

  function wireFileCapture(container) {
    const fileInput = container.querySelector("#p01-pdf-input");
    const dropzone = container.querySelector("#p01-dropzone");
    const passwordInput = container.querySelector("#p01-pdf-password");
    if (fileInput) fileInput.addEventListener("change", () => { if (fileInput.files[0]) pdfFile = fileInput.files[0]; });
    if (dropzone) dropzone.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) pdfFile = e.dataTransfer.files[0]; });
    if (passwordInput) passwordInput.addEventListener("input", () => { pdfPassword = passwordInput.value; });
  }

  // ---- ported from p01-runner.js:136-155 (waitForPdfJs/extractPages) -- pdf.js/DOM
  // extraction can't move server-side, this has to stay client-side regardless ----
  async function waitForPdfJs() {
    if (window.pdfjsLib) return;
    if (window.pdfjsLibLoadError) throw window.pdfjsLibLoadError;
    await new Promise((resolve) => window.addEventListener("pdfjs-ready", resolve, { once: true }));
    if (window.pdfjsLibLoadError) throw window.pdfjsLibLoadError;
  }

  async function extractPages(bytes, password, onProgress) {
    await waitForPdfJs();
    const doc = await window.pdfjsLib.getDocument({ data: bytes.slice(), password: password || undefined }).promise;
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

  // ---- ported from p01-runner.js:162-170 (buildChunks) ----
  function buildChunks(pages, chunkSize) {
    const chunks = [];
    for (let start = 1; start <= pages.length; start += chunkSize) {
      const end = Math.min(start + chunkSize - 1, pages.length);
      const text = pages.slice(start - 1, end).join("\n");
      chunks.push({ start, end, range: `${start}-${end}`, text });
    }
    return chunks;
  }

  async function submitAnalysis({ model, courseLabel, chunkSize, onProgress }) {
    if (!pdfFile) throw new Error("PDF를 먼저 업로드하세요");
    const apiKey = LabConfig.get("nvidia-key");
    const proxyUrl = LabConfig.get("proxy-url");
    if (!apiKey || !proxyUrl) throw new Error("NVIDIA 키와 프록시 URL을 먼저 입력하세요 (상단 연결 설정)");

    const c = await LabDB.ensureClient();
    const { data: sessionData } = await c.auth.getSession();
    const accessToken = sessionData && sessionData.session ? sessionData.session.access_token : null;
    if (!accessToken) throw new Error("로그인이 필요합니다 (서버에서 진행되는 분석은 팀 DB 계정에 연결돼야 합니다)");

    if (onProgress) onProgress("PDF 텍스트 추출 중 (pdf.js)...");
    const bytes = new Uint8Array(await pdfFile.arrayBuffer());
    const pages = await extractPages(bytes, pdfPassword, (msg) => onProgress && onProgress(msg));
    const chunks = buildChunks(pages, chunkSize || 10);

    const res = await fetch(`${ORCHESTRATOR_URL}/analyses`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model, courseLabel, chunks,
        nvidiaApiKey: apiKey, proxyUrl,
        supabaseAccessToken: accessToken,
        sourceFilename: pdfFile.name,
      }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`분석 제출 실패 (HTTP ${res.status}): ${text.slice(0, 300)}`);
    }
    return res.json(); // {jobId, status}
  }

  // D-fix (security review): both endpoints now require the caller's own Supabase
  // session (see worker/p01-orchestrator/index.js's own D-fix note) -- pulled fresh each
  // call rather than cached, same as submitAnalysis(), so a token refresh mid-session is
  // picked up automatically.
  async function currentAccessToken() {
    const c = await LabDB.ensureClient();
    const { data } = await c.auth.getSession();
    return data && data.session ? data.session.access_token : null;
  }

  async function pollJob(jobId) {
    const token = await currentAccessToken();
    if (!token) throw new Error("로그인이 필요합니다");
    const res = await fetch(`${ORCHESTRATOR_URL}/analyses/${encodeURIComponent(jobId)}?token=${encodeURIComponent(token)}`);
    if (!res.ok) throw new Error(`상태 조회 실패 (HTTP ${res.status})`);
    return res.json();
  }

  async function cancelJob(jobId) {
    const token = await currentAccessToken();
    if (!token) throw new Error("로그인이 필요합니다");
    const res = await fetch(`${ORCHESTRATOR_URL}/analyses/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ supabaseAccessToken: token }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`취소 요청 실패 (HTTP ${res.status}): ${text.slice(0, 200)}`);
    }
    return res.json();
  }

  return { wireFileCapture, submitAnalysis, pollJob, cancelJob };
})();
