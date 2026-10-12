from __future__ import annotations

import re
import urllib.request
from contextlib import contextmanager
from typing import Iterator


class ProxyConfigError(ValueError):
    pass


def normalize_proxy_url(value: object) -> str:
    proxy = str(value or "").strip().rstrip("/")
    if not proxy:
        return ""
    if re.fullmatch(r"\d{1,5}", proxy):
        port = int(proxy)
        if not 1 <= port <= 65535:
            raise ProxyConfigError("代理端口必须在 1-65535 之间。")
        return f"http://127.0.0.1:{port}"
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    if not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
        raise ProxyConfigError("代理地址必须以 http://、https://、socks5:// 或 socks5h:// 开头。")
    return proxy


def proxy_status_text(value: object) -> str:
    normalized = normalize_proxy_url(value)
    return f"代理 {normalized}" if normalized else "直连或系统代理"


def build_urllib_opener(value: object) -> urllib.request.OpenerDirector:
    normalized = normalize_proxy_url(value)
    if not normalized:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": normalized, "https": normalized})
    )


@contextmanager
def temporary_proxy_environment(value: object) -> Iterator[str]:
    import os

    normalized = normalize_proxy_url(value)
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
    previous = {key: os.environ.get(key) for key in keys}
    try:
        if normalized:
            for key in keys:
                os.environ[key] = normalized
        yield normalized
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
