"""The live closure implementation has one home under server/account."""
from pathlib import Path


def test_unused_root_closure_implementation_is_absent():
    root = Path(__file__).resolve().parents[3] / 'refactor/server'
    assert not any((root / name).exists() for name in
                   ('closure.py', 'closure_api.py', 'closure_store.py', 'closure_provider.py'))
