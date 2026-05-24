from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import sys
import time
import uuid
from typing import Any

from loguru import logger
from pydantic import BaseModel

from app.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level.upper(),
        serialize=settings.log_json,
        backtrace=False,
        diagnose=False,
    )
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_path,
            level=settings.log_level.upper(),
            serialize=settings.log_json,
            mode="w",
            backtrace=False,
            diagnose=False,
        )


def log_json(event: str, **payload: Any) -> None:
    logger.bind(event=event, payload=serialize_for_log(payload)).info(event)


def build_request_context(method: str, path: str, client: str | None) -> dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "method": method,
        "path": path,
        "client": client or "",
        "started_at": time.perf_counter(),
    }


def serialize_for_log(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return serialize_for_log(value.model_dump())
    if isinstance(value, dict):
        return {str(key): serialize_for_log(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize_for_log(item) for item in value]
    if hasattr(value, "_mapping"):
        return serialize_for_log(dict(value._mapping))
    if hasattr(value, "keys") and hasattr(value, "__getitem__"):
        try:
            return serialize_for_log(dict(value))
        except Exception:
            return str(value)
    return str(value)


def text_preview(value: str, limit: int = 300) -> str:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized[:limit]


def text_hash(value: str) -> str:
    return sha256(str(value or "").encode("utf-8")).hexdigest()
