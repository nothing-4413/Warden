"""出站 HTTP 的小工具：指向本机的请求绕开系统代理。

Windows 上 `httpx` 默认 `trust_env=True`，会去读注册表 Internet Settings 里的代理；
实测本机 Clash 类代理（127.0.0.1:7897）对 **127.0.0.1 目标也返回 502 且 body 为空**，
被代理的 llama-server / Ollama 日志里根本看不到这条请求，排查方向极易跑偏。

所以：目标是本机 → 显式 `trust_env=False`；目标是外网 → 保持 httpx 默认行为，
企业代理、`SSL_CERT_FILE` 这类环境配置照常生效。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# 常见写法里不等于 IP 但明确指本机的主机名
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def is_loopback_url(url: str) -> bool:
    """URL 的主机是不是本机（localhost 或回环 IP）；解析不出主机则按非本机处理。"""
    host = urlparse(url).hostname
    if not host:
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def httpx_env_kwargs(url: str) -> dict[str, bool]:
    """展开到 httpx 调用里的关键字：本机目标不读环境代理。"""
    return {"trust_env": False} if is_loopback_url(url) else {}
