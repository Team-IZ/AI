// Shared Pyodide instance across P02/P03 runners so the ~10MB WASM runtime is only
// ever fetched once per page load, regardless of which pipelines a session touches.
const LabPyodide = (() => {
  let instance = null;
  let loadingPromise = null;
  const loadedFileSets = new Set(); // e.g. "p02", "p03" -- avoid re-fetching the same source twice

  async function get(onProgress) {
    if (instance) return instance;
    if (!loadingPromise) {
      loadingPromise = (async () => {
        if (onProgress) onProgress("Pyodide(브라우저 내 파이썬) 초기화 중... (최초 1회, ~10MB)");
        instance = await loadPyodide();
        return instance;
      })();
    }
    return loadingPromise;
  }

  async function loadFiles(pyodide, rawBase, fileList, destPrefix, onProgress) {
    for (const relPath of fileList) {
      const res = await fetch(rawBase + relPath);
      if (!res.ok) throw new Error(`소스 로드 실패: ${relPath} (HTTP ${res.status})`);
      const text = await res.text();
      const fullPath = destPrefix + "/" + relPath;
      const dir = fullPath.substring(0, fullPath.lastIndexOf("/"));
      pyodide.FS.mkdirTree(dir);
      pyodide.FS.writeFile(fullPath, text, { encoding: "utf8" });
      if (onProgress) onProgress(`${relPath} 로드됨`);
    }
  }

  function markLoaded(key) { loadedFileSets.add(key); }
  function isLoaded(key) { return loadedFileSets.has(key); }

  return { get, loadFiles, markLoaded, isLoaded };
})();
