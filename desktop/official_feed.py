"""Opt-in, allowlisted official update feeds for the desktop overview."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

FEED_URLS = (
    "https://openai.com/news/rss.xml",
    "https://blog.google/rss/",
    "https://huggingface.co/blog/feed.xml",
    "https://www.nist.gov/news-events/news/rss.xml",
    "https://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=50",
)
_ALLOWLIST = frozenset(FEED_URLS)
ALLOWED_FEED_URLS = _ALLOWLIST
MAX_BYTES = 1_500_000
MAX_ITEMS_PER_FEED = 40
MAX_SUMMARY_CHARS = 420
DEFAULT_DAYS = 90
_XML_CONTENT_TYPES = (
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
)


@dataclass(frozen=True)
class OfficialUpdate:
    title: str
    summary: str
    publisher: str
    published: str
    link: str


def is_allowed_feed_url(url: str) -> bool:
    """Require HTTPS and an exact URL from the small research allowlist."""

    return isinstance(url, str) and url in _ALLOWLIST and urlparse(url).scheme == "https"


def _strip_markup(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]*>", " ", value)
    return " ".join(value.split())[:MAX_SUMMARY_CHARS]


def _child_text(node: ElementTree.Element, *names: str) -> str:
    for child in list(node):
        tag = child.tag.rsplit("}", 1)[-1].casefold()
        if tag in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _link(node: ElementTree.Element) -> str:
    for child in list(node):
        tag = child.tag.rsplit("}", 1)[-1].casefold()
        if tag == "link":
            href = child.attrib.get("href", "").strip()
            value = href or (child.text or "").strip()
            if value.startswith("https://"):
                return value
    return ""


def parse_feed(xml: bytes | str, *, publisher: str, now: datetime | None = None, days: int = DEFAULT_DAYS) -> list[OfficialUpdate]:
    """Parse bounded RSS or Atom XML into safe, provenance-carrying items."""

    raw = xml.encode("utf-8") if isinstance(xml, str) else bytes(xml)
    if len(raw) > MAX_BYTES:
        raise ValueError("official feed exceeds size limit")
    lowered = raw[: min(len(raw), 8192)].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("official feed contains a prohibited document type or entity")
    root = ElementTree.fromstring(raw)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cutoff = reference.astimezone(timezone.utc) - timedelta(days=max(1, days))
    records: list[OfficialUpdate] = []
    seen: set[str] = set()
    for node in root.iter():
        kind = node.tag.rsplit("}", 1)[-1].casefold()
        if kind not in {"item", "entry"}:
            continue
        title = _strip_markup(_child_text(node, "title"))
        summary = _strip_markup(_child_text(node, "description", "summary", "content"))
        link = _link(node)
        date_text = _child_text(node, "pubdate", "published", "updated", "date")
        published_at = _parse_date(date_text)
        if (
            not title
            or not link
            or not published_at
            or published_at < cutoff
            or published_at > reference.astimezone(timezone.utc) + timedelta(minutes=5)
        ):
            continue
        key = link.casefold() or title.casefold()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            OfficialUpdate(
                title=title,
                summary=summary,
                publisher=_strip_markup(publisher) or "官方来源",
                published=published_at.date().isoformat(),
                link=link,
            )
        )
        if len(records) >= MAX_ITEMS_PER_FEED:
            break
    return records


def cache_root() -> Path:
    configured = os.getenv("MENTORFLOW_USER_DATA_ROOT", "").strip() or os.getenv("MENTORFLOW_DATA_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else Path(r"E:\MentorFlowData")
    return root / "cache"


def cache_path(root: str | os.PathLike[str] | None = None) -> Path:
    return (Path(root).expanduser() if root else cache_root()) / "official_updates.v1.json"


def _safe_records(payload: Any) -> list[OfficialUpdate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return []
    result: list[OfficialUpdate] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        values = {key: str(item.get(key) or "").strip() for key in ("title", "summary", "publisher", "published", "link")}
        if values["title"] and values["publisher"] and values["published"] and values["link"].startswith("https://"):
            result.append(OfficialUpdate(**values))
    return result


class OfficialFeedService:
    """Fetch official feeds only after an explicit caller opt-in."""

    def __init__(self, *, root: str | os.PathLike[str] | None = None, timeout: float = 8.0, days: int = DEFAULT_DAYS) -> None:
        self.cache_file = cache_path(root)
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.days = max(1, min(int(days), 365))
        self.last_status = "未启用在线更新。"

    def load_cached(self) -> list[OfficialUpdate]:
        try:
            return _safe_records(json.loads(self.cache_file.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            return []

    def _write_cache(self, items: list[OfficialUpdate]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "mentorflow.official-updates.v1", "saved_at": datetime.now(timezone.utc).isoformat(), "items": [asdict(item) for item in items]}
        fd, temporary = tempfile.mkstemp(prefix="official-updates-", suffix=".tmp", dir=str(self.cache_file.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(self.cache_file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def refresh(self) -> list[OfficialUpdate]:
        """Fetch all allowlisted feeds, retaining last-good data on failure."""

        all_items: list[OfficialUpdate] = []
        failures = 0
        for url in FEED_URLS:
            if not is_allowed_feed_url(url):
                continue
            parsed = urlparse(url)
            publisher = parsed.netloc
            try:
                request = Request(url, headers={"User-Agent": "MentorFlow official-updates/1"})
                # Reject redirects before the client makes a request to their
                # destination.  Checking ``response.geturl()`` after urllib's
                # default redirect handler would be too late for the network
                # allowlist guarantee.
                opener = build_opener(_RejectRedirects())
                with opener.open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    if final_url != url or not is_allowed_feed_url(final_url):
                        raise ValueError("official feed redirected outside its exact allowlisted URL")
                    content_type = str(response.headers.get_content_type() or "").casefold()
                    if content_type not in _XML_CONTENT_TYPES:
                        raise ValueError("official feed returned a non-XML content type")
                    body = response.read(MAX_BYTES + 1)
                all_items.extend(parse_feed(body, publisher=publisher, days=self.days))
            except Exception:  # network/parser failures retain last-good cache
                failures += 1
        deduped: dict[str, OfficialUpdate] = {}
        for item in all_items:
            deduped.setdefault(item.link.casefold() or item.title.casefold(), item)
        result = sorted(deduped.values(), key=lambda item: item.published, reverse=True)
        if result:
            try:
                self._write_cache(result)
            except OSError:
                pass
            self.last_status = f"已加载 {len(result)} 条官方更新；来源与日期均可查看。"
            return result
        cached = self.load_cached()
        self.last_status = "在线更新暂不可用，已显示最近一次成功缓存。" if cached else "在线更新暂不可用，暂无缓存。"
        return cached


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise HTTPError(req.full_url, code, "official feed redirects are disabled", headers, fp)


__all__ = [
    "ALLOWED_FEED_URLS",
    "FEED_URLS",
    "OfficialFeedService",
    "OfficialUpdate",
    "cache_path",
    "is_allowed_feed_url",
    "is_allowed_url",
    "parse_feed",
    "parse_official_feed",
]

# Short aliases keep the pure parser/allowlist API easy to discover in tests
# and downstream desktop integrations.
is_allowed_url = is_allowed_feed_url
parse_official_feed = parse_feed
