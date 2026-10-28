"""Avatar routes stay bound to the Bearer account under forged selectors (229)."""
import pytest

pytest_plugins = ["support_profile229"]

from support_profile229 import new_account229, png229  # noqa: E402


@pytest.mark.parametrize("backend", ["python", "java"])
def test_avatar_account_workspace_selectors_cannot_access_other_profile229(profile_env229, backend):
    """This API is a current-user route: forged IDs cannot select B's image."""
    env = profile_env229
    client = env["clients"][backend]
    account_a = new_account229(env, f"avatar-selector-a-{backend}")
    account_b = new_account229(env, f"avatar-selector-b-{backend}")
    profile_url = "/api/enterprise/account/profile"
    avatar_url = profile_url + "/avatar"

    # Establish distinct, real avatars so an unauthorized cross-account read is
    # detectable from both the bytes and the content ETag.
    initial_b = client.get(profile_url, headers=account_b["headers"])
    assert initial_b.status_code == 200 and initial_b.json()["version"] == 1
    saved_b = client.put(
        avatar_url,
        headers={**account_b["headers"], "If-Match": '"profile-1"', "Content-Type": "image/png"},
        content=png229(color="blue", size=(32, 32)),
    )
    assert saved_b.status_code == 200, saved_b.text
    before_b_profile = client.get(profile_url, headers=account_b["headers"])
    before_b_avatar = client.get(avatar_url, headers=account_b["headers"])
    assert before_b_profile.status_code == before_b_avatar.status_code == 200
    b_bytes = before_b_avatar.content
    b_etag = before_b_avatar.headers["etag"]
    b_profile = before_b_profile.json()
    assert b_profile["avatar"]["etag"] == b_etag

    initial_a = client.get(profile_url, headers=account_a["headers"])
    assert initial_a.status_code == 200 and initial_a.json()["version"] == 1
    saved_a = client.put(
        avatar_url,
        headers={**account_a["headers"], "If-Match": '"profile-1"', "Content-Type": "image/png"},
        content=png229(color="red", size=(32, 32)),
    )
    assert saved_a.status_code == 200, saved_a.text
    a_etag = saved_a.json()["avatar"]["etag"]
    a_version = saved_a.json()["version"]
    assert a_etag != b_etag

    # Missing bearer is rejected at every avatar operation.
    assert client.get(avatar_url).status_code == 401
    assert client.put(avatar_url, content=png229(), headers={"If-Match": '"profile-1"',
                      "Content-Type": "image/png"}).status_code == 401
    assert client.delete(avatar_url, headers={"If-Match": '"profile-1"'}).status_code == 401

    # The route has no account-ID selector. Try account-ID and workspace
    # selectors separately; ownership must still come from A's signed bearer.
    # The authorized operation can update A, but B's exact avatar bytes,
    # profile version, and ETag must remain unchanged after every request.
    selector_cases = (
        (
            "account",
            {"X-Account-ID": account_b["account_id"],
             "X-Account-Subject": account_b["subject"],
             "X-Account-Scope": account_b["scope"]},
            {"account_id": account_b["account_id"]},
        ),
        (
            "workspace",
            {"X-Workspace-ID": "workspace-for-" + account_b["subject"]},
            {"workspace_id": "workspace-b"},
        ),
    )
    for index, (selector_name, selector_headers, selector_query) in enumerate(selector_cases):
        # The previous selector scenario deletes A's avatar. Restore a fresh
        # one before testing the next selector so its GET proves the result is
        # A's current image rather than relying on a stale ETag.
        if index:
            restored_a = client.put(
                avatar_url,
                headers={**account_a["headers"], "If-Match": f'"profile-{a_version}"',
                         "Content-Type": "image/png"},
                content=png229(color="orange", size=(32, 32)),
            )
            assert restored_a.status_code == 200, restored_a.text
            a_version = restored_a.json()["version"]
            a_etag = restored_a.json()["avatar"]["etag"]
            assert a_etag != b_etag

        read = client.get(
            avatar_url,
            headers={**account_a["headers"], **selector_headers, "If-None-Match": b_etag},
            params=selector_query,
        )
        assert read.status_code == 200, (selector_name, read.text)
        assert read.headers["etag"] == a_etag and read.content != b_bytes
        assert read.status_code != 304

        updated_a = client.put(
            avatar_url,
            headers={**account_a["headers"], **selector_headers,
                     "If-Match": f'"profile-{a_version}"', "Content-Type": "image/png"},
            params=selector_query,
            content=png229(color="green" if index == 0 else "yellow", size=(32, 32)),
        )
        assert updated_a.status_code == 200, (selector_name, updated_a.text)
        assert updated_a.json()["version"] == a_version + 1
        a_version = updated_a.json()["version"]
        a_etag = updated_a.json()["avatar"]["etag"]
        assert_b_unchanged(client, account_b, profile_url, avatar_url, b_profile, b_bytes, b_etag)

        deleted_a = client.delete(
            avatar_url,
            headers={**account_a["headers"], **selector_headers, "If-Match": f'"profile-{a_version}"'},
            params=selector_query,
        )
        assert deleted_a.status_code == 200, (selector_name, deleted_a.text)
        assert deleted_a.json()["version"] == a_version + 1
        a_version = deleted_a.json()["version"]
        assert deleted_a.json()["avatar"] is None
        assert_b_unchanged(client, account_b, profile_url, avatar_url, b_profile, b_bytes, b_etag)

    # A's version must not be a cache hit for B, even when explicitly sent.
    cross_etag = client.get(avatar_url, headers={**account_b["headers"], "If-None-Match": a_etag})
    assert cross_etag.status_code == 200, cross_etag.text
    assert cross_etag.content == b_bytes and cross_etag.headers["etag"] == b_etag


def assert_b_unchanged(client, actor, profile_url, avatar_url, expected_profile, expected_bytes, expected_etag):
    profile = client.get(profile_url, headers=actor["headers"])
    avatar = client.get(avatar_url, headers=actor["headers"])
    assert profile.status_code == avatar.status_code == 200
    assert profile.json() == expected_profile
    assert avatar.content == expected_bytes
    assert avatar.headers["etag"] == expected_etag
    assert client.get(avatar_url, headers={**actor["headers"], "If-None-Match": expected_etag}).status_code == 304
