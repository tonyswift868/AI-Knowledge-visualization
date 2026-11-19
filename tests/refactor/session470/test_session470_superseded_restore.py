"""BUG-297: a superseded refresh must remain a superseded refresh."""
import asyncio

import httpx
import pytest

from refactor.desktop.account_protocol import AccountConfig, AccountError
from refactor.desktop.native_auth import NativeCredentialClient


ISSUER = "https://identity.session470.invalid/realms/mentorflow"


def test_refresh_preserves_native_session_superseded_code():
    async def run():
        config = AccountConfig(
            ISSUER, "mentorflow-desktop", "mentorflow-api", "https://api.session470.invalid"
        )
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                401, json={"code": "NATIVE_SESSION_SUPERSEDED"}
            )
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = NativeCredentialClient(config, http_client=http)
            with pytest.raises(AccountError) as raised:
                await client._session_request("refresh", {"refresh_token": "synthetic"})
        assert raised.value.code == "NATIVE_SESSION_SUPERSEDED"

    asyncio.run(run())
