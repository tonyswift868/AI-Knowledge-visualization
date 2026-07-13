from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 2_000_000

def validate_public_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only http/https URLs are allowed")
    for info in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
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
