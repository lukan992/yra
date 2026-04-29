from pydantic import BaseModel, Field


class ClaimAnalyzeRequest(BaseModel):
    user_text: str = Field(min_length=1)
