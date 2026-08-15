from __future__ import annotations

from datetime import datetime, timezone

import pytest

from desktop import official_feed


RSS = b"""<?xml version='1.0'?>
<rss><channel><item><title>Official update</title>
<description>&lt;b&gt;Safe summary&lt;/b&gt;</description>
<link>https://openai.com/news/example</link>
<pubDate>Sat, 15 Aug 2026 00:00:00 GMT</pubDate>
</item></channel></rss>"""


class _Headers:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _Response:
    def __init__(self, *, final_url: str, content_type: str, body: bytes = RSS) -> None:
        self._final_url = final_url
        self.headers = _Headers(content_type)
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self._final_url

    def read(self, _limit: int) -> bytes:
        return self._body


class _Opener:
    def __init__(self, response: _Response) -> None:
        self._response = response

    def open(self, *_args, **_kwargs) -> _Response:
        return self._response


def test_parser_strips_markup_and_rejects_future_dates() -> None:
    items = official_feed.parse_feed(
        RSS,
        publisher="OpenAI",
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    )
    assert [item.summary for item in items] == ["Safe summary"]

    future = RSS.replace(b"15 Aug 2026", b"18 Aug 2026")
    assert official_feed.parse_feed(
        future,
        publisher="OpenAI",
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
    ) == []


def test_parser_rejects_entity_expansion() -> None:
    hostile = b"<!DOCTYPE x [<!ENTITY x 'boom'>]><rss><item><title>&x;</title></item></rss>"
    with pytest.raises(Exception):
        official_feed.parse_feed(hostile, publisher="test")


@pytest.mark.parametrize(
    ("final_url", "content_type"),
    [
        ("https://example.com/feed.xml", "application/rss+xml"),
        (official_feed.FEED_URLS[0], "text/html"),
    ],
)
def test_refresh_keeps_last_good_on_redirect_or_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    final_url: str,
    content_type: str,
) -> None:
    monkeypatch.setattr(official_feed, "FEED_URLS", (official_feed.FEED_URLS[0],))
    response = _Response(final_url=final_url, content_type=content_type)
    monkeypatch.setattr(official_feed, "build_opener", lambda *_args: _Opener(response))
    service = official_feed.OfficialFeedService(root=tmp_path)
    assert service.refresh() == []
    assert not service.cache_file.exists()


def test_refresh_accepts_exact_allowlisted_xml(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    url = official_feed.FEED_URLS[0]
    monkeypatch.setattr(official_feed, "FEED_URLS", (url,))
    response = _Response(final_url=url, content_type="application/rss+xml")
    monkeypatch.setattr(official_feed, "build_opener", lambda *_args: _Opener(response))
    service = official_feed.OfficialFeedService(root=tmp_path)
    assert len(service.refresh()) == 1
    assert len(service.load_cached()) == 1
