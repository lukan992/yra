from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.requests import ClaimAnalyzeRequest
from app.schemas.responses import ClaimAnalyzeResponse
from app.services.pipeline import ClaimPipeline


router = APIRouter()


@router.post("/claims/analyze", response_model=ClaimAnalyzeResponse)
def analyze_claim(payload: ClaimAnalyzeRequest, db: Session = Depends(get_db)) -> ClaimAnalyzeResponse:
    pipeline = ClaimPipeline(db)
    return pipeline.run(payload.user_text)
