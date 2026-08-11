from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx


class OpenSearchError(RuntimeError):
    pass


class OpenSearchUnsupported(OpenSearchError):
    pass


def _safe_result_url(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.casefold().strip(".")
    if (
        host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith(".local")
        or host.endswith(".internal")
    ):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
    ):
        return ""
    return candidate


def _normalize_results(
    items: list[dict[str, Any]],
    *,
    engine: str,
    limit: int,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        url = _safe_result_url(item.get("url") or item.get("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        title = str(item.get("title") or urlparse(url).hostname or "搜索结果").strip()
        snippet = str(
            item.get("content") or item.get("body") or item.get("snippet") or ""
        ).strip()
        output.append(
            {
                "url": url,
                "title": title[:240],
                "snippet": snippet[:800],
                "engine": engine,
            }
        )
        if len(output) >= limit:
            break
    return output


async def _search_searxng(
    endpoint: str,
    query: str,
    limit: int,
) -> list[dict[str, str]]:
    url = endpoint.rstrip("/") + "/search"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(
                url,
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": "all",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OpenSearchError(f"SearXNG 搜索失败：{exc}") from exc
    return _normalize_results(
        list(payload.get("results") or []),
        engine="searxng",
        limit=limit,
    )


def _search_ddgs_sync(
    query: str,
    limit: int,
    backends: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise OpenSearchError(
            "未安装开源搜索依赖 ddgs；请重新安装 backend/requirements.txt。"
        ) from exc
    errors: list[str] = []
    for backend in backends:
        for attempt in range(2):
            try:
                raw = list(
                    DDGS().text(
                        query,
                        max_results=limit,
                        backend=backend,
                    )
                )
            except Exception as exc:  # backend-specific network/search errors
                errors.append(
                    f"{backend} 尝试 {attempt + 1}: {type(exc).__name__}"
                )
                if attempt == 0:
                    time.sleep(0.4)
                continue
            results = _normalize_results(raw, engine=backend, limit=limit)
            if results:
                return results, errors
            errors.append(f"{backend} 尝试 {attempt + 1}: 无结果")
            if attempt == 0:
                time.sleep(0.4)
    return [], errors


async def search_web(
    query: str,
    *,
    max_results: int = 6,
) -> dict[str, Any]:
    """Run an actual metasearch query and return only public HTTPS results."""
    normalized_query = " ".join(str(query or "").split())[:360]
    if not normalized_query:
        raise OpenSearchError("搜索查询不能为空。")
    limit = max(1, min(int(max_results), 8))
    searxng_endpoint = str(os.getenv("MENTORFLOW_SEARXNG_URL") or "").strip()
    if searxng_endpoint:
        results = await _search_searxng(
            searxng_endpoint,
            normalized_query,
            limit,
        )
        if not results:
            raise OpenSearchError("SearXNG 没有返回可用的 HTTPS 搜索结果。")
        return {
            "query": normalized_query,
            "results": results,
            "engine": "searxng",
            "errors": [],
        }

    configured = str(
        os.getenv("MENTORFLOW_DDGS_BACKENDS") or "bing,duckduckgo"
    )
    backends = [
        item.strip().casefold()
        for item in configured.split(",")
        if item.strip()
    ] or ["bing", "duckduckgo"]
    results, errors = await asyncio.to_thread(
        _search_ddgs_sync,
        normalized_query,
        limit,
        backends,
    )
    if not results:
        detail = "；".join(errors[:4]) or "无可用搜索后端"
        raise OpenSearchError(f"开源元搜索没有返回可用结果（{detail}）。")
    return {
        "query": normalized_query,
        "results": results,
        "engine": results[0]["engine"],
        "errors": errors,
    }
