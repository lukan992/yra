import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ClaimRequest, GeneratedClaim, PipelineRun, PipelineStep


class ClaimRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_request(self, user_text: str) -> ClaimRequest:
        request = ClaimRequest(user_text=user_text)
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        return request

    def create_run(self, request_id: uuid.UUID, status: str = "received") -> PipelineRun:
        run = PipelineRun(request_id=request_id, status=status)
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def update_run(
        self,
        run: PipelineRun,
        status: str,
        case_type: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PipelineRun:
        run.status = status
        if case_type is not None:
            run.case_type = case_type
        run.error_code = error_code
        run.error_message = error_message
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def create_step(
        self,
        run_id: uuid.UUID,
        step_name: str,
        status: str,
        input_json: dict[str, Any] | None = None,
        output_json: Any | None = None,
        error_json: dict[str, Any] | None = None,
    ) -> PipelineStep:
        step = PipelineStep(
            run_id=run_id,
            step_name=step_name,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_json=error_json,
        )
        self.db.add(step)
        self.db.commit()
        self.db.refresh(step)
        return step

    def create_generated_claim(
        self,
        request_id: uuid.UUID,
        run_id: uuid.UUID,
        status: str,
        claim_json: dict[str, Any],
        validation_json: dict[str, Any] | None,
        used_laws_json: Any | None,
    ) -> GeneratedClaim:
        generated_claim = GeneratedClaim(
            request_id=request_id,
            run_id=run_id,
            status=status,
            claim_json=claim_json,
            validation_json=validation_json,
            used_laws_json=used_laws_json,
        )
        self.db.add(generated_claim)
        self.db.commit()
        self.db.refresh(generated_claim)
        return generated_claim
