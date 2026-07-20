import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tier_b_hook import confirmed_keys  # noqa: E402


def apply_tier_b_suppression(flagged_files):
    """(trigger, matched_text)가 confirmed 오탐이면 그 히트만 제거한다.

    파일에 다른(억제 안 된) 트리거 히트가 남아있으면 그 파일은 여전히 flagged로 유지된다 —
    "파일 전체를 안 본다"가 아니라 "이 특정 매치가 오탐이라는 것만 기억한다"는 세밀한 억제.
    """
    confirmed = confirmed_keys()
    filtered = {}
    for fname, hits in flagged_files.items():
        kept = [h for h in hits if (h["trigger"], h["matched_text"]) not in confirmed]
        if kept:
            filtered[fname] = kept
    return filtered
