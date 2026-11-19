from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_mirror460_action_publishes_atomically_and_keeps_two_versions():
    action = (ROOT / ".github/actions/mf-installer/action.yml").read_text(encoding="utf-8")

    assert "install -o root -g root -m 0644 \"$temp\" \"$releases/.MentorFlow-Setup-$version.exe.tmp\"" in action
    assert "mv -f \"$releases/.MentorFlow-Setup-$version.exe.tmp\" \"$releases/MentorFlow-Setup-$version.exe\"" in action
    assert "`n        mv -f" not in action
    assert 'mv -f "$releases/.current.json.tmp" "$releases/current.json"' in action
    assert "sort -V | head -n -2" in action
    # The login user is not root; root-owned files need the same sudo -n as other release steps.
    assert "base64 -d | sudo -n bash -s --" in action  # CRLF from a Windows pipe breaks bash
