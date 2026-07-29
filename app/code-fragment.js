// finding/분석문서가 가리키는 {file, lines} 참조를 실제 소스와 대조해 코드 파편을 뽑는다.
//
// D-poc6: 기존 P02 finding에는 라인 번호가 없다(cognition/judgment는 파일 단위로만
// 판단한다) -- 명세의 "코드 파편(질문에 사용한 코드 파일, line 넘버)"을 만들려면 LLM이
// 분석 문서(p04-1)에서 스스로 {file, lines}를 지목하게 한 뒤, 그 지목이 실제 파일 범위
// 안에 있는지 여기서 검증한다. LLM이 지어낸 파일/라인을 그대로 믿지 않는다 -- 검증에
// 실패하면 그 항목은 버려지고(analysis.js가 사용자에게 알림), 화면에는 결코 존재하지
// 않는 코드를 보여주지 않는다.
const CodeFragment = (() => {
  const CONTEXT_LINES = 2; // 지목된 범위 위아래로 몇 줄을 더 보여줄지

  function splitLines(content) {
    return String(content).split(/\r\n|\r|\n/);
  }

  /** files 맵에서 ref.file을 찾는다. 정확한 경로 우선, 없으면 P02Engine의 베이스네임 폴백. */
  function resolveFile(files, refFile) {
    if (!refFile) return null;
    if (Object.prototype.hasOwnProperty.call(files, refFile)) return refFile;
    return P02Engine.findFileByBasename(files, refFile.split("/").pop());
  }

  /**
   * @param {object} files  {path: content}
   * @param {object} ref    {file, lines:[start,end]}
   * @returns {{valid:true,file,lines:[number,number],text:string}|{valid:false,reason:string}}
   */
  function extractFragment(files, ref) {
    if (!ref || !ref.file) return { valid: false, reason: "file 없음" };
    const resolved = resolveFile(files, ref.file);
    if (!resolved) return { valid: false, reason: `파일을 찾을 수 없음: ${ref.file}` };

    const lines = splitLines(files[resolved]);
    let [start, end] = Array.isArray(ref.lines) && ref.lines.length === 2 ? ref.lines : [1, Math.min(lines.length, 20)];
    start = Math.max(1, Math.min(Number(start) || 1, lines.length));
    end = Math.max(start, Math.min(Number(end) || start, lines.length));
    // LLM이 지목한 범위가 파일 전체 길이를 넘으면(예: 300줄짜리 파일에 500번째 줄) 클램프된
    // 값 자체가 이미 원래 지목과 다르다는 뜻 -- 그 경우도 무효로 처리해 잘못된 근거를
    // "비슷하게 맞았다"고 넘기지 않는다.
    if (Array.isArray(ref.lines) && (ref.lines[0] > lines.length || ref.lines[1] > lines.length)) {
      return { valid: false, reason: `줄 범위가 파일 길이(${lines.length}줄)를 벗어남: ${JSON.stringify(ref.lines)}` };
    }

    const ctxStart = Math.max(1, start - CONTEXT_LINES);
    const ctxEnd = Math.min(lines.length, end + CONTEXT_LINES);
    const text = lines.slice(ctxStart - 1, ctxEnd).join("\n");
    return { valid: true, file: resolved, lines: [start, end], contextLines: [ctxStart, ctxEnd], text };
  }

  /** 사람이 읽는 참조 표기: "path/to/file.py:12-34" */
  function formatRef(ref) {
    if (!ref || !ref.file) return "(참조 없음)";
    const [s, e] = ref.lines || [];
    if (s === undefined) return ref.file;
    return s === e ? `${ref.file}:${s}` : `${ref.file}:${s}-${e}`;
  }

  /** 여러 파편을 프롬프트에 넣을 하나의 코드 블록으로 합친다. */
  function formatFragmentBlock(fragments) {
    return fragments
      .filter((f) => f.valid)
      .map((f) => `### ${formatRef(f)}\n\`\`\`\n${f.text}\n\`\`\``)
      .join("\n\n");
  }

  /**
   * 코드베이스 전체를 프롬프트에 넣을 하나의 블록으로 truncate. maxChars 예산 안에서
   * 파일을 순서대로 채우고, 넘치면 남은 파일은 파일명만 나열한다(있다는 사실은 알리되
   * 내용을 짜깁기해 왜곡하지 않는다).
   */
  function buildCodeBlock(files, { maxChars = 12000 } = {}) {
    const paths = Object.keys(files).sort();
    let used = 0;
    const included = [];
    const omitted = [];
    for (const path of paths) {
      const chunk = `### ${path}\n\`\`\`\n${files[path]}\n\`\`\`\n\n`;
      if (used + chunk.length > maxChars) { omitted.push(path); continue; }
      included.push(chunk);
      used += chunk.length;
    }
    let block = included.join("");
    if (omitted.length) {
      block += `\n(문자 수 예산 초과로 아래 ${omitted.length}개 파일은 내용을 생략함: ${omitted.join(", ")})`;
    }
    return block;
  }

  return { extractFragment, formatRef, formatFragmentBlock, buildCodeBlock, resolveFile };
})();
