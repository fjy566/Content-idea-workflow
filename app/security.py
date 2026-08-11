from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.com",
}


def validate_public_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        raise ValueError("只允许 http 或 https 地址")
    if parts.username or parts.password:
        raise ValueError("地址不能包含用户名或密码")
    hostname = (parts.hostname or "").lower().rstrip(".")
    if not hostname or hostname in BLOCKED_HOSTS or hostname.endswith(".local"):
        raise ValueError("地址必须指向公开主机")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and (literal_ip.is_private or literal_ip.is_loopback or literal_ip.is_link_local or literal_ip.is_reserved or literal_ip.is_unspecified):
        raise ValueError("不允许访问本机、内网或元数据地址")
    # DNS resolution is intentionally opt-in. Some corporate, proxy, and
    # privacy DNS setups return synthetic private ranges for public hosts.
    # Enable STRICT_SSRF_DNS=1 when the local network requires DNS rebinding
    # protection and its resolver returns routable public addresses.
    if os.getenv("STRICT_SSRF_DNS", "0") == "1":
        try:
            addresses = socket.getaddrinfo(hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("无法解析数据源主机名") from exc
        for address in addresses:
            resolved = ipaddress.ip_address(address[4][0])
            if resolved.is_private or resolved.is_loopback or resolved.is_link_local or resolved.is_reserved or resolved.is_unspecified:
                raise ValueError("数据源解析到了本机或内网地址")
    return url.strip()
