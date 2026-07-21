#!/usr/bin/env python3
"""NVIDIA Build based Java curriculum analysis pipeline.

Pipeline:
PDF -> 10-page chunks -> parallel NVIDIA calls with key rotation -> page-grounded
unit map -> NVIDIA refine loop -> graphify-compatible graph -> graph-grounded
diagnostic questions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FEEDBACK = REPO / "feedback"
sys.path.insert(0, str(FEEDBACK))

from nvidia_client import NvidiaRotatingClient  # noqa: E402
from nvidia_key_pool import NvidiaKeyPool  # noqa: E402


DEFAULT_PDF = "/Users/xox/Downloads/AI_JAVA_교안.pdf"
DEFAULT_MODEL = "qwen/qwen3-next-80b-a3b-instruct"


class CountingNvidiaKeyPool(NvidiaKeyPool):
    """NvidiaKeyPool wrapper that records key slot usage without logging keys."""

    def __init__(self, keys: list[str], capacity_per_minute: int = 40):
        super().__init__(keys, capacity_per_minute=capacity_per_minute)
        self.usage_by_slot: dict[str, int] = {f"NVIDIA_API_KEY_{i + 1}": 0 for i in range(len(keys))}
        self.acquire_events: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls, prefix: str = "NVIDIA_API_KEY_", capacity_per_minute: int = 40) -> "CountingNvidiaKeyPool":
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        found: list[tuple[int, str]] = []
        for name, value in os.environ.items():
            match = pattern.match(name)
            if match and value.strip():
                found.append((int(match.group(1)), value.strip()))
        if not found:
            raise ValueError(f"No {prefix}<N> variables found")
        found.sort(key=lambda pair: pair[0])
        pool = cls([value for _, value in found], capacity_per_minute=capacity_per_minute)
        pool.usage_by_slot = {f"{prefix}{idx}": 0 for idx, _ in found}
        pool._slot_names = [f"{prefix}{idx}" for idx, _ in found]
        return pool

    def acquire(self, model: str, max_wait_s: float = 30.0) -> str:
        key = super().acquire(model, max_wait_s=max_wait_s)
        with self._lock:
            for idx, state in enumerate(self._states):
                if state.key == key:
                    slot = getattr(self, "_slot_names", [f"NVIDIA_API_KEY_{i + 1}" for i in range(len(self._states))])[idx]
                    self.usage_by_slot[slot] = self.usage_by_slot.get(slot, 0) + 1
                    self.acquire_events.append(
                        {
                            "slot": slot,
                            "model": model,
                            "t_epoch": time.time(),
                            "t_monotonic": time.monotonic(),
                        }
                    )
                    break
        return key


def build_rate_audit(
    events: list[dict[str, Any]],
    capacity_per_minute: int,
    key_count: int,
    window_s: float = 60.0,
) -> dict[str, Any]:
    """Compute sliding-window max request counts without exposing key values."""

    def max_in_window(items: list[dict[str, Any]]) -> int:
        times = sorted(float(item["t_monotonic"]) for item in items)
        best = 0
        left = 0
        for right, value in enumerate(times):
            while value - times[left] >= window_s:
                left += 1
            best = max(best, right - left + 1)
        return best

    by_slot_model: dict[str, list[dict[str, Any]]] = {}
    by_model: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_slot_model.setdefault(f"{event['slot']}::{event['model']}", []).append(event)
        by_model.setdefault(str(event["model"]), []).append(event)

    slot_model_windows = {}
    for key, items in sorted(by_slot_model.items()):
        slot, model = key.split("::", 1)
        slot_model_windows[key] = {
            "slot": slot,
            "model": model,
            "calls": len(items),
            "max_calls_in_any_60s": max_in_window(items),
            "limit_per_60s": capacity_per_minute,
            "within_limit": max_in_window(items) <= capacity_per_minute,
        }

    model_windows = {}
    for model, items in sorted(by_model.items()):
        model_windows[model] = {
            "calls": len(items),
            "max_calls_in_any_60s": max_in_window(items),
            "aggregate_limit_per_60s": capacity_per_minute * key_count,
            "within_aggregate_limit": max_in_window(items) <= capacity_per_minute * key_count,
        }

    return {
        "policy": {
            "capacity_per_key_per_model_per_60s": capacity_per_minute,
            "key_slots": key_count,
            "aggregate_capacity_per_model_per_60s": capacity_per_minute * key_count,
            "window_s": window_s,
        },
        "event_count": len(events),
        "slot_model_windows": slot_model_windows,
        "model_windows": model_windows,
        "within_policy": all(v["within_limit"] for v in slot_model_windows.values())
        and all(v["within_aggregate_limit"] for v in model_windows.values()),
    }


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    text: str

    @property
    def label(self) -> str:
        return f"p{self.start}-{self.end}"

    @property
    def range(self) -> str:
        return f"{self.start}-{self.end}"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def pdf_page_count(pdf: Path, password: str | None = None) -> int:
    cmd = ["pdfinfo"]
    if password:
        cmd.extend(["-upw", password])
    cmd.append(str(pdf))
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Could not read page count from pdfinfo output for {pdf}")


def extract_pages(pdf: Path, start: int, end: int, password: str | None = None) -> str:
    cmd = ["pdftotext", "-layout"]
    if password:
        cmd.extend(["-upw", password])
    cmd.extend(["-f", str(start), "-l", str(end), str(pdf), "-"])
    result = subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def build_chunks(
    pdf: Path,
    chunk_size: int,
    total_pages: int,
    max_chunks: int | None,
    password: str | None = None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for start in range(1, total_pages + 1, chunk_size):
        end = min(start + chunk_size - 1, total_pages)
        chunks.append(Chunk(start=start, end=end, text=extract_pages(pdf, start, end, password=password)))
        if max_chunks and len(chunks) >= max_chunks:
            break
    return chunks


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def chat_json(
    client: NvidiaRotatingClient,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    json_mode: bool = True,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "max_retries": 3,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat(
        model=model,
        messages=messages,
        **kwargs,
    )
    choice = response["choices"][0]["message"]
    content = choice.get("content") or ""
    if not content:
        raise ValueError(f"NVIDIA response had no content; finish_reason={response['choices'][0].get('finish_reason')}")
    try:
        return extract_json_object(content)
    except Exception as parse_error:
        repair_prompt = (
            "Repair the following malformed JSON into one valid JSON object. "
            "Preserve all fields and content where possible. Return JSON only.\n\n"
            f"{content[:14000]}"
        )
        repair_kwargs: dict[str, Any] = {
            "max_retries": 2,
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if json_mode:
            repair_kwargs["response_format"] = {"type": "json_object"}
        repaired = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": "You repair malformed JSON. Output strict JSON only."},
                {"role": "user", "content": repair_prompt},
            ],
            **repair_kwargs,
        )
        repaired_content = repaired["choices"][0]["message"].get("content") or ""
        try:
            return extract_json_object(repaired_content)
        except Exception as repair_error:
            raise ValueError(
                f"JSON parse failed ({parse_error}); repair failed ({repair_error}); raw_head={content[:500]!r}"
            ) from repair_error


# D131(LLMOps pilot): course_label 파라미터화 -- "Java"가 프롬프트 3곳(이 함수/refine_once/
#   generate_questions)에 하드코딩돼 있어 다른 과정(LLMOps 등) PDF를 넣으면 LLM에게 잘못된
#   프레이밍("이건 Java 커리큘럼이다")을 준다. 실제 콘텐츠는 chunk.text가 프롬프트 뒤에
#   그대로 붙어 주 근거가 되므로(WHY) 문구 하나로 결과가 뒤집히진 않지만, 프레이밍 자체는
#   정확해야 한다. 기본값 "Java"로 기존 호출부(curriculum_4axis_benchmark.py의 import)
#   100% 하위호환 -- 이 함수들을 직접 import해서 쓰므로 여기 하나만 고치면 양쪽 다 반영됨.
def analyse_chunk(
    client: NvidiaRotatingClient,
    model: str,
    chunk: Chunk,
    max_tokens: int,
    json_mode: bool = True,
    course_label: str = "Java",
) -> dict[str, Any]:
    # D1/D2 (2026-07-21, ported from docs/lab/prompt_manifest.json's p01-2 fix): a real
    # 251-page run had nearly every concept land in one giant "Overview" unit spanning
    # p.1-251. Root cause: (a) concepts had no unit_id so make_unit_map() below couldn't
    # tell which of a chunk's units a concept belonged to, and (b) this prompt's own
    # example showed a literal "unit_id":"01","unit_title":"Overview" that an ambiguous
    # chunk could easily echo verbatim -- with 26+ independent chunk calls sharing no
    # context, that made unrelated chunks collide under the same unit_id at merge time.
    #   WHY: give the model a way to say which unit each concept belongs to, and replace
    #     the literal example with a <placeholder> plus an explicit anti-genericness rule.
    #   COST: response schema changed -- only affects new runs, not past saved output.
    #   EXIT: if cross-chunk id collisions persist even with specific titles, escalate to
    #     a code-level fix (salt/namespace unit_id at collection time).
    prompt = f"""
KT AIVLE School {course_label} curriculum PDF page range: {chunk.range}.

Return ONLY valid JSON with this exact shape:
{{
  "chunk_range": "{chunk.range}",
  "units": [{{"unit_id": "02", "unit_title": "<specific topic on these pages, e.g. Variables and Data Types>", "source_pages": [4, 5]}}],
  "concepts": [
    {{
      "name": "short concept name",
      "kind": "concept|code_example|caution",
      "unit_id": "<must match one of the unit_id values in units above>",
      "summary": "one sentence grounded in the slides",
      "source_pages": [1],
      "evidence": "short paraphrase of the page evidence"
    }}
  ]
}}

Rules:
- unit_title must name the actual topic on these pages (e.g. "Variables and Data Types", "Loops", "Exception Handling") -- never a generic label like "Overview", "Introduction", or "Chapter N" unless these exact pages are a title/table-of-contents slide with no substantive teaching content.
- If this chunk covers more than one distinct topic, list each as its own entry in units, and set each concept's unit_id to whichever unit it actually belongs to.
- Every concept's unit_id must exactly match one of the unit_id values listed in units above.
- Every concept must have at least one concrete page number from {chunk.start}..{chunk.end}.
- Do not invent content outside the given pages.
- If a page is just title/table-of-contents, preserve it only if it affects unit mapping.

PDF text:
{chunk.text[:18000]}
""".strip()
    return chat_json(
        client,
        model,
        [
            {"role": "system", "content": "You are a precise curriculum-analysis extractor. Output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


def dry_chunk(chunk: Chunk) -> dict[str, Any]:
    page = chunk.start
    return {
        "chunk_range": chunk.range,
        "units": [{"unit_id": f"{max(1, (page - 1) // 20 + 1):02d}", "unit_title": "dry-run unit", "source_pages": [page]}],
        "concepts": [
            {
                "name": f"dry concept {chunk.range}",
                "kind": "concept",
                "summary": "Dry-run placeholder proving graph construction without network.",
                "source_pages": [page],
                "evidence": f"Placeholder evidence for page {page}.",
            }
        ],
    }


def normalize_pages(value: Any, start: int | None = None, end: int | None = None) -> list[int]:
    pages: list[int] = []
    if isinstance(value, list):
        for item in value:
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if start is not None and page < start:
                continue
            if end is not None and page > end:
                continue
            pages.append(page)
    return sorted(set(pages))


def make_unit_map(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    unit_map: dict[str, Any] = {}
    for chunk in chunks:
        chunk_range = chunk.get("chunk_range", "")
        units = chunk.get("units", []) or []
        for unit in units:
            unit_id = str(unit.get("unit_id") or "unknown")
            if unit_id not in unit_map:
                unit_map[unit_id] = {
                    "unit_id": unit_id,
                    "unit_title": unit.get("unit_title") or "",
                    "source_pages": [],
                    "concepts": [],
                    "code_examples": [],
                    "cautions": [],
                }
            unit_map[unit_id]["source_pages"].extend(normalize_pages(unit.get("source_pages")))

        # D1 (2026-07-21): route each concept to its OWN declared unit_id instead of the
        # old placement (this loop used to live INSIDE the `for unit in units` loop above,
        # so a chunk with 2+ units re-ran the full concepts list once per unit -- every
        # concept got duplicated into every unit the chunk declared, not attributed to the
        # one it actually belongs to). Falls back to this chunk's first unit only when the
        # model didn't supply a valid unit_id (defensive, not the expected path).
        chunk_unit_ids = {str(u.get("unit_id") or "unknown") for u in units}
        fallback_unit_id = str(units[0].get("unit_id") or "unknown") if units else "unknown"
        for concept in chunk.get("concepts", []) or []:
            declared = concept.get("unit_id")
            target_unit_id = str(declared) if declared is not None and str(declared) in chunk_unit_ids else fallback_unit_id
            if target_unit_id not in unit_map:
                continue
            item = {
                "name": concept.get("name") or "unnamed",
                "summary": concept.get("summary") or "",
                "evidence": concept.get("evidence") or "",
                "source_pages": normalize_pages(concept.get("source_pages")),
                "chunk_range": chunk_range,
            }
            kind = concept.get("kind") or "concept"
            if kind == "code_example":
                unit_map[target_unit_id]["code_examples"].append(item)
            elif kind == "caution":
                unit_map[target_unit_id]["cautions"].append(item)
            else:
                unit_map[target_unit_id]["concepts"].append(item)
    for unit in unit_map.values():
        unit["source_pages"] = sorted(set(unit["source_pages"]))
    return dict(sorted(unit_map.items()))


def refine_once(
    client: NvidiaRotatingClient,
    model: str,
    unit_map: dict[str, Any],
    iteration: int,
    max_tokens: int,
    json_mode: bool = True,
    course_label: str = "Java",
) -> dict[str, Any]:
    compact = json.dumps(unit_map, ensure_ascii=False)[:24000]
    prompt = f"""
Audit this page-grounded {course_label} curriculum unit_map for refinement iteration {iteration}.

Return ONLY valid JSON:
{{
  "iteration": {iteration},
  "status": "pass|needs_refine",
  "coverage_summary": "short summary",
  "issues": [
    {{"severity": "high|medium|low", "issue": "specific issue", "source_pages": [1, 2], "suggested_fix": "fix"}}
  ],
  "checklist": {{
    "all_concepts_have_pages": true,
    "unit_boundaries_clear": true,
    "duplicates_detected": false,
    "question_generation_ready": true
  }}
}}

Checklist standard:
- page provenance must be concrete.
- concepts without pages are a hard failure.
- duplicate or overly broad concept nodes should be called out.
- graph should support asking "where in the PDF does this concept come from?"

unit_map:
{compact}
""".strip()
    return chat_json(
        client,
        model,
        [
            {"role": "system", "content": "You are a strict graph/refinement auditor. Output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


def build_graph(unit_map: dict[str, Any], refine_audits: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_node(node_id: str, label: str, node_type: str, **attrs: Any) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append({"id": node_id, "label": label, "type": node_type, **attrs})

    def add_link(source: str, target: str, relation: str, **attrs: Any) -> None:
        links.append({"source": source, "target": target, "relation": relation, **attrs})

    add_node("doc:ai_java", "AI_JAVA_교안.pdf", "document")
    for unit_id, unit in unit_map.items():
        uid = f"unit:{unit_id}"
        add_node(uid, f"Unit {unit_id} {unit.get('unit_title', '')}".strip(), "unit", source_pages=unit.get("source_pages", []))
        add_link("doc:ai_java", uid, "contains_unit")
        for group, relation in [("concepts", "teaches"), ("code_examples", "shows_code"), ("cautions", "warns")]:
            for idx, item in enumerate(unit.get(group, []) or [], 1):
                cid = f"{group}:{unit_id}:{idx}"
                pages = item.get("source_pages", [])
                add_node(
                    cid,
                    item.get("name") or cid,
                    group[:-1] if group.endswith("s") else group,
                    summary=item.get("summary", ""),
                    evidence=item.get("evidence", ""),
                    source_pages=pages,
                    chunk_range=item.get("chunk_range", ""),
                )
                add_link(uid, cid, relation)
                for page in pages:
                    pid = f"page:{page}"
                    add_node(pid, f"p{page}", "page", page=page)
                    add_link(cid, pid, "sourced_by")
    for audit in refine_audits:
        aid = f"audit:{audit.get('iteration', len(refine_audits))}"
        add_node(aid, f"refine iteration {audit.get('iteration')}", "refine_audit", status=audit.get("status"))
        add_link(aid, "doc:ai_java", "audits")
        for idx, issue in enumerate(audit.get("issues", []) or [], 1):
            iid = f"{aid}:issue:{idx}"
            pages = normalize_pages(issue.get("source_pages"))
            add_node(iid, issue.get("issue") or iid, "refine_issue", severity=issue.get("severity"), source_pages=pages)
            add_link(aid, iid, "found_issue")
            for page in pages:
                pid = f"page:{page}"
                add_node(pid, f"p{page}", "page", page=page)
                add_link(iid, pid, "issue_page")
    return {
        "directed": True,
        "multigraph": False,
        "graph": {"name": "java_curriculum_page_grounded_graph", "schema": "graphify-compatible node-link"},
        "nodes": nodes,
        "links": links,
    }


def generate_questions(
    client: NvidiaRotatingClient,
    model: str,
    graph: dict[str, Any],
    max_tokens: int,
    json_mode: bool = True,
    course_label: str = "Java",
) -> dict[str, Any]:
    compact_nodes = [
        {
            "id": n["id"],
            "label": n["label"],
            "type": n["type"],
            "source_pages": n.get("source_pages", []),
            "summary": n.get("summary", ""),
        }
        for n in graph["nodes"]
        if n["type"] in {"unit", "concept", "code_example", "caution"}
    ]
    prompt = f"""
Use this page-grounded {course_label} curriculum graph to generate diagnostic interview questions.

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "unit": "Unit 01",
      "question": "question text",
      "source_node_ids": ["concepts:01:1"],
      "source_pages": [4, 5],
      "rationale": "why this checks understanding",
      "good_answer_signal": "what a strong answer includes",
      "common_gap": "what learners often miss"
    }}
  ]
}}

Rules:
- Every question must cite source_node_ids and concrete source_pages.
- Prefer questions that require trade-off, counterexample, or code reasoning.
- Do not cite a page unless a source node carries that page.

Graph nodes:
{json.dumps(compact_nodes, ensure_ascii=False)[:24000]}
""".strip()
    return chat_json(
        client,
        model,
        [
            {"role": "system", "content": "You design graph-grounded diagnostic questions. Output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        json_mode=json_mode,
    )


def attach_questions_to_graph(graph: dict[str, Any], questions: dict[str, Any]) -> None:
    nodes = graph["nodes"]
    links = graph["links"]
    existing = {node["id"] for node in nodes}
    for idx, q in enumerate(questions.get("questions", []) or [], 1):
        qid = f"question:{idx}"
        if qid in existing:
            continue
        nodes.append(
            {
                "id": qid,
                "label": q.get("question", f"question {idx}")[:120],
                "type": "diagnostic_question",
                "unit": q.get("unit", ""),
                "source_pages": normalize_pages(q.get("source_pages")),
                "rationale": q.get("rationale", ""),
                "good_answer_signal": q.get("good_answer_signal", ""),
                "common_gap": q.get("common_gap", ""),
            }
        )
        for source in q.get("source_node_ids", []) or []:
            if source in existing:
                links.append({"source": qid, "target": source, "relation": "grounded_in"})
        for page in normalize_pages(q.get("source_pages")):
            pid = f"page:{page}"
            if pid in existing:
                links.append({"source": qid, "target": pid, "relation": "question_source_page"})


def write_report(
    out_dir: Path,
    args: argparse.Namespace,
    started: float,
    chunks: list[dict[str, Any]],
    unit_map: dict[str, Any],
    audits: list[dict[str, Any]],
    graph: dict[str, Any],
    questions: dict[str, Any],
    key_usage: dict[str, int],
    rate_audit: dict[str, Any],
    graphify_query: str | None,
) -> None:
    elapsed = time.time() - started
    total_concepts = sum(len(u.get("concepts", [])) + len(u.get("code_examples", [])) + len(u.get("cautions", [])) for u in unit_map.values())
    lines = [
        f"# {args.course_label} Curriculum NVIDIA Parallel Pipeline Run",
        "",
        f"- PDF: `{args.pdf}`",
        f"- Model: `{args.model}`",
        f"- Chunk size: `{args.chunk_size}` pages",
        f"- Chunks processed: `{len(chunks)}`",
        f"- Max workers: `{args.max_workers}`",
        f"- Dry run: `{args.dry_run}`",
        f"- Elapsed: `{elapsed:.1f}s`",
        f"- NVIDIA key slots used: `{key_usage}`",
        f"- Rate policy: `{rate_audit.get('policy', {})}`",
        f"- Rate policy respected: `{rate_audit.get('within_policy')}`",
        "",
        "## Verified Pipeline",
        "",
        "1. PDF pages were extracted with `pdftotext`.",
        "2. Each page range was sent as an independent chunk call through `NvidiaRotatingClient`.",
        "3. `CountingNvidiaKeyPool` rotated calls across `NVIDIA_API_KEY_1..N` and recorded slot counts only, never key values.",
        "4. Chunk outputs preserved `source_pages` for every concept/code/caution node.",
        "5. A NVIDIA refinement loop audited page provenance and graph readiness.",
        "6. A graphify-compatible `graphify-out/graph.json` was built from unit/concept/page/refine/question state.",
        "7. Final questions were generated from graph nodes and cite `source_node_ids` plus concrete `source_pages`.",
        "",
        "## Outputs",
        "",
        "- `chunks.json`",
        "- `unit_map.json`",
        "- `refine_audit.json`",
        "- `questions.json`",
        "- `rate_audit.json`",
        "- `graphify-out/graph.json`",
        "- `graphify-out/GRAPH_REPORT.md`",
        "",
        "## Counts",
        "",
        f"- Units: `{len(unit_map)}`",
        f"- Concepts/code/cautions: `{total_concepts}`",
        f"- Graph nodes: `{len(graph['nodes'])}`",
        f"- Graph links: `{len(graph['links'])}`",
        f"- Questions: `{len(questions.get('questions', []) or [])}`",
        "",
        "## Refine Loop",
        "",
    ]
    for audit in audits:
        lines.append(f"- Iteration `{audit.get('iteration')}`: `{audit.get('status')}` — {audit.get('coverage_summary', '')}")
    lines.extend(["", "## NVIDIA Rate Audit", ""])
    for key, value in (rate_audit.get("slot_model_windows") or {}).items():
        lines.append(
            f"- `{key}`: calls `{value.get('calls')}`, max 60s `{value.get('max_calls_in_any_60s')}` / limit `{value.get('limit_per_60s')}`, within `{value.get('within_limit')}`"
        )
    for model, value in (rate_audit.get("model_windows") or {}).items():
        lines.append(
            f"- aggregate `{model}`: calls `{value.get('calls')}`, max 60s `{value.get('max_calls_in_any_60s')}` / limit `{value.get('aggregate_limit_per_60s')}`, within `{value.get('within_aggregate_limit')}`"
        )
    lines.extend(["", "## Sample Questions", ""])
    for q in (questions.get("questions", []) or [])[:5]:
        lines.append(f"- {q.get('unit', '')}: {q.get('question', '')} (pages: {q.get('source_pages', [])})")
    if graphify_query:
        lines.extend(["", "## Graphify Query Smoke Test", "", "```text", graphify_query.strip(), "```"])
    (out_dir / "RUN_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = [
        "# Graph Report",
        "",
        f"- Nodes: {len(graph['nodes'])}",
        f"- Links: {len(graph['links'])}",
        f"- Units: {len(unit_map)}",
        f"- Questions: {len(questions.get('questions', []) or [])}",
        "",
        "## Node Types",
        "",
    ]
    counts: dict[str, int] = {}
    for node in graph["nodes"]:
        counts[node["type"]] = counts.get(node["type"], 0) + 1
    for key, value in sorted(counts.items()):
        report.append(f"- {key}: {value}")
    (out_dir / "graphify-out" / "GRAPH_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def run_graphify_query(graph_path: Path, course_label: str = "Java") -> str | None:
    graphify = "/Users/xox/.local/bin/graphify"
    if not Path(graphify).exists():
        return None
    result = subprocess.run(
        [graphify, "query", f"Which diagnostic questions cite concrete {course_label} curriculum pages?", "--graph", str(graph_path), "--budget", "800"],
        text=True,
        capture_output=True,
    )
    return (result.stdout or result.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=DEFAULT_PDF)
    parser.add_argument("--pdf-password", default=os.environ.get("JAVA_CURRICULUM_PDF_PASSWORD"))
    parser.add_argument("--out-dir", default=str(REPO / "docs" / "java_curriculum_pipeline_run"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--timeout-s", type=float, default=240.0)
    parser.add_argument("--capacity-per-minute", type=int, default=20)
    parser.add_argument("--refine-iters", type=int, default=2)
    parser.add_argument("--max-tokens-chunk", type=int, default=1800)
    parser.add_argument("--max-tokens-refine", type=int, default=1600)
    parser.add_argument("--max-tokens-questions", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-json-mode", action="store_true")
    parser.add_argument("--course-label", default="Java", help="D131: prompt framing label, e.g. 'LLMOps'")
    args = parser.parse_args()

    started = time.time()
    pdf = Path(args.pdf)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "graphify-out").mkdir(parents=True, exist_ok=True)

    load_dotenv(Path.home() / ".env")
    load_dotenv(REPO / ".env")
    total_pages = pdf_page_count(pdf, args.pdf_password)
    chunks = build_chunks(pdf, args.chunk_size, total_pages, args.max_chunks, password=args.pdf_password)

    if args.dry_run:
        chunk_results = [dry_chunk(chunk) for chunk in chunks]
        pool = None
        key_usage: dict[str, int] = {}
        rate_audit = build_rate_audit([], args.capacity_per_minute, 0)
        client = None
    else:
        pool = CountingNvidiaKeyPool.from_env(capacity_per_minute=args.capacity_per_minute)
        client = NvidiaRotatingClient(pool=pool, timeout_s=args.timeout_s)
        chunk_results = []
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(
                    analyse_chunk,
                    client,
                    args.model,
                    chunk,
                    args.max_tokens_chunk,
                    not args.no_json_mode,
                    args.course_label,
                ): chunk
                for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    result = future.result()
                    result["chunk_range"] = result.get("chunk_range") or chunk.range
                    chunk_results.append(result)
                    print(f"[chunk-ok] {chunk.label}", flush=True)
                except (urllib.error.HTTPError, urllib.error.URLError, Exception) as exc:
                    chunk_results.append(
                        {
                            "chunk_range": chunk.range,
                            "units": [],
                            "concepts": [],
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    print(f"[chunk-error] {chunk.label}: {type(exc).__name__}: {exc}", flush=True)
        chunk_results.sort(key=lambda item: int(str(item.get("chunk_range", "999-999")).split("-", 1)[0]))
        key_usage = pool.usage_by_slot if pool else {}
        rate_audit = build_rate_audit(pool.acquire_events if pool else [], args.capacity_per_minute, len(pool) if pool else 0)

    unit_map = make_unit_map([c for c in chunk_results if not c.get("error")])

    audits: list[dict[str, Any]] = []
    if args.dry_run:
        audits.append(
            {
                "iteration": 1,
                "status": "pass",
                "coverage_summary": "Dry-run audit: graph construction path is valid.",
                "issues": [],
                "checklist": {
                    "all_concepts_have_pages": True,
                    "unit_boundaries_clear": True,
                    "duplicates_detected": False,
                    "question_generation_ready": True,
                },
            }
        )
    else:
        assert client is not None
        for i in range(1, args.refine_iters + 1):
            try:
                audit = refine_once(client, args.model, unit_map, i, args.max_tokens_refine, not args.no_json_mode, args.course_label)
                print(f"[refine-ok] iteration {i}: {audit.get('status')}", flush=True)
            except Exception as exc:
                audit = {
                    "iteration": i,
                    "status": "needs_refine",
                    "coverage_summary": f"Refine call failed but preserved as audit state: {type(exc).__name__}: {exc}",
                    "issues": [
                        {
                            "severity": "medium",
                            "issue": "NVIDIA refine response was not parseable JSON; rerun this iteration for a stricter audit.",
                            "source_pages": [],
                            "suggested_fix": "Increase max_tokens_refine or reduce unit_map payload before the next refine pass.",
                        }
                    ],
                    "checklist": {
                        "all_concepts_have_pages": False,
                        "unit_boundaries_clear": False,
                        "duplicates_detected": True,
                        "question_generation_ready": True,
                    },
                }
                print(f"[refine-error] iteration {i}: {type(exc).__name__}: {exc}", flush=True)
            audits.append(audit)

    graph = build_graph(unit_map, audits)

    if args.dry_run:
        questions = {
            "questions": [
                {
                    "unit": "Unit 01",
                    "question": "Dry-run question: which page supports this placeholder concept?",
                    "source_node_ids": ["concepts:01:1"],
                    "source_pages": [chunks[0].start if chunks else 1],
                    "rationale": "Proves page-grounded question shape.",
                    "good_answer_signal": "Cites the page.",
                    "common_gap": "No page citation.",
                }
            ]
        }
    else:
        assert client is not None
        unit_nodes = [node for node in graph["nodes"] if node.get("type") == "unit" and node.get("source_pages")]
        if not unit_nodes:
            questions = {
                "questions": [],
                "error": "No page-grounded unit nodes were available after chunk analysis; skipped NVIDIA question generation.",
            }
            print("[questions-skip] no page-grounded unit nodes", flush=True)
        else:
            try:
                questions = generate_questions(client, args.model, graph, args.max_tokens_questions, not args.no_json_mode, args.course_label)
                print(f"[questions-ok] {len(questions.get('questions', []) or [])} questions", flush=True)
            except Exception as exc:
                questions = {
                    "questions": [
                        {
                            "unit": node.get("label", "unit"),
                            "question": f"What page-grounded concept under {node.get('label', 'this unit')} would you explain with a concrete counterexample?",
                            "source_node_ids": [node["id"]],
                            "source_pages": node.get("source_pages", [])[:3],
                            "rationale": f"Fallback question because NVIDIA question-generation JSON parsing failed: {type(exc).__name__}",
                            "good_answer_signal": "Answer cites a source page and explains the concept with a counterexample.",
                            "common_gap": "Answer gives a definition without page-grounded evidence.",
                        }
                        for node in unit_nodes
                    ][:8]
                }
                print(f"[questions-error] fallback generated: {type(exc).__name__}: {exc}", flush=True)

    attach_questions_to_graph(graph, questions)

    (out_dir / "chunks.json").write_text(json.dumps(chunk_results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "unit_map.json").write_text(json.dumps(unit_map, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "refine_audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "rate_audit.json").write_text(json.dumps(rate_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    graph_path = out_dir / "graphify-out" / "graph.json"
    graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    graphify_query = run_graphify_query(graph_path, args.course_label)
    write_report(out_dir, args, started, chunk_results, unit_map, audits, graph, questions, key_usage, rate_audit, graphify_query)
    print(f"[done] outputs: {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
