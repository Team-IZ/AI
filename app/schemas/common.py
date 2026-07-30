""" 모든 스키마의 공통 기반 """

from pydantic import BaseModel, ConfigDict

# snake_case를 camelCase로 별칭 변경
from pydantic.alias_generators import to_camel

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
