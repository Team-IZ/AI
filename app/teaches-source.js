// 0단계(교안 분석)의 산출물을 이 PoC로 들여오는 어댑터.
//
// D-poc3: 교안 분석을 여기서 다시 구현하지 않고, 이미 돌아가는 P01(curriculum-manager,
// feat/pdf_analysis)이 DB에 남긴 산출물을 읽어 쓴다.
//   WHY: P01은 PDF 청크 분할 -> 청크 분석 -> refine 루프 -> 질문 생성까지 이미 운영 중이고
//   전용 Worker(오케스트레이터)까지 딸려 있다. 그걸 이 브랜치에 복제하면 유지보수 지점이
//   둘로 갈라진다. 이 PoC가 실제로 필요한 건 "질문에 쓸 teaches"뿐이다.
//   COST: 교안이 아직 분석되지 않았다면 이 PoC 안에서 해결할 수 없다 -- 사용자를
//   curriculum-manager로 보내야 한다. 또한 로그인(Supabase auth) 없이는 목록이 비어 있다.
//   EXIT: 아래 parseManualTeaches()가 DB 없이도 쓸 수 있는 우회로다(JSON 붙여넣기).
//
// D-poc4: 편의 뷰(pdf_analysis_units_view)가 아니라 artifacts의 unit_map 원본을 읽는다.
//   WHY: 라이브 뷰의 실제 컬럼이 저장소의 스키마 파일과 달랐다(2026-07-28 실측: 파일에는
//   있는 unit_id 컬럼이 라이브에는 없음 -- "column ... does not exist"). 뷰는 사람이
//   Table Editor에서 훑어보라고 만든 것이고, 그 정의는 이 저장소가 소유하지 않는다.
//   unit_map artifact는 P01이 직접 쓴 원본이라 P01이 바뀌지 않는 한 형태가 바뀌지 않는다.
//   COST: 유닛/개념 평탄화를 클라이언트에서 직접 해야 한다(아래 toTeaches).
//   EXIT: 뷰 컬럼이 확정되면 listCurricula()만 뷰 조회로 바꿔도 나머지는 그대로다.
const TeachesSource = (() => {
  // P01이 unit_map/graph를 쓰는 두 곳. public은 원본 Pipeline Lab의 P01 탭이,
  // pdf_analysis는 curriculum-manager가 쓴다(같은 프로젝트, 스키마만 다름).
  const SOURCES = [
    { schema: "public", label: "Pipeline Lab P01" },
    { schema: "pdf_analysis", label: "curriculum-manager" },
  ];

  function table(client, schema, name) {
    return schema === "public" ? client.from(name) : client.schema(schema).from(name);
  }

  /**
   * 분석 완료된 교안 목록. 로그인이 안 돼 있으면 RLS 때문에 빈 배열이 온다(에러 아님).
   * @returns {Promise<Array<{run_id,schema,source_label,source_filename,started_at,model,status}>>}
   */
  async function listCurricula({ limit = 50 } = {}) {
    if (!LabDB.isConfigured()) return [];
    const client = await LabDB.ensureClient();
    const out = [];
    for (const src of SOURCES) {
      try {
        const { data, error } = await table(client, src.schema, "runs")
          .select("id,model,status,started_at,input_meta")
          .eq("pipeline", "p01")
          .order("started_at", { ascending: false })
          .limit(limit);
        // 스키마가 PostgREST에 노출돼 있지 않으면 여기서 404가 난다 -- 그 소스만 건너뛰고
        // 나머지로 계속한다(둘 중 하나만 살아 있어도 이 PoC는 동작해야 한다).
        if (error) continue;
        for (const r of data || []) {
          out.push({
            run_id: r.id,
            schema: src.schema,
            source_label: src.label,
            source_filename: (r.input_meta && r.input_meta.source_filename) || null,
            started_at: r.started_at,
            model: r.model,
            status: r.status,
          });
        }
      } catch (_) {
        // 네트워크/권한 문제 -- 목록이 비는 건 치명적이지 않다(수동 입력 경로가 있다).
      }
    }
    out.sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));
    return out;
  }

  /** 선택한 교안의 unit_map 원본. 없으면 null. */
  async function loadUnitMap({ run_id, schema }) {
    const client = await LabDB.ensureClient();
    const { data, error } = await table(client, schema, "artifacts")
      .select("content,created_at")
      .eq("run_id", run_id)
      .eq("kind", "unit_map")
      .order("created_at", { ascending: false })
      .limit(1);
    if (error) throw new Error(`unit_map 조회 실패: ${error.message}`);
    if (!data || !data.length) return null;
    return data[0].content;
  }

  /**
   * unit_map -> teach 목록 평탄화.
   * teach id는 P01이 그래프 노드에 쓰는 규약(`concepts:01:1`)을 그대로 따른다 --
   * P01의 questions artifact가 source_node_ids로 같은 문자열을 쓰므로, 나중에 이 PoC의
   * 문제와 P01이 만든 질문을 같은 키로 대조할 수 있다(p01-runner.js의 노드 생성부 참고).
   */
  function toTeaches(unitMap, { kinds = ["concepts", "code_examples", "cautions"] } = {}) {
    if (!unitMap || typeof unitMap !== "object") return [];
    const teaches = [];
    const unitIds = Object.keys(unitMap).sort();
    for (const unitId of unitIds) {
      const unit = unitMap[unitId] || {};
      for (const group of kinds) {
        const items = Array.isArray(unit[group]) ? unit[group] : [];
        items.forEach((item, idx) => {
          teaches.push({
            id: `${group}:${unitId}:${idx + 1}`,
            unit_id: unitId,
            unit_title: unit.unit_title || "",
            unit_pages: normalizePages(unit.source_pages),
            kind: group,
            name: item.name || "(이름 없음)",
            summary: item.summary || "",
            evidence: item.evidence || "",
            source_pages: normalizePages(item.source_pages),
          });
        });
      }
    }
    return teaches;
  }

  function normalizePages(pages) {
    if (!Array.isArray(pages)) return [];
    const nums = pages.map((p) => Number(p)).filter((n) => Number.isFinite(n));
    return [...new Set(nums)].sort((a, b) => a - b);
  }

  /** "4-6" 또는 "4" 형태의 사람이 읽는 페이지 표기. */
  function formatPages(pages) {
    if (!pages || !pages.length) return "페이지 미기록";
    if (pages.length === 1) return `p.${pages[0]}`;
    return `p.${pages[0]}-${pages[pages.length - 1]}`;
  }

  /**
   * DB를 못 쓰는 상황(미로그인/오프라인 데모)의 우회로.
   * P01의 unit_map JSON을 그대로 붙여넣거나, teach 배열을 직접 붙여넣어도 받는다.
   */
  function parseManualTeaches(text) {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.map((t, i) => ({
        id: t.id || `manual:${i + 1}`,
        unit_id: t.unit_id || "manual",
        unit_title: t.unit_title || "",
        unit_pages: normalizePages(t.unit_pages),
        kind: t.kind || "concepts",
        name: t.name || `teach ${i + 1}`,
        summary: t.summary || "",
        evidence: t.evidence || "",
        source_pages: normalizePages(t.source_pages),
      }));
    }
    return toTeaches(parsed);
  }

  /** 프롬프트에 넣을 teaches 블록. 페이지 정보를 반드시 포함한다 -- 보고서의 교안 참조가 이걸 근거로 삼는다. */
  function formatTeachesBlock(teaches) {
    if (!teaches || !teaches.length) return "(선택된 teach 없음)";
    return teaches
      .map((t) => {
        const lines = [
          `- id: ${t.id}`,
          `  unit: Unit ${t.unit_id} ${t.unit_title}`.trimEnd(),
          `  종류: ${t.kind}`,
          `  이름: ${t.name}`,
        ];
        if (t.summary) lines.push(`  요약: ${t.summary}`);
        lines.push(`  교안 위치: ${formatPages(t.source_pages.length ? t.source_pages : t.unit_pages)}`);
        return lines.join("\n");
      })
      .join("\n");
  }

  return {
    SOURCES, listCurricula, loadUnitMap, toTeaches, parseManualTeaches,
    formatTeachesBlock, formatPages, normalizePages,
  };
})();
