from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from desktop.classroom_theme import (
    CLASSROOM_THEMES,
    DEFAULT_CLASSROOM_THEME,
    classroom_theme,
    classroom_theme_options,
    classroom_theme_render_signature,
    normalize_classroom_theme,
)


def test_builtin_gallery_has_sixteen_structurally_distinct_presets() -> None:
    assert len(CLASSROOM_THEMES) >= 16
    assert normalize_classroom_theme("") == DEFAULT_CLASSROOM_THEME == "sakura"
    assert len(classroom_theme_options()) == len(CLASSROOM_THEMES)
    required = {
        "palette", "background", "backgroundPattern", "surface", "bubbleStyle",
        "borderStyle", "shadow", "teacherStage", "decorativeLayer", "texture",
        "ambientOverlay", "iconTreatment", "motionIntensity",
    }
    assert all(required <= set(theme) for theme in CLASSROOM_THEMES.values())
    signatures = {
        tuple(theme[field] if field != "palette" else tuple(theme[field].items()) for field in (
            "palette", "backgroundPattern", "surface", "bubbleStyle", "borderStyle",
            "shadow", "teacherStage", "decorativeLayer", "texture", "iconTreatment",
            "motionIntensity",
        ))
        for theme in CLASSROOM_THEMES.values()
    }
    assert len(signatures) == len(CLASSROOM_THEMES)


def test_unknown_or_foreign_theme_never_inherits_a_teacher_preset() -> None:
    assert normalize_classroom_theme("developer-secret-preset") == "sakura"
    assert classroom_theme("developer-secret-preset")["palette_id"] == "sakura"


def test_native_render_signature_consumes_structural_descriptors() -> None:
    signatures = {classroom_theme_render_signature(key) for key in CLASSROOM_THEMES}
    assert len(signatures) == len(CLASSROOM_THEMES)
    for theme in CLASSROOM_THEMES.values():
        assert theme["bubble_descriptor"]["shape"]
        assert theme["shadow_descriptor"]["width"] > 0
        assert theme["stage_descriptor"]["frame"]
        assert theme["decorative_descriptor"]["family"]
        assert theme["texture_descriptor"]["family"]
        assert theme["icon_descriptor"]["weight"] > 0
        assert theme["chrome_descriptor"]["control_fill"]
