// P04의 4개 페이지(index -> analysis -> session -> report) 사이 핸드오프.
//
// shared/session-state.js와 나란히 쓴다: ZIP 파일맵(IndexedDB)만 그쪽 것을 그대로 재사용하고
// (D210에서 이미 해결된 문제라 다시 풀 이유가 없다), P04 고유 페이로드는 여기서 own한다.
// 키 접두사를 분리해 code-qna 도구와 같은 브라우저에서 동시에 열어도 서로 덮어쓰지 않는다.
//
// D-poc5: 큰 페이로드(전체 소스 코드)는 sessionStorage에 넣지 않는다.
//   WHY: session-state.js 헤더가 이미 기록한 이유와 동일 -- 이 코드베이스는 조용한 잘림으로
//   두 번 데인 적이 있다(D153, D162). 코드 본문은 ZIP 파일맵(IndexedDB)이나 GitHub에서 다시
//   읽고, 여기엔 "무엇을 다시 읽어야 하는지"(경로·라인)만 남긴다.
//   COST: analysis/session 페이지가 코드를 다시 로드해야 한다.
//   EXIT: 파편만 저장하는 지금 구조로도 부족해지면(예: 원격 저장소가 사라진 세션 복원)
//   code fragment 텍스트까지 IndexedDB로 옮기면 된다 -- sessionStorage로 되돌리지 말 것.
const POCState = (() => {
  const P = "teamiz_p04_";
  const KEYS = {
    setup: P + "setup",        // 교안/teaches/요구사항/제출 방식
    analysis: P + "analysis",  // 2단계 산출물
    session: P + "session",    // 3단계 진행/결과
  };

  function safeSet(key, value) {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
      return { ok: true };
    } catch (err) {
      const isQuota = err && (err.name === "QuotaExceededError" || err.code === 22);
      return {
        ok: false,
        error: err,
        hint: isQuota
          ? "저장 용량 초과 -- 선택한 교안/코드 파편이 너무 큽니다 (브라우저 세션 저장공간 한도)"
          : undefined,
      };
    }
  }

  function safeGet(key) {
    try {
      const raw = sessionStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  /**
   * 1단계 결과.
   * @param {object} p
   * @param {Array} p.teaches      선택된 teach 3개 (원본 객체 그대로 -- 보고서의 교안 참조가 페이지 정보를 쓴다)
   * @param {object} p.curriculum  {run_id, schema, source_filename, source_label} 또는 null(수동 입력)
   * @param {Array<string>} p.requirements  P/F 판정 대상 요구사항
   * @param {object} p.submission  {method:"pat"|"zip", repoInput?, branch?, zipName?}
   * @param {string} p.model
   */
  function saveSetup(p) { return safeSet(KEYS.setup, { ...p, saved_at: new Date().toISOString() }); }
  function loadSetup() { return safeGet(KEYS.setup); }

  /**
   * 2단계 결과. codeContexts는 질문에 실제로 쓰인 파일만(경로+본문), 전체 파일맵이 아니다.
   */
  function saveAnalysis(p) { return safeSet(KEYS.analysis, { ...p, saved_at: new Date().toISOString() }); }
  function loadAnalysis() { return safeGet(KEYS.analysis); }

  /** 3단계 결과(문제별 레벨 기록 + 최종 판정). */
  function saveSession(p) { return safeSet(KEYS.session, { ...p, saved_at: new Date().toISOString() }); }
  function loadSession() { return safeGet(KEYS.session); }

  function clearAll() {
    for (const k of Object.values(KEYS)) sessionStorage.removeItem(k);
  }

  return { KEYS, saveSetup, loadSetup, saveAnalysis, loadAnalysis, saveSession, loadSession, clearAll };
})();
