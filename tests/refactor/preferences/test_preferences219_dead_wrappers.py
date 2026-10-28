from pathlib import Path


def test_preferences219_dead_theme_wrappers_removed():
    root = Path(__file__).parents[3] / "refactor" / "learning" / "preferences"
    assert not (root / "store.py").exists()
    assert not (root / "themes.py").exists()
