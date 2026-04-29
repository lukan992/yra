import json
import sys
from typing import Any

from loguru import logger


def setup_logging() -> None:
    logger.remove()
    logger.add(sys.stdout, serialize=True, backtrace=False, diagnose=False)


def log_json(event: str, **payload: Any) -> None:
    logger.bind(event=event).info(json.dumps(payload, ensure_ascii=False, default=str))
