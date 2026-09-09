"""Final HTTPS mount, installed UI behavior and publisher compatibility, offline.

Only inert files/widget doubles. No QApplication, window, network or process.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

from scripts import build_e108_user_candidate as build, build_release as publisher
from scripts.e108_release_input import public_uploads
from scripts.per_user_update_core import static_feed_url
from desktop.version import installed_release_details
from desktop.update_protocol import canonical, verify_release


def test_exact_https_base_resolves_to_explicit_stable_file():
    base = build.UPDATE_FEED_URL
    assert build.require_update_feed_url(base) == base
    for version in (build.BASE_VERSION, build.TARGET_VERSION):
        doc = build.version_document(version, {'public': bytes(32)})
        assert doc['update']['UPDATE_FEED_URL'] == base
        assert static_feed_url(doc) == base + 'stable/stable.json'
    assert publisher.origin_value(base + 'releases', False) == base + 'releases'


@pytest.mark.parametrize('url', ['https://89.45.45.28/mentorflow/updates/e108/stable.json',
    'https://other.example/e108/', 'https://89.45.45.28/mentorflow/updates/e108/?key=a',
    'http://89.45.45.28/mentorflow/updates/e108/'])
def test_final_build_cannot_drift_from_authorized_endpoint(url):
    with pytest.raises(ValueError): build.require_update_feed_url(url)


@pytest.mark.parametrize('path', ['/a/../secret', '/a/./x', '/a/%2e%2e/x', '/a/%2F/x',
    '/a//b', '/a\\b', '/a?key=x', '/a#frag'])
def test_mount_rejects_traversal_alias_and_credentials(path):
    with pytest.raises(ValueError): publisher.origin_value('https://updates.example' + path, False)


@pytest.mark.parametrize('notes', [None, '', [], 'x' * 2001])
def test_invalid_or_absent_installed_notes_are_not_rendered(notes):
    assert installed_release_details(dict(installed_release_notes=notes)) is None


def test_target_has_functional_offline_notes_and_copy_action_without_windows(monkeypatch):
    from desktop.gui_update_runtime import add_installed_release_details
    from PySide6 import QtWidgets
    class Widget:
        def __init__(self, text):
            self.text = text
            self.clicked = SimpleNamespace(connect=lambda fn: setattr(self, 'click', fn))
        def setObjectName(self, value): self.object_name = value
        def setText(self, value): self.text = value
        def setTextFormat(self, value): self.format = value
        def setWordWrap(self, value): pass
        def setTextInteractionFlags(self, value): pass
    monkeypatch.setattr(QtWidgets, 'QLabel', Widget)
    monkeypatch.setattr(QtWidgets, 'QPushButton', Widget)
    widgets = []
    clipboard = Mock()
    application = SimpleNamespace(clipboard=lambda: clipboard)
    layout = SimpleNamespace(addWidget=widgets.append)
    base = build.version_document('1.4.0', {'key': bytes(32)})
    assert add_installed_release_details(layout, application, base) is None
    assert widgets == []
    target = build.version_document('1.4.1', {'key': bytes(32)})
    target['update']['secret'] = 'MUST_NOT_COPY_CONFIG'
    button = add_installed_release_details(layout, application, target)
    assert button.text == '复制版本信息'
    assert [w.object_name for w in widgets] == ['installedReleaseHeading', 'installedReleaseDetails', 'copyInstalledVersion']
    clipboard.setText.assert_not_called()
    button.click()
    assert button.text == '已复制版本信息'
    copied = clipboard.setText.call_args.args[0]
    assert 'MentorFlow 1.4.1' in copied and 'stable' in copied and build.TARGET_RELEASE_NOTES in copied
    assert 'MUST_NOT_COPY_CONFIG' not in copied and 'https://' not in copied


def test_publisher_pair_matches_final_consumer_paths_and_public_allowlist(tmp_path):
    from scripts.test_build_release import package
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / 'test-signing-key.pem'
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    keys = {build.KEY_ID: key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    for notes in ('base-release-notes.txt', 'release-notes.txt'):
        (tmp_path / notes).write_text('offline fixture notes', encoding='utf-8')
    packages = [package(tmp_path / 'BASE', build.BASE_VERSION),
                package(tmp_path / 'TARGET', build.TARGET_VERSION, target=True)]
    source = dict(source_sha='a' * 40, tree_sha='b' * 40)
    for index, (root, version) in enumerate(zip(packages, (build.BASE_VERSION, build.TARGET_VERSION))):
        args = build.release_arguments(root, version, packages[0] if index else None, tmp_path, source)
        publisher.build(publisher.parser().parse_args(args))
    feed = tmp_path / 'feed-for-04-publication'
    pointer = feed / build.METADATA_RELATIVE_PATH
    pointer.parent.mkdir()
    pointer.write_bytes((feed / 'releases/stable.json').read_bytes())
    release = verify_release(pointer.read_bytes(), keys, current=build.BASE_VERSION)
    assert release.version == build.TARGET_VERSION
    assert release.signed['base_versions'] == [build.BASE_VERSION]
    publisher.verify_output(feed / 'releases', release)
    entries = public_uploads(feed)
    mapping = {row['relative_path']: row for row in entries}
    for asset in release.assets.values():
        relative = asset['url'].removeprefix(build.UPDATE_FEED_URL)
        assert relative.startswith('releases/objects/')
        assert mapping[relative]['sha256'] == asset['sha256']
    assert entries[-1]['relative_path'] == build.METADATA_RELATIVE_PATH
    assert entries[-1]['phase'] == 'ATOMIC_CHANNEL_LAST'
    assert all(row['phase'] == 'IMMUTABLE_FIRST' for row in entries[:-1])
    assert not any('.release-state' in path or path.endswith('.pem') or path == 'releases/stable.json' for path in mapping)
    private = feed / 'releases/.release-state'
    assert private.is_dir()  # State exists locally but cannot enter upload manifest.
