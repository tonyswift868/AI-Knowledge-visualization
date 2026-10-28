"""Real Java/PostgreSQL first-profile insert race regression (229)."""
from concurrent.futures import ThreadPoolExecutor
import threading

import psycopg
import pytest

pytest_plugins = ["support_profile229"]


def test_java_first_profile_save_has_one_winner_and_one_conflict229(profile_env229):
    """Two simultaneous version-1 saves for an absent row must not both win."""
    env = profile_env229
    client = env["clients"]["java"]
    actor = new_account229(env, "java-first-save")
    url = "/api/enterprise/account/profile"

    # Reading an absent profile returns its logical version without inserting it.
    initial = client.get(url, headers=actor["headers"])
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"nickname": "", "signature": "", "version": 1, "avatar": None}
    with psycopg.connect(env["pg_admin"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM public.account_profiles WHERE account_scope=%s",
            (actor["scope"],),
        ).fetchone()[0] == 0

    barrier = threading.Barrier(2)
    payloads = (
        {"nickname": "first-writer-a", "signature": "A"},
        {"nickname": "first-writer-b", "signature": "B"},
    )

    def save(payload):
        barrier.wait(timeout=10)
        return client.patch(
            url,
            headers={**actor["headers"], "If-Match": '"profile-1"'},
            json=payload,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, payloads))

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 412], [(response.status_code, response.text) for response in responses]
    winner = next(response for response in responses if response.status_code == 200)
    reread = client.get(url, headers=actor["headers"])
    assert reread.status_code == 200, reread.text
    assert reread.json() == winner.json()
    assert reread.json()["version"] == 2

    with psycopg.connect(env["pg_admin"]) as conn:
        rows = conn.execute(
            "SELECT nickname,signature,version FROM public.account_profiles WHERE account_scope=%s",
            (actor["scope"],),
        ).fetchall()
    assert rows == [(winner.json()["nickname"], winner.json()["signature"], 2)]


# Imported after plugin registration so this file works with pytest's per-directory
# plugin loading while support_profile229 remains an ordinary test-only helper.
from support_profile229 import new_account229  # noqa: E402
