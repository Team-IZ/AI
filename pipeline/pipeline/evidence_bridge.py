import json
import sys

# D36: D안(B안+C안 결합) 구현 — C안의 finding을 B안의 "Repository Evidence" 형식으로 자동 변환
#   WHY: B안(ROAF-B) 프롬프트는 Step1에서 사람이 직접 Repository를 분석해 "Repository 근거"
#        문단을 써야 했다. C안(이 저장소의 cognition+judgment)은 이 분석을 이미 코드로
#        자동화했으므로, 그 출력을 B안 Rule 엔진이 바로 소비할 "Repository Evidence" +
#        "Suggested Question"으로 변환하면 사람이 매번 다시 분석할 필요가 없어진다
#   COST: 자동 생성된 Evidence는 사람이 쓴 것보다 정형화돼 있어 질문의 자연스러움이 떨어짐
#        (예: B안 원본 질문은 여러 파일을 엮어 서사로 묻는데, 이 브릿지는 finding 단위로 끊어서 묻는다)
#   EXIT: 품질이 부족하면 자동 생성 evidence를 "초안"으로만 쓰고 사람이 다듬는 반자동 모드로 전환.
#        완전 자동화(LLM이 evidence를 서사로 재구성)를 원하면 Anthropic API 붙여서 재작성 단계 추가

DEPTH_LADDER_OPENING = {
    "cognition-isolation": "이 파일이 다른 형제 파일들과 달리 허브 모듈과 연결되어 있지 않은데, 이 구조를 선택/방치한 이유를 설명해보세요.",
    "architecture-diffusion": "이 파일이 여러 컴포넌트에서 공유되는 확산 지점인데, 왜 이런 구조를 선택했는지 설명해보세요.",
    "tier-b-risk": "이 코드에 잠재적 위험 패턴이 있는데, 이 부분이 어떤 위험을 가질 수 있는지, 그리고 왜 이렇게 구현했는지 설명해보세요.",
    "repeated-pattern": "이 패턴이 여러 파일에 반복되는데, 공용 모듈로 뽑지 않은 이유를 설명해보세요.",
}


def finding_category(finding_id):
    return finding_id.split(":", 1)[0]


def build_evidence_packet(finding):
    category = finding_category(finding["id"])
    sub = finding.get("subrubric", {})

    evidence_lines = [f"파일: {finding.get('file') or '(다중 파일)'}", f"관측: {finding['finding']}"]
    if finding.get("pattern_key"):
        evidence_lines.append(f"패턴: {finding['pattern_key']}" + (f" (idiom_filtered: {finding.get('idiom_note')})" if finding.get("idiom_filtered") else " (미확정 — idiom 후보)"))
    if sub:
        for axis in ("design_intent", "question_value", "risk"):
            if axis in sub:
                evidence_lines.append(f"{axis} 서브축: {sub[axis]['sub']} (총점 {sub[axis]['total']})")

    question = DEPTH_LADDER_OPENING.get(category, "이 finding에 대해 설명해보세요.")

    return {
        "finding_id": finding["id"],
        "priority": finding["priority"],
        "current_grade": {
            "design_intent": finding["design_intent"],
            "question_value": finding["question_value"],
            "risk": finding["risk"],
        },
        "repository_evidence": "\n".join(evidence_lines),
        "suggested_question": question,
        "idiom_or_risk_status": (
            "confirmed_idiom" if finding.get("idiom_filtered")
            else "tier_b_risk" if category == "tier-b-risk"
            else "unresolved_signal"
        ),
    }


def build_all(judgment_output_path):
    with open(judgment_output_path, encoding="utf-8") as f:
        data = json.load(f)
    return [build_evidence_packet(f) for f in data["findings"]]


def main():
    if len(sys.argv) < 2:
        print("usage: evidence_bridge.py <judgment_output.json>", file=sys.stderr)
        sys.exit(1)
    packets = build_all(sys.argv[1])
    print(json.dumps(packets, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
