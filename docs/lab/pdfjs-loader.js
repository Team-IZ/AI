// pdf.js ships as an ES module at this cdnjs version, so it can't be loaded with a plain
// <script integrity=...> tag the way Pyodide/JSZip are (dynamic import() has no native
// integrity= support in any browser as of writing). We get the same guarantee by hand:
// fetch() *does* support `integrity`, so we fetch+verify the bytes ourselves, then
// import() from a local Blob URL built from those verified bytes. Same applies to the
// worker script, which pdf.js loads internally via `new Worker(workerSrc)` -- that
// constructor has no integrity option either, so we pre-fetch+verify it too and hand
// pdf.js a blob: URL instead of the raw CDN URL.
const PDFJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.min.mjs";
const PDFJS_INTEGRITY = "sha384-uwJM5C0hGZH5Ed/vHx4Ht1PUd8ABIDVIcLbHydZOdFL5eTUG8Jdr32xNVIyn0kYH";
const PDFJS_WORKER_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.mjs";
const PDFJS_WORKER_INTEGRITY = "sha384-NVnhtZrGsIwDcnD5XVrDINOVzrQT0KzaOPpVmavjhwAaVWUtLcgQW7Cua6BKLsEA";

async function fetchVerifiedBlobUrl(url, integrity) {
  const res = await fetch(url, { integrity, mode: "cors" });
  if (!res.ok) throw new Error(`fetch failed: ${url} (HTTP ${res.status})`);
  const text = await res.text();
  return URL.createObjectURL(new Blob([text], { type: "application/javascript" }));
}

(async () => {
  try {
    const moduleBlobUrl = await fetchVerifiedBlobUrl(PDFJS_URL, PDFJS_INTEGRITY);
    const pdfjsLib = await import(/* @vite-ignore */ moduleBlobUrl);
    URL.revokeObjectURL(moduleBlobUrl);

    const workerBlobUrl = await fetchVerifiedBlobUrl(PDFJS_WORKER_URL, PDFJS_WORKER_INTEGRITY);
    pdfjsLib.GlobalWorkerOptions.workerSrc = workerBlobUrl;

    window.pdfjsLib = pdfjsLib;
    window.dispatchEvent(new Event("pdfjs-ready"));
  } catch (err) {
    console.error("pdf.js SRI-verified load failed:", err);
    window.pdfjsLibLoadError = err;
    window.dispatchEvent(new Event("pdfjs-ready"));
  }
})();
