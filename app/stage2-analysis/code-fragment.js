// finding/분석문서가 가리키는 {file, symbol} 참조를 실제 소스와 대조해 코드 파편을 뽑는다.
//
// D-poc6: 기존 P02 finding에는 라인 번호가 없다(cognition/judgment는 파일 단위로만
// 판단한다) -- 명세의 "코드 파편(질문에 사용한 코드 파일, line 넘버)"을 만들려면 LLM이
// 코드 위치를 지목해야 한다. LLM이 지어낸 파일/라인을 그대로 믿지 않는다 -- 검증에
// 실패하면 그 항목은 버려지고(analysis.js가 사용자에게 알림), 화면에는 결코 존재하지
// 않는 코드를 보여주지 않는다.
//
// D-poc10 (2026-07-30, 사용자 실사용 재현): 처음엔 LLM에게 줄 번호([시작,끝])까지
// 직접 세게 했는데, 실사용에서 코드 조각 조회가 자꾸 깨졌다 -- LLM이 파일을 다시 세면서
// 줄 번호를 종종 틀렸고(특히 긴 파일), extractFragment는 그걸 "무효"로 버릴 수밖에 없어
// "코드 조각을 확인할 수 없음"만 계속 떴다. 근본 원인은 "LLM이 세는 것"이라는 설계 자체였다.
//   WHY symbol로 바꿨는가: LLM은 코드를 그대로 인용하는 건 잘한다(문자열 복사) -- 세는
//   것만 못한다. 그래서 LLM에게는 실제 코드 한 줄(symbol, 예: "def pay(order, method):")만
//   그대로 옮겨 적게 하고, 그 문자열이 파일의 몇 번째 줄에 있는지는 우리가 직접 찾는다
//   (locateSymbol). "산정된 사실"과 "LLM의 주장"을 분리하는 것 -- 이 저장소가 이미
//   finding 검증에서 쓰던 원칙(D-poc6)을 위치 탐색에도 적용한 것뿐이다.
//   COST: 심볼이 파일 안에 정확히(또는 공백 정규화 후) 존재해야 한다 -- LLM이 코드를
//   요약·재구성해서 인용하면(예: 실제론 여러 줄인데 한 줄로 합쳐 인용) 여전히 못 찾는다.
//   블록 끝(줄 범위의 end) 추정은 들여쓰기 기반 휴리스틱이라 완벽하지 않다(중괄호 언어의
//   한 줄짜리 조건문 등에서 과소/과대 추정 가능) -- 그래도 "시작 줄은 항상 정확하다"가
//   보장되므로, 최소한 학생에게 엉뚱한 코드를 보여주는 사고는 구조적으로 막힌다.
//   EXIT: 언어별 파서를 넣어 블록 끝을 정확히 잡고 싶어지면 locateSymbol의 들여쓰기
//   휴리스틱 부분만 교체하면 된다 -- 심볼 매칭 자체는 그대로 재사용 가능.
const CodeFragment = (() => {
  const CONTEXT_LINES = 2; // 지목된 범위 위아래로 몇 줄을 더 보여줄지
  const BLOCK_MAX_LINES = 40; // 심볼 탐색 시 블록 끝을 추정하는 최대 범위

  function splitLines(content) {
    return String(content).split(/\r\n|\r|\n/);
  }

  function normalizeForMatch(s) {
    return s.replace(/\s+/g, " ").trim();
  }

  /** files 맵에서 ref.file을 찾는다. 정확한 경로 우선, 없으면 P02Engine의 베이스네임 폴백. */
  function resolveFile(files, refFile) {
    if (!refFile) return null;
    if (Object.prototype.hasOwnProperty.call(files, refFile)) return refFile;
    return P02Engine.findFileByBasename(files, refFile.split("/").pop());
  }

  /**
   * 실제 코드 한 줄(symbol)이 파일의 몇 번째 줄에 있는지 우리가 직접 찾는다 -- LLM에게
   * 세게 하지 않는다(D-poc10).
   * @returns {{valid:true,file,lines:[start,end],matchedLine:number}|{valid:false,reason:string}}
   */
  function locateSymbol(files, refFile, symbol) {
    const resolved = resolveFile(files, refFile);
    if (!resolved) return { valid: false, reason: `파일을 찾을 수 없음: ${refFile}` };
    const needle = String(symbol || "").trim();
    if (!needle) return { valid: false, reason: "symbol이 비어있음" };

    const lines = splitLines(files[resolved]);
    let idx = lines.findIndex((l) => l.includes(needle));
    if (idx === -1) {
      const normNeedle = normalizeForMatch(needle);
      idx = lines.findIndex((l) => normalizeForMatch(l).includes(normNeedle));
    }
    if (idx === -1) {
      return { valid: false, reason: `코드에서 찾을 수 없음: "${needle.slice(0, 60)}"` };
    }

    // 블록 끝 추정: 들여쓰기가 시작줄과 같거나 얕아지는 다음 비어있지 않은 줄 전까지.
    // 시작 줄(idx)은 문자열 매칭으로 확정된 사실이라 항상 정확하다 -- 이 추정은 "얼마나
    // 더 보여줄지"에만 영향을 준다.
    const baseIndent = (lines[idx].match(/^\s*/) || [""])[0].length;
    let endIdx = idx;
    for (let i = idx + 1; i < lines.length && i - idx < BLOCK_MAX_LINES; i++) {
      const line = lines[i];
      if (!line.trim()) { endIdx = i; continue; }
      const indent = (line.match(/^\s*/) || [""])[0].length;
      if (indent <= baseIndent) break;
      endIdx = i;
    }
    while (endIdx > idx && !lines[endIdx].trim()) endIdx--; // 끝의 빈 줄 제거

    return { valid: true, file: resolved, lines: [idx + 1, endIdx + 1], matchedLine: idx + 1 };
  }

  function buildFragmentFromLines(files, resolved, lines, start, end) {
    const ctxStart = Math.max(1, start - CONTEXT_LINES);
    const ctxEnd = Math.min(lines.length, end + CONTEXT_LINES);
    const text = lines.slice(ctxStart - 1, ctxEnd).join("\n");
    return { valid: true, file: resolved, lines: [start, end], contextLines: [ctxStart, ctxEnd], text };
  }

  /**
   * @param {object} files  {path: content}
   * @param {object} ref    {file, symbol} 우선, 없으면 하위호환으로 {file, lines:[start,end]}
   * @returns {{valid:true,file,lines:[number,number],text:string}|{valid:false,reason:string}}
   */
  function extractFragment(files, ref) {
    if (!ref || !ref.file) return { valid: false, reason: "file 없음" };

    if (ref.symbol) {
      const located = locateSymbol(files, ref.file, ref.symbol);
      if (!located.valid) return located;
      const lines = splitLines(files[located.file]);
      return buildFragmentFromLines(files, located.file, lines, located.lines[0], located.lines[1]);
    }

    // 하위호환 경로 -- 이미 산정된(추측 아닌) lines를 직접 넘기는 내부 호출용
    // (예: HintLadder.freezeQuestionSet이 자기 자신의 이전 결과를 재사용할 때).
    const resolved = resolveFile(files, ref.file);
    if (!resolved) return { valid: false, reason: `파일을 찾을 수 없음: ${ref.file}` };
    const lines = splitLines(files[resolved]);
    let [start, end] = Array.isArray(ref.lines) && ref.lines.length === 2 ? ref.lines : [1, Math.min(lines.length, 20)];
    if (Array.isArray(ref.lines) && (ref.lines[0] > lines.length || ref.lines[1] > lines.length)) {
      return { valid: false, reason: `줄 범위가 파일 길이(${lines.length}줄)를 벗어남: ${JSON.stringify(ref.lines)}` };
    }
    start = Math.max(1, Math.min(Number(start) || 1, lines.length));
    end = Math.max(start, Math.min(Number(end) || start, lines.length));
    return buildFragmentFromLines(files, resolved, lines, start, end);
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
   *
   * D-poc13 (2026-07-31): opts.order로 채우는 순서를 주입할 수 있다. **기본값은 오늘과
   * 동일한 알파벳순이라, 호출부를 안 바꾸면 동작이 한 비트도 변하지 않는다.**
   *   WHY: 알파벳순은 중요도 신호가 0이다 -- 알파벳으로 늦은 핵심 파일이 잘리고 이른 사소한
   *   파일이 예산을 먹는다. 그런데 이 함수가 도는 시점(p04-1 이전)에도 구조 신호는 이미
   *   존재한다: P02Engine.run()이 먼저 끝나 있고 그 결과의
   *   scan.tier_a_structural.fan_in이 그대로 쓸 수 있는 중요도다(새 계산/새 LLM 호출 0).
   *   CodeCandidates.orderFilesByImportance(files, fanIn)가 그 순서를 만든다.
   *   COST: fan_in은 basename 키라 서로 다른 폴더의 동명 파일이 합산돼 있다(스캐너 쪽 계약,
   *   shared/p02-engine.js:148의 같은 경고). 순서만 바뀌고 포함/제외 판정 로직은 그대로라
   *   최악이라도 "다른 순서로 같은 예산을 채움"이다.
   *   EXIT: poc-engine.js:81의 호출부에 order를 넘기는 한 줄이 전부다. 알파벳순보다
   *   나쁘다는 게 관측되면 그 한 줄을 되돌린다.
   *   NOTE 예산 초과 시 stop이 아니라 skip인 것은 유지한다 -- 랭크 순으로 채우다 첫 파일이
   *   예산보다 크면 그 자리에서 멈추는 구현(Team-IZ-AI-codemap의
   *   app/engines/codemap/shortlist.py)은 결과가 통째로 비어버린다. 여기서는 그 파일만
   *   건너뛰고 다음 파일을 계속 시도한다.
   */
  // D-fix (redteam audit H4, 2026-08-04): a submitted file containing a literal ``` line
  // used to close the fence early, dumping the rest of that file's content into the
  // prompt's instruction-level context (unfenced) -- e.g. a fake "## 규칙" section could
  // follow, and the model has no structural way to tell it apart from the real prompt.
  // CommonMark's own rule for this: a fence only closes on a run of backticks *at least
  // as long as* the one that opened it, so a fence longer than any backtick run inside
  // the content can never be closed early by that content, regardless of what it says.
  function fenceFor(text) {
    const runs = String(text).match(/`+/g) || [];
    const longest = runs.reduce((max, run) => Math.max(max, run.length), 0);
    return "`".repeat(Math.max(3, longest + 1));
  }

  function buildCodeBlock(files, { maxChars = 12000, order = null } = {}) {
    const paths = Array.isArray(order) && order.length
      ? order.filter((p) => Object.prototype.hasOwnProperty.call(files, p))
      : Object.keys(files).sort();
    let used = 0;
    const included = [];
    const omitted = [];
    for (const path of paths) {
      const fence = fenceFor(files[path]);
      const chunk = `### ${path}\n${fence}\n${files[path]}\n${fence}\n\n`;
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

  return { extractFragment, locateSymbol, formatRef, formatFragmentBlock, buildCodeBlock, resolveFile, fenceFor };
})();

// D-poc13: 브라우저에는 module이 없어 no-op. node --test에서 이 파일의 순수 로직을
// (app/code-candidates.test.js가) 그대로 불러 검증하기 위한 한 줄이다 -- locateSymbol을
// 복제하지 않고 실제 구현을 테스트에서도 쓰기 위한 것.
if (typeof module !== "undefined" && module.exports) module.exports = CodeFragment;
