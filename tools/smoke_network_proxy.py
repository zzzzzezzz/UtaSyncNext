from __future__ import annotations

import sys
import urllib.request
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from engine.model_downloader import _normalize_proxy_url as normalize_download_proxy  # noqa: E402
from engine.pipeline_worker import build_pipeline_preview  # noqa: E402
from ui import engine_manager_support  # noqa: E402
from utils.config import _normalize_settings  # noqa: E402
from utils.network_proxy import ProxyConfigError, build_urllib_opener, normalize_proxy_url  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class EntryStub:
    def __init__(self) -> None:
        self.value = "old"

    def delete(self, _start, _end) -> None:
        self.value = ""

    def insert(self, _index, value: str) -> None:
        self.value = value


def main() -> int:
    _require(normalize_proxy_url("") == "", "空代理应保留为直连")
    _require(normalize_proxy_url("10808") == "http://127.0.0.1:10808", "端口简写解析错误")
    _require(
        normalize_proxy_url("127.0.0.1:10808") == "http://127.0.0.1:10808",
        "主机端口代理解析错误",
    )
    _require(
        normalize_download_proxy("10808") == "http://127.0.0.1:10808",
        "模型下载没有复用统一代理规则",
    )

    try:
        normalize_proxy_url("99999")
    except ProxyConfigError:
        pass
    else:
        raise AssertionError("非法代理端口没有被拒绝")

    opener = build_urllib_opener("10808")
    proxy_handlers = [handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)]
    _require(bool(proxy_handlers), "AI 配色网络请求没有安装代理处理器")
    _require(
        proxy_handlers[0].proxies.get("https") == "http://127.0.0.1:10808",
        "AI 配色没有使用统一代理地址",
    )

    migrated = _normalize_settings({
        "audio_type": "Live",
        "output_mode": "Bilingual",
        "use_demucs": "Skip",
        "model_size": "large-v2",
        "proxy_url": "",
        "model_download_proxy": "10808",
    })
    _require(migrated["proxy_url"] == "10808", "旧模型代理没有迁移为全局代理")
    _require(migrated["model_download_proxy"] == "10808", "全局代理没有回写模型下载配置")

    entry = EntryStub()
    parent = type("Parent", (), {})()
    parent.settings = {}
    parent.sidebar = type("Sidebar", (), {})()
    parent.sidebar.api_settings_panel = type("ApiPanel", (), {"proxy_entry": entry})()
    original_save = engine_manager_support.save_settings
    engine_manager_support.save_settings = lambda _settings: None
    try:
        engine_manager_support.persist_model_proxy(parent, "10808")
    finally:
        engine_manager_support.save_settings = original_save
    _require(parent.settings["proxy_url"] == "10808", "模型管理代理没有同步到全局设置")
    _require(entry.value == "10808", "模型管理代理没有同步到 API 设置面板")

    invalid_preview = build_pipeline_preview({
        "media_path": __file__,
        "settings": {
            "api_key": "fake-key",
            "proxy_url": "99999",
        },
    })
    _require(not invalid_preview.get("ok"), "非法代理预检不应允许启动")
    _require(
        any("代理格式有误" in str(line) for line in invalid_preview.get("lines", [])),
        "非法代理预检应给出普通用户可读提示",
    )

    print("PASS: one proxy setting covers translation, AI color, and model downloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
