"""Lexical safety policy for user-configured Provider Base URLs.

This policy intentionally does *not* resolve DNS or reject private/loopback
addresses.  MentorFlow is local-first and must continue to support Ollama,
LM Studio, localhost, LAN and ``.local`` Provider endpoints.  The boundary
here is only URL syntax and schemes before a value is persisted or handed to
an HTTP client.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


_ALLOWED_SCHEMES = frozenset({"http", "https"})
_HEX = frozenset("0123456789abcdefABCDEF")
_HOSTNAME_SAFE = re.compile(r"^[A-Za-z0-9._~-]+$")


class ProviderURLPolicyError(ValueError):
    """A user-facing Provider Base URL policy violation.

    The error deliberately carries no copy of the rejected URL.  A Base URL
    may contain sensitive material in a user-info component, and validation
    failures must never echo it into API responses or logs.
    """

    code = "PROVIDER_BASE_URL_INVALID"
    status = 400
    message = (
        "Provider Base URL 无效；仅支持带主机名的 http/https URL，"
        "请检查地址、端口和路径"
    )

    def __init__(self, reason: str = "invalid provider base URL") -> None:
        super().__init__(reason)
        self.reason = reason


def _reject(reason: str) -> None:
    raise ProviderURLPolicyError(reason)


def _validate_percent_escapes(value: str) -> None:
    for index, character in enumerate(value):
        if character != "%":
            continue
        if index + 2 >= len(value) or value[index + 1] not in _HEX or value[index + 2] not in _HEX:
            _reject("malformed percent escape")


def _validate_port(parts) -> None:
    """Validate an authority port without accepting an empty/non-ASCII port."""

    netloc = parts.netloc
    if not netloc:
        _reject("missing authority")

    # User-info is forbidden before this helper is called.  Split the port
    # from a bracketed IPv6 host or an ordinary hostname explicitly so
    # ``example.test:`` does not silently become a URL without a port.
    if netloc.startswith("["):
        closing = netloc.find("]")
        if closing < 0:
            _reject("unterminated IPv6 host")
        remainder = netloc[closing + 1 :]
        if not remainder:
            return
        if not remainder.startswith(":"):
            _reject("invalid authority suffix")
        port_text = remainder[1:]
    else:
        if ":" not in netloc:
            return
        host_text, port_text = netloc.rsplit(":", 1)
        if not host_text or ":" in host_text:
            _reject("missing host")

    if not port_text or not port_text.isascii() or not port_text.isdigit():
        _reject("invalid port")
    port = int(port_text)
    if not 1 <= port <= 65535:
        _reject("port out of range")


def _validate_hostname(host: str) -> None:
    # urlsplit() has already rejected malformed bracketed IPv6 literals.  A
    # colon in the parsed hostname therefore denotes an IPv6 address (with an
    # optional RFC 6874 zone id), not an ordinary hostname separator.
    if ":" in host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            _reject("invalid IPv6 host")
        return

    if not host or host.startswith(".") or ".." in host:
        _reject("invalid hostname")
    try:
        # IDNA keeps ordinary internationalized hostnames usable while the
        # lexical check below rejects path delimiters and other authority
        # separators.  Underscores remain allowed for local/mDNS names.
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        _reject("invalid hostname encoding")
    if len(ascii_host.rstrip(".")) > 253 or not _HOSTNAME_SAFE.fullmatch(ascii_host):
        _reject("invalid hostname characters")
    if any(not label for label in ascii_host.rstrip(".").split(".")):
        _reject("empty hostname label")


def validate_provider_base_url(value: str) -> str:
    """Validate and return a Provider Base URL unchanged.

    Only lexical properties are checked.  In particular, this function does
    not perform DNS resolution and does not block localhost, loopback, LAN,
    private IPv6 or ``.local`` destinations.
    """

    if not isinstance(value, str) or not value:
        _reject("base URL must be a non-empty string")
    if value != value.strip():
        _reject("leading or trailing whitespace")
    if any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        _reject("whitespace or control character")
    _validate_percent_escapes(value)

    try:
        parts = urlsplit(value)
    except ValueError as exc:
        _reject("malformed URL authority")
        raise AssertionError("unreachable") from exc

    scheme = parts.scheme.casefold()
    if scheme not in _ALLOWED_SCHEMES:
        _reject("unsupported URL scheme")
    if not parts.netloc or not parts.hostname:
        _reject("missing URL hostname")
    # Provider sessions are surfaced through a compatibility route that
    # includes the base URL.  Reject the entire query component (including an
    # empty ``?``) so API keys and other query credentials can never be
    # persisted in a session and echoed back to the caller.
    if "?" in value or parts.query:
        _reject("URL queries are not accepted")
    # Treat an empty fragment marker (``...#``) the same as a non-empty
    # fragment; fragments are never sent to a Provider and are therefore
    # rejected rather than silently discarded.
    if "#" in value or parts.fragment:
        _reject("URL fragments are not accepted")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        _reject("URL user-info is not accepted")
    _validate_port(parts)
    _validate_hostname(parts.hostname)
    return value


__all__ = ["ProviderURLPolicyError", "validate_provider_base_url"]
