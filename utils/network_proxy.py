from __future__ import annotations

import re
import ssl
import urllib.request
from contextlib import contextmanager
from typing import Iterator

import certifi


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
    """Build an urllib opener with proxy support and a portable CA bundle.

    The launcher is distributed with its own Python runtime.  On a fresh
    Windows machine that runtime may not discover the operating system's CA
    path even though the browser can open the same HTTPS URL.  Start with the
    native default context (so installed Windows roots and enterprise proxy
    roots remain usable), then add the bundled certifi roots as a fallback.
    """
    normalized = normalize_proxy_url(value)
    proxy_handler = (
        urllib.request.ProxyHandler({"http": normalized, "https": normalized})
        if normalized
        else urllib.request.ProxyHandler()
    )
    handlers: list[urllib.request.BaseHandler] = [
        proxy_handler,
        urllib.request.HTTPSHandler(context=_build_ssl_context()),
    ]
    return urllib.request.build_opener(*handlers)


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        # load_verify_locations adds to the native trust store instead of
        # replacing it, so both bundled public CAs and Windows-installed
        # proxy/security roots remain available.
        context.load_verify_locations(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        # The native context is still preferable to disabling verification.
        pass
    return context


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
