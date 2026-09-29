"""Moved identity assets are the existing MF-U11 artifacts, with no new SPI logic."""
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[3]


def test_slim_keycloak_runtime_assets_are_reused_byte_for_byte():
    manifest=json.loads((ROOT/'refactor/server/source_manifest.json').read_text(encoding='utf-8'))
    for relative,source in manifest.items():
        if relative.startswith('keycloak/') and relative!='keycloak/build.py':
            # Checkout line endings differ by machine (refactor/server is LF-only); compare content.
            moved, original = ((ROOT/p).read_bytes().replace(b'\r\n', b'\n') for p in ('refactor/server/'+relative, source['source']))
            assert moved==original,relative
    jar=ROOT/'refactor/server/keycloak/mentorflow-native-verifier.jar'
    with zipfile.ZipFile(jar) as package:
        names=package.namelist()
        assert 'win/clannad/mentorflow/RegistrationCodes.class' in names
        assert not any('FixtureControl' in name for name in names)


def test_slim_spi_builder_uses_only_moved_sources():
    source=(ROOT/'refactor/server/keycloak/build.py').read_text(encoding='utf-8')
    assert "ROOT = Path(__file__).resolve().parent" in source
    assert 'deploy/enterprise' not in source
    assert "'RegistrationCodes.java'" in source
    assert "'EmailCodeBinding.java'" in source
