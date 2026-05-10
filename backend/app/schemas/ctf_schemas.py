from pydantic import BaseModel

class CtfSubmitRequest(BaseModel):
    flag_value: str
