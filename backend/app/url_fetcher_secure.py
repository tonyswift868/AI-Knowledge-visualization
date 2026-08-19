from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 2_000_000

_LOCAL_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
_PROXY_FAKE_IPV4 = ipaddress.ip_network("198.18.0.0/15")


def _loopback_proxy_for(scheme: str) -> bool:
    """Return whether the active environment proxy is explicitly loopback.

    Clash-compatible Windows setups may answer public DNS queries with RFC 2544
    benchmark addresses and route the actual HTTPS request through a loopback
    proxy.  Treating those synthetic answers as ordinary private DNS makes all
    selected web resources unusable.  We only accept that narrow case when the
    proxy endpoint itself is local; literal/private target URLs remain blocked.
    """

    keys = (
        ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
        if scheme == "https"
        else ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    )
    proxy = next((str(os.environ.get(key) or "").strip() for key in keys if os.environ.get(key)), "")
    if not proxy:
        return False
    parsed = urlparse(proxy)
    host = str(parsed.hostname or "").casefold().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _proxy_fake_dns(addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address], scheme: str) -> bool:
    """Recognize a proxy fake-DNS answer without allowing general private DNS."""

    if not _loopback_proxy_for(scheme):
        return False
    has_fake_v4 = any(address.version == 4 and address in _PROXY_FAKE_IPV4 for address in addresses)
    if not has_fake_v4:
        return False
    return all(
        (address.version == 4 and address in _PROXY_FAKE_IPV4)
        or (address.version == 6 and address.is_private)
        for address in addresses
    )

def validate_public_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only http/https URLs are allowed")
    host = parsed.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith(_LOCAL_HOST_SUFFIXES):
        raise ValueError("private, loopback, or reserved address is not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("private, loopback, or reserved address is not allowed")
        return parsed
    addresses = list({ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)})
    if not addresses:
        raise ValueError("hostname did not resolve")
    unsafe = [address for address in addresses if not address.is_global]
    if unsafe and not _proxy_fake_dns(addresses, parsed.scheme):
        raise ValueError("private, loopback, or reserved address is not allowed")
    return parsed

async def _bounded_content(response: httpx.Response) -> bytes:
    declared = int(response.headers.get("content-length", "0") or 0)
    if declared > MAX_BYTES:
        raise ValueError("response exceeds size limit")
    if hasattr(response, "aiter_bytes"):
        data = bytearray()
        async for part in response.aiter_bytes():
            data.extend(part)
            if len(data) > MAX_BYTES:
                raise ValueError("response exceeds size limit")
        return bytes(data)
    content = response.content
    if len(content) > MAX_BYTES:
        raise ValueError("response exceeds size limit")
    return content

async def fetch_safe(url: str):
    current = url
    async with httpx.AsyncClient(timeout=8, follow_redirects=False, headers={"User-Agent": "MentorFlow/2.0"}) as client:
        for _ in range(4):
            validate_public_url(current)
            if hasattr(client, "stream"):
                async with client.stream("GET", current) as response:
                    status = response.status_code
                    headers = response.headers
                    if status in {301, 302, 303, 307, 308}:
                        location = headers.get("location")
                        if not location: raise ValueError("redirect target is missing")
                        current = urljoin(current, location)
                        continue
                    if status >= 400: raise ValueError(f"page returned HTTP {status}")
                    content_type = headers.get("content-type", "").lower()
                    if "text/html" not in content_type and "text/plain" not in content_type: raise ValueError("content type is not text")
                    content = await _bounded_content(response)
            else:
                response = await client.get(current)
                status = response.status_code; headers = response.headers
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location: raise ValueError("redirect target is missing")
                    current = urljoin(current, location)
                    continue
                if status >= 400: raise ValueError(f"page returned HTTP {status}")
                content_type = headers.get("content-type", "").lower()
                if "text/html" not in content_type and "text/plain" not in content_type: raise ValueError("content type is not text")
                content = await _bounded_content(response)
            soup = BeautifulSoup(content, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]): tag.decompose()
            text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            if len(re.sub(r"\s+", "", text)) < 40: raise ValueError("正文为空或过短")
            return {"text": text, "title": soup.title.get_text(strip=True) if soup.title else current, "final_url": current, "fetched_at": datetime.now(timezone.utc).isoformat(), "content_type": content_type, "snapshot_hash": hashlib.sha256(text.encode()).hexdigest()}
    raise ValueError("too many redirects")
