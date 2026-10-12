from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from engine.model_downloader import (
    MODEL_REPOS,
    SOURCE_MIRROR,
    SOURCE_OFFICIAL,
    ModelDownloadError,
    _temporary_proxy_env,
    preflight_model_download,
)
from engine.model_registry import MODEL_ORDER


@dataclass(frozen=True)
class SourceCheckItem:
    model: str
    source: str
    repo_id: str | None
    status: str
    file_count: int = 0
    total_size: int | None = None
    endpoint: str | None = None
    detail: str = ""
    seconds: float = 0.0


def check_model_sources(
    *,
    models: Iterable[str] | None = None,
    sources: Iterable[str] = (SOURCE_OFFICIAL, SOURCE_MIRROR),
    proxy_url: str = "",
    require_mirror: bool = False,
    report_path: Path | None = None,
) -> dict[str, object]:
    """Probe model manifests without downloading model payloads.

    Official-source failures are errors. Mirror failures are warnings by
    default because a mirror can be unavailable for a particular network while
    the downloader can still fall back to the official Hub endpoint.
    """
    started = time.monotonic()
    selected_models = list(models or MODEL_ORDER)
    selected_sources = list(sources)
    items: list[SourceCheckItem] = []
    errors: list[str] = []
    warnings: list[str] = []

    stream_ok, stream_detail = _check_windowed_stream_guard()
    if not stream_ok:
        errors.append(stream_detail)

    for model_name in selected_models:
        repo_id = MODEL_REPOS.get(model_name)
        if not repo_id:
            detail = f"未找到模型映射：{model_name}"
            errors.append(detail)
            for source_name in selected_sources:
                items.append(SourceCheckItem(model_name, source_name, None, "ERROR", detail=detail))
            continue

        for source_name in selected_sources:
            item_started = time.monotonic()
            try:
                with tempfile.TemporaryDirectory(prefix="utasyncnext-source-check-") as temp_dir:
                    result = preflight_model_download(
                        model_name,
                        source_name=source_name,
                        proxy_url=proxy_url,
                        cache_dir=Path(temp_dir),
                    )
                item = SourceCheckItem(
                    model=model_name,
                    source=source_name,
                    repo_id=result.repo_id,
                    status="PASS",
                    file_count=result.file_count,
                    total_size=result.total_size,
                    endpoint=result.endpoint,
                    detail="文件清单可访问",
                    seconds=round(time.monotonic() - item_started, 2),
                )
            except Exception as exc:
                detail = _compact_exception(exc)
                status = "WARN" if source_name == SOURCE_MIRROR and not require_mirror else "ERROR"
                item = SourceCheckItem(
                    model=model_name,
                    source=source_name,
                    repo_id=repo_id,
                    status=status,
                    detail=detail,
                    seconds=round(time.monotonic() - item_started, 2),
                )
                (warnings if status == "WARN" else errors).append(f"{model_name} / {source_name}: {detail}")
            items.append(item)

    payload: dict[str, object] = {
        "ok": not errors,
        "stream_guard": {"ok": stream_ok, "detail": stream_detail},
        "models": selected_models,
        "sources": selected_sources,
        "proxy": proxy_url or "",
        "errors": errors,
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 2),
        "results": [asdict(item) for item in items],
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def format_source_check_report(payload: dict[str, object]) -> str:
    lines = ["模型源巡检结果：" + ("通过" if payload.get("ok") else "发现错误")]
    stream_guard = payload.get("stream_guard") or {}
    lines.append(f"窗口化下载器：{'正常' if stream_guard.get('ok') else '异常'}")
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        size = item.get("total_size")
        size_text = f"，约 {size / 1024 / 1024:.1f}MB" if isinstance(size, int) else ""
        lines.append(
            f"[{item.get('status')}] {item.get('model')} / {item.get('source')}"
            f"：{item.get('detail', '')}{size_text}"
        )
    for label in ("errors", "warnings"):
        for message in payload.get(label) or []:
            lines.append(f"{label}: {message}")
    return "\n".join(lines)


def _check_windowed_stream_guard() -> tuple[bool, str]:
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None  # type: ignore[assignment]
        sys.stderr = None  # type: ignore[assignment]
        with _temporary_proxy_env(None):
            if sys.stdout is None or sys.stderr is None:
                return False, "窗口化进程的 stdout/stderr 保护未生效"
        return True, "stdout/stderr 空值保护正常"
    except Exception as exc:
        return False, f"窗口化输出流保护失败：{_compact_exception(exc)}"
    finally:
        sys.stdout = previous_stdout
        sys.stderr = previous_stderr


def _compact_exception(exc: Exception) -> str:
    if isinstance(exc, ModelDownloadError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
