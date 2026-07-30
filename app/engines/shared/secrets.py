""" NVIDIA API 키 접근자 -- 이 함수 하나만 tools/check_no_secrets.py의 grep 대상이 된다

D9 (2026-07-30): 실배포는 40 rpm 단일 키를 쓴다(사용자의 NvidiaKeyPool 로테이션은
로컬 테스트 편의용일 뿐, 실배포 코드에 들어가지 않는다 -- app/engines/shared/llm.py의
자체 docstring 참고). 키를 읽는 지점을 이 함수 하나로 좁혀두면, "이 키가 실제로
어디서 읽히는가"를 리뷰할 때 이 파일 하나만 보면 된다.
"""
from __future__ import annotations

from app.config import get_settings


def nvidia_api_key() -> str:
    """ .env/환경변수의 NVIDIA_API_KEY. 비어 있으면 즉시 RuntimeError --
    빈 키로 조용히 호출을 시도해 나중에야 401로 실패하는 것보다 낫다. """
    key = get_settings().nvidia_api_key
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY가 비어 있습니다. .env에 로컬 전용으로만 설정하세요 "
            "(D9 -- 절대 커밋하지 않음, tools/check_no_secrets.py가 강제)."
        )
    return key
