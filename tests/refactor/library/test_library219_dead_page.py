from pathlib import Path


def test_library219_dead_page_module_removed():
    root = Path(__file__).parents[3] / "refactor" / "learning" / "library"
    assert not (root / "page.py").exists()
