"""Exact-user Keycloak deletion using the existing admin resolver and TLS client."""
import asyncio
import json
import re
from .closure import ClosureError


class KeycloakClosureProvider:
    def __init__(self, admin):
        self.admin = admin

    async def _request(self, method, subject):
        if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject):
            raise ClosureError("CLOSURE_IDENTITY_INVALID", 403)
        headers, raw = {}, bytearray()
        try:
            async with asyncio.timeout(12):
                authority = await self.admin.resolve_token()
                if not isinstance(authority, str) or not authority or "\r" in authority or "\n" in authority:
                    raise ClosureError("CLOSURE_PROVIDER_UNAVAILABLE")
                headers.update(Authorization="Bearer " + authority, Accept="application/json")
                authority = None
                async with self.admin.http.stream(method, self.admin.base + "/users/" + subject,
                                                  headers=headers) as response:
                    status = response.status_code
                    if status not in ({200, 404} if method == "GET" else {204, 404}):
                        raise ClosureError("CLOSURE_PROVIDER_UNAVAILABLE")
                    if status in (200, 404):
                        if response.headers.get("content-type", "").split(";")[0] != "application/json":
                            raise ClosureError("CLOSURE_PROVIDER_RESPONSE_INVALID")
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 65536:
                                raise ClosureError("CLOSURE_PROVIDER_RESPONSE_INVALID")
                            raw.extend(chunk)
                        value = json.loads(raw)
                        if (not isinstance(value, dict)
                                or status == 200 and value.get("id") != subject
                                or status == 404 and value.get("error") != "User not found"):
                            raise ClosureError("CLOSURE_PROVIDER_RESPONSE_INVALID")
                    return status
        except ClosureError:
            raise
        except Exception:
            raise ClosureError("CLOSURE_PROVIDER_OUTCOME_UNKNOWN") from None
        finally:
            headers.clear()
            raw[:] = b"\0" * len(raw)

    async def ensure_deleted(self, subject):
        # The operation was prepared only after an exact-user GET and password
        # verification. A 404 here confirms the provider stage ONLY, never closure.
        if await self._request("GET", subject) == 404:
            return
        await self._request("DELETE", subject)
        if await self._request("GET", subject) != 404:
            raise ClosureError("CLOSURE_PROVIDER_DELETE_UNCONFIRMED")
