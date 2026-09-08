"""Real-package path budget regression; no App/UI/install/network execution."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from desktop import update_service
from scripts import per_user_install, install_product
from scripts.runtime_path_budget import runtime_root


def configured(monkeypatch, *, app_id='MentorFlow.UAT.E108Assembly1', root='E:/Test/MentorFlow', explicit=None):
    product = install_product.Product(app_id)
    monkeypatch.setattr(install_product, 'configured', lambda: product)
    monkeypatch.setattr(per_user_install, 'is_install', lambda _: True)
    monkeypatch.setattr(per_user_install, 'keys', lambda: {'test-key': bytes(32)})
    monkeypatch.setattr(update_service, 'MatureUpdateService', lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setenv('LOCALAPPDATA', 'C:/Users/LongerUsername/AppData/Local')
    config = dict(protocol='mentorflow.signed-file-release.v1', public_keys={'test-key': 'AA=='},
                  metadata_url=('http://127.0.0.1:48218/stable.json' if product.uat
                                else 'https://updates.example.com/stable.json'))
    if explicit: config['staging_root'] = explicit
    return update_service.configured_mature_service(install_root=Path(root),
        version_manifest=dict(version='1.4.0', channel='stable', update=config))


def test_real_package_longest_path_fits_normal_profile(monkeypatch):
    service = configured(monkeypatch)
    longest = ('_internal/torch-2.13.0.dist-info/licenses/third_party/kineto/libkineto/'
               'third_party/dynolog/third_party/prometheus-cpp/3rdparty/googletest/LICENSE')
    assert len(longest) == 145  # actual final candidate's longest dependency path
    target = service.runtime_staging_root / ('a' * 12) / 'App'
    assert runtime_root(target, [longest]) <= 240
    assert '.mfu' in service.runtime_staging_root.parts
    assert 'MentorFlow.UAT.E108Assembly1' in service.cache_root.parts
    # The previous default failed even for the current five-character username.
    old = Path('C:/Users/21533/AppData/Local') / 'MentorFlow.UAT.E108Assembly1/Updates/.u' / ('b' * 12) / ('a' * 12) / 'App'
    with pytest.raises(ValueError, match='路径过长'): runtime_root(old, [longest])


def test_short_stage_retains_product_and_install_separation(monkeypatch):
    first = configured(monkeypatch)
    other_root = configured(monkeypatch, root='E:/Another/MentorFlow')
    other_product = configured(monkeypatch, app_id='MentorFlow.UAT.Other')
    assert len({str(s.runtime_staging_root) for s in (first, other_root, other_product)}) == 3


def test_production_and_explicit_cache_unchanged(monkeypatch):
    production = configured(monkeypatch, app_id='MentorFlow')
    explicit = configured(monkeypatch, explicit='E:/DedicatedUpdateCache')
    assert production.runtime_staging_root is None
    assert production.cache_root.name == 'MentorFlowUpdates'
    assert explicit.runtime_staging_root is None
    assert explicit.cache_root == 'E:/DedicatedUpdateCache'


def test_builder_does_not_alias_base_into_target():
    # Source-level wiring invariant: original no-hardlink verifier stays intact.
    from scripts import build_e108_user_candidate as builder
    source = Path(builder.__file__).read_text(encoding='utf-8')
    assert 'os.link(path, dest)' not in source
    assert 'shutil.copyfile(path, dest)' in source
