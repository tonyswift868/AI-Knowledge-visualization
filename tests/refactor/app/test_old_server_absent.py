"""The retired Python HTTP application cannot be launched from the tree."""
from pathlib import Path


def test_unused_root_closure_implementation_is_absent():
    root = Path(__file__).resolve().parents[3] / "refactor/server"
    assert not (root / "app.py").exists()
    assert not (root / "entrypoint.py").exists()
    assert not any((root / "account").glob("*.py"))
