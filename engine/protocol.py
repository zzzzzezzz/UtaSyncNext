from __future__ import annotations

from typing import Any


MSG_LOG = "log"
MSG_PROGRESS = "progress"
MSG_STATUS = "status"
MSG_DONE = "done"
MSG_ERROR = "error"


def log(text: str) -> dict[str, Any]:
    return {"type": MSG_LOG, "text": text}


def progress(value: float, text: str) -> dict[str, Any]:
    return {"type": MSG_PROGRESS, "progress": value, "text": text}


def status(text: str, value: float = 0.0) -> dict[str, Any]:
    return {"type": MSG_STATUS, "progress": value, "text": text}


def done(
    text: str,
    artifacts: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"type": MSG_DONE, "text": text}
    if artifacts:
        message["artifacts"] = artifacts
    if summary:
        message["summary"] = summary
    return message


def error(text: str) -> dict[str, Any]:
    return {"type": MSG_ERROR, "text": text}
