from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.export_source_whitelist import FIELDS, DEFAULT_SOURCE, convert, main


def test_real_lists_export_exactly_seven_fields_without_candidates():
    original = json.loads(DEFAULT_SOURCE.read_text(encoding='utf-8'))
    before = deepcopy(original)
    result = convert(original)
    value = json.loads(result)
    assert set(value) == set(FIELDS)
    assert 'candidates' in original and 'candidates' not in value
    assert all(value[k] == original[k] for k in FIELDS)
    assert original == before
    assert result == convert(original) and len(result) <= 1024 * 1024


@pytest.mark.parametrize('field,value', [('version', True), ('updated', 'invalid-date'),
    ('whitelist', {}), ('github_orgs', ['invalid']), ('blocked_keywords', {'adult': [123]})])
def test_invalid_source_rejected(field, value):
    source = json.loads(DEFAULT_SOURCE.read_text(encoding='utf-8'))
    source[field] = value
    with pytest.raises((ValueError, TypeError)):
        convert(source)


def test_missing_fields_and_oversized_output_rejected():
    source = json.loads(DEFAULT_SOURCE.read_text(encoding='utf-8'))
    with pytest.raises(ValueError):
        convert({k: v for k, v in source.items() if k != 'version'})
    source['whitelist'][0]['reason'] = 'x' * (1024 * 1024)
    with pytest.raises(ValueError, match='1 MiB'):
        convert(source)


def test_cli_atomic_output_does_not_damage_previous_file_on_invalid_input(tmp_path):
    out = tmp_path / 'source-whitelist.json'
    main(['--output', str(out)])
    previous = out.read_bytes()
    assert json.loads(previous)['version']
    bad = tmp_path / 'invalid.json'
    bad.write_text('{invalid', encoding='utf-8')
    with pytest.raises(SystemExit) as exc:
        main(['--source', str(bad), '--output', str(out)])
    assert exc.value.code == 2 and out.read_bytes() == previous
    assert not list(tmp_path.glob('*.tmp'))
