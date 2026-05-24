import time

from fastapi import FastAPI
from fastapi import Request

from app.api.routes import router
from app.core.logging import build_request_context, log_json, setup_logging
from loguru import logger


setup_logging()

app = FastAPI(title="Legal Claim Pipeline MVP", version="0.1.0")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    context = build_request_context(
        method=request.method,
        path=request.url.path,
        client=request.client.host if request.client else None,
    )
    request.state.request_id = context["request_id"]
    with logger.contextualize(
        request_id=context["request_id"],
        method=context["method"],
        path=context["path"],
        client=context["client"],
    ):
        log_json(
            "http_request_started",
            request_id=context["request_id"],
            method=context["method"],
            path=context["path"],
            client=context["client"],
            query=str(request.url.query or ""),
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - context["started_at"]) * 1000, 2)
            log_json(
                "http_request_failed",
                request_id=context["request_id"],
                method=context["method"],
                path=context["path"],
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise

        duration_ms = round((time.perf_counter() - context["started_at"]) * 1000, 2)
        response.headers["X-Request-ID"] = context["request_id"]
        log_json(
            "http_request_finished",
            request_id=context["request_id"],
            method=context["method"],
            path=context["path"],
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router, prefix="/api/v1")
