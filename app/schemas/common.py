""" 모든 스키마의 공통 기반 """

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# snake_case를 camelCase로 별칭 변경
from pydantic.alias_generators import to_camel

# Spring이 발급하는 PK가 실리는 자리. **검증하지 않고 문서에만 표시한다.**
#
# 백엔드 요청(2026-08-05)이 "type: string, format: uuid로 표시"였고 검증까지는
# 아니었다. 실제로 강제하면 안 되는 이유가 있다 — AI는 이 값의 주인이 아니라
# 받은 것을 그대로 되돌리는 쪽이라(teachId·requirementId는 순수 에코다),
# 여기서 UUID를 강제하면 **백엔드가 무엇을 보내든 422로 막는 관문**이 하나 생긴다.
# 형식 위반을 잡을 자리는 값을 만드는 쪽이지 반향하는 쪽이 아니다.
UuidStr = Annotated[str, Field(json_schema_extra={"format": "uuid"})]


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        # 별칭(questionBudget)뿐 아니라 원래 이름(question_budget)으로도 받아준다.
        # 테스트나 파이썬 코드에서 직접 만들 때 편하다.
        populate_by_name=True
    )

class ErrorResponse(BaseSchema):
    # 에러 스키마로 실제 에러는 api/error.py 의 핸들러가 만듬. swagger 문서용
    # error: 기계분기 코드, 예: INVALID_REQUEST
    # retryable: 스프링이 재시도할지 판단하는 부분
    error: str
    message: str
    retryable: bool = False