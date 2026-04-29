from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import setup_logging


setup_logging()

app = FastAPI(title="Legal Claim Pipeline MVP", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router, prefix="/api/v1")
