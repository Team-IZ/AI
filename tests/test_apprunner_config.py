""" 배포 설정(`apprunner.yaml`)이 코드 기본값과 어긋나지 않는지.

🔴 **App Runner의 env가 `app/config.py`의 기본값을 덮어쓴다.** 그래서 코드에서 모델을
바꿔도 이 파일을 같이 안 고치면 **배포본만 옛 모델을 계속 쓴다.** 로컬에서도 같은 클래스의
사고가 났다 — `.env`의 `MODEL_CODE_SESSION`이 옛 값으로 남아 있었다(2026-08-07).

같은 실수를 두 번 했으므로 여기서 고정한다. `apprunner.yaml`은 배포 파이프라인이 읽는
파일이라 아무도 실행해보지 않고, 틀려도 **배포가 성공한 채로 잘못된 모델을 쓴다** —
런타임 에러가 아니라 조용한 오작동이라 사람 눈으로는 거의 안 잡힌다.
"""
from pathlib import Path

import yaml

from app.config import Settings

APPRUNNER = Path(__file__).resolve().parent.parent / "apprunner.yaml"


def _env() -> dict[str, str]:
    spec = yaml.safe_load(APPRUNNER.read_text(encoding="utf-8"))
    return {item["name"]: item["value"] for item in spec["run"]["env"]}


def test_model_codes_match_the_code_defaults():
    """배포 env의 모델 코드가 config.py 기본값과 같아야 한다.

    고치는 법: 모델을 바꿨으면 `apprunner.yaml`의 해당 `MODEL_CODE_*`도 같이 고친다.
    ⚠️ 그리고 백엔드에 **사전 통보**해야 한다 — `ai_model.model_code`가 FK라
    등록 안 된 코드가 응답에 실리면 `ai_usage` INSERT가 통째로 실패한다.
    """
    env = _env()
    # _env_file=None -- 로컬 .env 를 무시하고 **코드 기본값**과 대조한다. .env 는 개발자
    # 개인 오버라이드라, 그게 섞이면 "내 로컬에선 통과"가 된다.
    defaults = Settings(_env_file=None)

    assert env["MODEL_CODE_ANALYSIS"] == defaults.model_code_analysis
    assert env["MODEL_CODE_SESSION"] == defaults.model_code_session
    assert env["MODEL_CODE_CURRICULUM"] == defaults.model_code_curriculum
    assert env["MODEL_CODE_INTERVIEW_BRIEF"] == defaults.model_code_interview_brief


def test_every_model_code_setting_is_pinned_in_the_deploy_env():
    """새 역할(모델)이 늘면 배포 env에도 반드시 추가되게 한다.

    위 테스트는 **이미 적힌 키만** 대조하므로, `config.py`에 `model_code_X`를 새로
    추가하고 `apprunner.yaml`을 안 고치면 그냥 지나간다 -- 그 역할만 배포본에서
    코드 기본값으로 떨어지고 아무도 모른다.
    """
    pinned = {k for k in _env() if k.startswith("MODEL_CODE_")}
    expected = {
        f"MODEL_CODE_{name.removeprefix('model_code_').upper()}"
        for name in Settings.model_fields
        if name.startswith("model_code_")
    }

    assert pinned == expected, f"apprunner.yaml에서 빠진 것: {expected - pinned}"


def test_production_runs_the_real_engine():
    """배포본이 stub으로 뜨면 백엔드는 200을 받는데 내용이 전부 가짜다.

    `engine_mode` 기본값이 `stub`이라(로컬 개발 기준) 배포 env가 이걸 안 덮으면
    조용히 가짜 응답을 낸다 -- 에러가 없어서 헬스체크도 통과한다.
    """
    env = _env()
    assert env["ENGINE_MODE"] == "real"
    assert env["APP_ENV"] == "production"
