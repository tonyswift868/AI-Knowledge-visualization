"""Native classroom theme manifest.

The manifest intentionally describes structure as well as colour.  A theme is
therefore safe to preview and persist as a local UI preference without
touching teacher presets, provider/model settings, or learning state.
"""

from __future__ import annotations

from typing import Any


DEFAULT_CLASSROOM_THEME = "sakura"

# The native settings page keeps the historical manifest available for
# keyboard users and existing preferences, while presenting this small,
# high-contrast set as the first classroom-only preview/apply slice.
FEATURED_CLASSROOM_THEME_KEYS: tuple[str, ...] = (
    "sakura", "after_school", "rainy_window", "cyber",
)
FEATURED_CLASSROOM_THEME_LABELS: dict[str, str] = {
    "sakura": "Sakura · 樱花教室",
    "after_school": "Japanese Sunset · 放学后教室",
    "rainy_window": "Rainy Night · 雨窗自习",
    "cyber": "Cyber Academy · 赛博学院",
}


# Structural descriptors are deliberately data driven.  Qt style sheets do
# not implement CSS box-shadows or textures, so the native renderer consumes
# these small descriptors to turn the manifest vocabulary into visible,
# deterministic geometry and chrome instead of silently treating every skin as
# the same generic card.
_BUBBLE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "soft-card": {"shape": "rounded", "padding": 16, "edge": 1, "shadow_width": 3, "shadow_side": "bottom"},
    "paper-note": {"shape": "page", "padding": 14, "edge": 1, "shadow_width": 4, "shadow_side": "right"},
    "glass": {"shape": "pill", "padding": 17, "edge": 1, "shadow_width": 1, "shadow_side": "bottom"},
    "dark-glow": {"shape": "rounded", "padding": 16, "edge": 2, "shadow_width": 2, "shadow_side": "bottom"},
    "chalk-rounded": {"shape": "chalk", "padding": 13, "edge": 2, "shadow_width": 1, "shadow_side": "bottom"},
    "book-page": {"shape": "page", "padding": 14, "edge": 1, "shadow_width": 4, "shadow_side": "right"},
    "rounded": {"shape": "rounded", "padding": 15, "edge": 1, "shadow_width": 2, "shadow_side": "bottom"},
    "frosted": {"shape": "glass", "padding": 16, "edge": 1, "shadow_width": 2, "shadow_side": "bottom"},
    "watercolor": {"shape": "wash", "padding": 18, "edge": 1, "shadow_width": 3, "shadow_side": "bottom"},
    "notebook": {"shape": "note", "padding": 13, "edge": 2, "shadow_width": 3, "shadow_side": "right"},
    "neon-panel": {"shape": "terminal", "padding": 14, "edge": 2, "shadow_width": 2, "shadow_side": "bottom"},
    "minimal": {"shape": "minimal", "padding": 12, "edge": 1, "shadow_width": 1, "shadow_side": "bottom"},
    "pixel": {"shape": "pixel", "padding": 11, "edge": 2, "shadow_width": 3, "shadow_side": "right"},
    "warm-card": {"shape": "rounded", "padding": 15, "edge": 1, "shadow_width": 3, "shadow_side": "bottom"},
    "frost": {"shape": "frost", "padding": 16, "edge": 1, "shadow_width": 2, "shadow_side": "bottom"},
    "cafe-card": {"shape": "page", "padding": 14, "edge": 1, "shadow_width": 4, "shadow_side": "right"},
}

_SHADOW_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "soft-petal": {"color_role": "accent_soft", "alpha": 180, "width": 3},
    "paper-lift": {"color_role": "border", "alpha": 190, "width": 4},
    "cloud-soft": {"color_role": "accent_soft", "alpha": 150, "width": 2},
    "midnight-glow": {"color_role": "accent", "alpha": 210, "width": 2},
    "chalk-offset": {"color_role": "accent_dark", "alpha": 160, "width": 2},
    "wooden": {"color_role": "border", "alpha": 210, "width": 4},
    "late-sun": {"color_role": "accent_soft", "alpha": 175, "width": 3},
    "wet-glass": {"color_role": "accent", "alpha": 135, "width": 2},
    "pigment-soft": {"color_role": "accent_soft", "alpha": 160, "width": 3},
    "page-curl": {"color_role": "border", "alpha": 200, "width": 3},
    "neon-edge": {"color_role": "accent", "alpha": 220, "width": 2},
    "hairline": {"color_role": "border", "alpha": 150, "width": 1},
    "pixel-offset": {"color_role": "accent_dark", "alpha": 220, "width": 3},
    "leaf-shadow": {"color_role": "accent_soft", "alpha": 180, "width": 3},
    "ice-soft": {"color_role": "accent_soft", "alpha": 155, "width": 2},
    "tabletop": {"color_role": "border", "alpha": 210, "width": 4},
}

_STAGE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "sakura-window": {"frame": "window", "header_mark": "✿", "panel_role": "surface_alt"},
    "reading-lamp": {"frame": "lamp", "header_mark": "☕", "panel_role": "surface_alt"},
    "sky-window": {"frame": "window", "header_mark": "☁", "panel_role": "surface_alt"},
    "moon-window": {"frame": "moon", "header_mark": "✦", "panel_role": "surface_alt"},
    "board-frame": {"frame": "board", "header_mark": "✎", "panel_role": "surface"},
    "library-lamp": {"frame": "lamp", "header_mark": "⌂", "panel_role": "surface_alt"},
    "classroom-window": {"frame": "window", "header_mark": "♫", "panel_role": "surface_alt"},
    "rain-window": {"frame": "rain", "header_mark": "☂", "panel_role": "surface_alt"},
    "painted-easel": {"frame": "easel", "header_mark": "◌", "panel_role": "surface_alt"},
    "journal-clip": {"frame": "journal", "header_mark": "▤", "panel_role": "surface"},
    "holo-stage": {"frame": "holo", "header_mark": "⌬", "panel_role": "surface_alt"},
    "marker-board": {"frame": "board", "header_mark": "□", "panel_role": "surface"},
    "crt-stage": {"frame": "crt", "header_mark": "▣", "panel_role": "surface"},
    "autumn-window": {"frame": "window", "header_mark": "❦", "panel_role": "surface_alt"},
    "snow-window": {"frame": "snow", "header_mark": "❄", "panel_role": "surface_alt"},
    "cafe-window": {"frame": "cafe", "header_mark": "☕", "panel_role": "surface_alt"},
}

_DECORATIVE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "petal-drift": {"family": "petal", "count": 10, "motion": 0.012},
    "dust": {"family": "dust", "count": 18, "motion": 0.002},
    "cloud-float": {"family": "cloud", "count": 5, "motion": 0.004},
    "star-drift": {"family": "star", "count": 14, "motion": 0.008},
    "chalk-dust": {"family": "chalk", "count": 22, "motion": 0.001},
    "dust-motes": {"family": "dust", "count": 20, "motion": 0.002},
    "dust-ray": {"family": "ray", "count": 6, "motion": 0.003},
    "rain-streaks": {"family": "rain", "count": 16, "motion": 0.015},
    "wash-blobs": {"family": "wash", "count": 7, "motion": 0.006},
    "margin-notes": {"family": "note", "count": 8, "motion": 0.001},
    "scanline": {"family": "scanline", "count": 18, "motion": 0.012},
    "marker-dots": {"family": "dot", "count": 24, "motion": 0.001},
    "scanlines": {"family": "scanline", "count": 20, "motion": 0.014},
    "falling-leaves": {"family": "leaf", "count": 10, "motion": 0.012},
    "snow-drift": {"family": "snow", "count": 20, "motion": 0.006},
    "steam": {"family": "steam", "count": 7, "motion": 0.008},
}

_TEXTURE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "silk": {"family": "crosshatch", "alpha": 10, "step": 24},
    "fibres": {"family": "fibres", "alpha": 18, "step": 18},
    "mist": {"family": "mist", "alpha": 12, "step": 40},
    "velvet": {"family": "velvet", "alpha": 18, "step": 30},
    "chalk": {"family": "chalk", "alpha": 28, "step": 12},
    "paper-grain": {"family": "grain", "alpha": 20, "step": 20},
    "painted-wall": {"family": "wash", "alpha": 15, "step": 32},
    "condensation": {"family": "mist", "alpha": 20, "step": 18},
    "paper-cotton": {"family": "grain", "alpha": 19, "step": 15},
    "lined-paper": {"family": "lines", "alpha": 22, "step": 20},
    "carbon": {"family": "carbon", "alpha": 26, "step": 8},
    "dry-erase": {"family": "erase", "alpha": 12, "step": 28},
    "crt": {"family": "scanline", "alpha": 34, "step": 6},
    "parchment": {"family": "grain", "alpha": 20, "step": 21},
    "frost": {"family": "frost", "alpha": 22, "step": 16},
    "kraft-paper": {"family": "grain", "alpha": 24, "step": 18},
}

_ICON_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "petal": {"weight": 850, "letter_spacing": 0, "transform": "none"},
    "ink": {"weight": 700, "letter_spacing": 1, "transform": "none"},
    "outline": {"weight": 700, "letter_spacing": 1, "transform": "none"},
    "glow": {"weight": 900, "letter_spacing": 2, "transform": "none"},
    "chalk": {"weight": 700, "letter_spacing": 1, "transform": "none"},
    "engraved": {"weight": 800, "letter_spacing": 2, "transform": "none"},
    "handwritten": {"weight": 700, "letter_spacing": 0, "transform": "none"},
    "waterline": {"weight": 700, "letter_spacing": 1, "transform": "none"},
    "brush": {"weight": 800, "letter_spacing": 0, "transform": "none"},
    "sticker": {"weight": 900, "letter_spacing": 0, "transform": "none"},
    "hologram": {"weight": 900, "letter_spacing": 2, "transform": "none"},
    "line": {"weight": 600, "letter_spacing": 1, "transform": "none"},
    "pixel": {"weight": 900, "letter_spacing": 1, "transform": "none"},
    "leaf": {"weight": 800, "letter_spacing": 0, "transform": "none"},
    "crystal": {"weight": 800, "letter_spacing": 2, "transform": "none"},
    "stamp": {"weight": 800, "letter_spacing": 1, "transform": "none"},
}

_CHROME_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "soft": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 800},
    "paper": {"control_fill": "surface", "control_border": "border", "focus_width": 2, "button_weight": 700},
    "night": {"control_fill": "surface_alt", "control_border": "accent", "focus_width": 2, "button_weight": 800},
    "chalkboard": {"control_fill": "surface", "control_border": "border", "focus_width": 2, "button_weight": 700},
    "wood": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 750},
    "window": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 800},
    "rain-glass": {"control_fill": "surface", "control_border": "accent", "focus_width": 2, "button_weight": 800},
    "wash": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 750},
    "notebook": {"control_fill": "surface", "control_border": "border", "focus_width": 2, "button_weight": 700},
    "cyber": {"control_fill": "surface_alt", "control_border": "accent", "focus_width": 3, "button_weight": 900},
    "minimal": {"control_fill": "surface", "control_border": "border", "focus_width": 2, "button_weight": 700},
    "retro": {"control_fill": "surface", "control_border": "accent_dark", "focus_width": 2, "button_weight": 900},
    "autumn": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 800},
    "ice": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 700},
    "cafe": {"control_fill": "surface_elevated", "control_border": "border", "focus_width": 2, "button_weight": 750},
}


def _palette(
    top: str, bottom: str, surface: str, alt: str, border: str,
    accent: str, dark: str, soft: str, text: str, muted: str,
    teacher: str, learner: str,
) -> dict[str, str]:
    return {
        "background_top": top, "background_bottom": bottom, "surface": surface,
        "surface_alt": alt, "border": border, "accent": accent,
        "accent_dark": dark, "accent_soft": soft, "text": text, "muted": muted,
        "teacher_bubble": teacher, "teacher_border": border,
        "learner_bubble": learner, "learner_border": accent,
        "status_bg": soft, "status_border": accent,
        "scrollbar": accent,
    }


def _theme(
    *, key: str, label: str, description: str, symbol: str,
    palette: dict[str, str], background_pattern: str, bubble_style: str,
    border_style: str, shadow: str, teacher_stage: str, decorative_layer: str,
    texture: str, ambient_overlay: tuple[str, ...], icon_treatment: str,
    motion_intensity: str, spacing: int, font_family: str, font_size: int,
    panel_radius: int, bubble_radius: int, control_radius: int,
    background_direction: str = "vertical", chrome: str = "soft",
) -> dict[str, Any]:
    """Normalise modern manifest fields and legacy renderer aliases."""

    token = dict(palette)
    bubble_descriptor = dict(_BUBBLE_DESCRIPTORS.get(bubble_style, _BUBBLE_DESCRIPTORS["rounded"]))
    shadow_descriptor = dict(_SHADOW_DESCRIPTORS.get(shadow, {"color_role": "border", "alpha": 160, "width": 2}))
    stage_descriptor = dict(_STAGE_DESCRIPTORS.get(teacher_stage, {"frame": "window", "header_mark": symbol, "panel_role": "surface_alt"}))
    decorative_descriptor = dict(_DECORATIVE_DESCRIPTORS.get(decorative_layer, {"family": "dot", "count": 12, "motion": 0.004}))
    texture_descriptor = dict(_TEXTURE_DESCRIPTORS.get(texture, {"family": "grain", "alpha": 14, "step": 20}))
    icon_descriptor = dict(_ICON_DESCRIPTORS.get(icon_treatment, _ICON_DESCRIPTORS["line"]))
    chrome_descriptor = dict(_CHROME_DESCRIPTORS.get(chrome, _CHROME_DESCRIPTORS["soft"]))
    shadow_color = str(palette.get(shadow_descriptor["color_role"], palette["border"]))
    token.update(
        {
            "key": key, "label": label, "description": description, "symbol": symbol,
            # Explicit manifest vocabulary used by gallery/QA and future
            # renderers.  These are structural tokens, not palette aliases.
            "palette": dict(palette), "palette_id": key,
            "background": f"{palette['background_top']} → {palette['background_bottom']}",
            "backgroundPattern": background_pattern, "surface": palette["surface"],
            "bubbleStyle": bubble_style, "borderStyle": border_style, "shadow": shadow,
            "teacherStage": teacher_stage, "decorativeLayer": decorative_layer,
            "texture": texture, "ambientOverlay": ambient_overlay,
            "iconTreatment": icon_treatment, "motionIntensity": motion_intensity,
            "bubble_descriptor": bubble_descriptor,
            "shadow_descriptor": shadow_descriptor,
            "shadow_color": shadow_color,
            "stage_descriptor": stage_descriptor,
            "decorative_descriptor": decorative_descriptor,
            "texture_descriptor": texture_descriptor,
            "icon_descriptor": icon_descriptor,
            "chrome_descriptor": chrome_descriptor,
            # Snake-case aliases consumed by the existing Qt renderer.
            "background_pattern": background_pattern, "pattern_color": palette["border"],
            "surface_elevated": palette.get("surface_elevated", palette["surface"]),
            "surface_inset": palette.get("surface_inset", palette["surface_alt"]),
            # Qt's stylesheet parser supports solid/dashed/dotted reliably;
            # richer borderStyle values remain in the manifest while the
            # renderer falls back to a safe solid line.
            "bubble_shadow": shadow,
            "border_style": border_style if border_style in {"solid", "dashed", "dotted"} else "solid",
            "ambient": ambient_overlay, "ambient_mode": decorative_layer,
            "spacing": spacing, "font_family": font_family, "font_size": font_size,
            "title_weight": 850 if bubble_style in {"pill", "glass"} else 750,
            "panel_radius": panel_radius, "bubble_radius": bubble_radius,
            "control_radius": control_radius, "chrome": chrome,
            "motion_easing": "linear" if motion_intensity == "quiet" else "out-cubic",
            "scrollbar_width": 13 if bubble_style == "pill" else 10,
            "background_direction": background_direction,
            "bubble_shape": bubble_descriptor["shape"],
            "bubble_padding": bubble_descriptor["padding"],
            "bubble_edge_width": bubble_descriptor["edge"],
            "bubble_shadow_width": max(int(bubble_descriptor["shadow_width"]), int(shadow_descriptor["width"])),
            "bubble_shadow_side": bubble_descriptor["shadow_side"],
            "stage_frame": stage_descriptor["frame"],
            "stage_header_mark": stage_descriptor["header_mark"],
            "decorative_family": decorative_descriptor["family"],
            "texture_family": texture_descriptor["family"],
            "texture_alpha": texture_descriptor["alpha"],
            "icon_weight": icon_descriptor["weight"],
            "icon_letter_spacing": icon_descriptor["letter_spacing"],
            "control_fill_role": chrome_descriptor["control_fill"],
            "control_border_role": chrome_descriptor["control_border"],
            "focus_width": chrome_descriptor["focus_width"],
            "button_weight": chrome_descriptor["button_weight"],
        }
    )
    return token


CLASSROOM_THEMES: dict[str, dict[str, Any]] = {
    "sakura": _theme(
        key="sakura", label="樱花教室", description="默认 · 柔和粉色、花瓣光影与温暖对话气泡", symbol="✿",
        palette=_palette("#fff8fb", "#f9eef4", "#fffdfd", "#fff8fb", "#edc8d8", "#c95f8d", "#8d3f62", "#ffe9f2", "#3b2b3f", "#786572", "#fff8fb", "#fbe7f0"),
        background_pattern="petals", bubble_style="soft-card", border_style="solid", shadow="soft-petal", teacher_stage="sakura-window", decorative_layer="petal-drift", texture="silk", ambient_overlay=("#f4a8c6", "#e8bfd4", "#fff6d9"), icon_treatment="petal", motion_intensity="gentle", spacing=10, font_family="Segoe UI, Microsoft YaHei", font_size=14, panel_radius=16, bubble_radius=20, control_radius=10,
    ),
    "cream": _theme(
        key="cream", label="奶油自习室", description="暖米色、纸张质感与安静的阅读氛围", symbol="☕",
        palette=_palette("#fffaf0", "#f3e8d5", "#fffdf7", "#fbf3e7", "#e5d2b7", "#b57945", "#77502f", "#f8ead7", "#3f3329", "#796a5c", "#fffaf0", "#f5ead9"),
        background_pattern="paper", bubble_style="paper-note", border_style="solid", shadow="paper-lift", teacher_stage="reading-lamp", decorative_layer="dust", texture="fibres", ambient_overlay=("#e5b77e", "#f1d7ad", "#fff8e2"), icon_treatment="ink", motion_intensity="quiet", spacing=9, font_family="Georgia, Microsoft YaHei", font_size=14, panel_radius=12, bubble_radius=12, control_radius=6, background_direction="diagonal", chrome="paper",
    ),
    "sky": _theme(
        key="sky", label="晴空课堂", description="轻盈蓝白、通透背景与清晰的信息层级", symbol="☁",
        palette=_palette("#f5fbff", "#e8f2fb", "#fbfdff", "#f2f8fd", "#c9deee", "#5689b7", "#365f83", "#e5f3ff", "#283846", "#627587", "#f8fcff", "#e5f2fc"),
        background_pattern="clouds", bubble_style="glass", border_style="solid", shadow="cloud-soft", teacher_stage="sky-window", decorative_layer="cloud-float", texture="mist", ambient_overlay=("#8cc6ed", "#c4e2f5", "#ffffff"), icon_treatment="outline", motion_intensity="gentle", spacing=11, font_family="Segoe UI, Microsoft YaHei", font_size=14, panel_radius=18, bubble_radius=18, control_radius=12,
    ),
    "night": _theme(
        key="night", label="星夜学院", description="深蓝夜色、柔光气泡与低眩光长时间学习", symbol="✦",
        palette=_palette("#192235", "#0e1524", "#202b3e", "#182235", "#3a4b68", "#da86b1", "#f0afd0", "#3a2941", "#f2edf4", "#b7afc0", "#252f43", "#3a2941"),
        background_pattern="stars", bubble_style="dark-glow", border_style="solid", shadow="midnight-glow", teacher_stage="moon-window", decorative_layer="star-drift", texture="velvet", ambient_overlay=("#b16fbc", "#648bd1", "#f4c8e0"), icon_treatment="glow", motion_intensity="gentle", spacing=10, font_family="Segoe UI, Microsoft YaHei", font_size=14, panel_radius=14, bubble_radius=16, control_radius=8, chrome="night",
    ),
    "chalkboard": _theme(
        key="chalkboard", label="黑板课堂", description="黑板绿、粉笔线与真实课堂板书层次", symbol="✎",
        palette=_palette("#314c45", "#1d312d", "#36584f", "#29473f", "#769d8f", "#f0c674", "#ffe4a3", "#496c60", "#f1f5ed", "#b8cbbf", "#385b50", "#2e4c43"),
        background_pattern="chalk-grid", bubble_style="chalk-rounded", border_style="dashed", shadow="chalk-offset", teacher_stage="board-frame", decorative_layer="chalk-dust", texture="chalk", ambient_overlay=("#7ea99b", "#d1bd7f", "#6e8c82"), icon_treatment="chalk", motion_intensity="quiet", spacing=10, font_family="Trebuchet MS, Microsoft YaHei", font_size=14, panel_radius=8, bubble_radius=8, control_radius=5, chrome="chalkboard",
    ),
    "library": _theme(
        key="library", label="暖心图书馆", description="胡桃木书架、暖灯与沉静的深色文字", symbol="⌂",
        palette=_palette("#fff7e8", "#ead3aa", "#fffaf0", "#f3e3c8", "#d0a66b", "#9a6533", "#633c21", "#f3ddbd", "#3c2b20", "#76604f", "#fff5e3", "#f0dfc3"),
        background_pattern="woodgrain", bubble_style="book-page", border_style="solid", shadow="wooden", teacher_stage="library-lamp", decorative_layer="dust-motes", texture="paper-grain", ambient_overlay=("#e5ac66", "#f0d297", "#fff0bd"), icon_treatment="engraved", motion_intensity="quiet", spacing=10, font_family="Palatino Linotype, Microsoft YaHei", font_size=14, panel_radius=10, bubble_radius=9, control_radius=5, background_direction="diagonal", chrome="wood",
    ),
    "after_school": _theme(
        key="after_school", label="放学后教室", description="金橙夕照、长窗影和放学后安静的教室层次", symbol="♫",
        palette=_palette("#fff1d5", "#d98c79", "#fff8ec", "#f8dec9", "#d9906f", "#d15f4f", "#71323b", "#ffd2ad", "#402a31", "#76535a", "#fff0df", "#f1c9bc"),
        background_pattern="window-light", bubble_style="rounded", border_style="solid", shadow="late-sun", teacher_stage="classroom-window", decorative_layer="dust-ray", texture="painted-wall", ambient_overlay=("#efb071", "#e7958d", "#ffe6ae"), icon_treatment="handwritten", motion_intensity="gentle", spacing=11, font_family="Yu Gothic, Microsoft YaHei", font_size=14, panel_radius=14, bubble_radius=18, control_radius=9, background_direction="diagonal", chrome="window",
    ),
    "rainy_window": _theme(
        key="rainy_window", label="雨夜自习", description="深蓝雨窗、暗色桌面与一盏暖灯的夜间自习", symbol="☂",
        palette=_palette("#17283d", "#0b1424", "#1d3044", "#14263a", "#3f627d", "#78b7d4", "#b8def0", "#27475d", "#f0f7fb", "#a9bdc9", "#21394d", "#284a60"),
        background_pattern="raindrops", bubble_style="frosted", border_style="solid", shadow="wet-glass", teacher_stage="rain-window", decorative_layer="rain-streaks", texture="condensation", ambient_overlay=("#5ca6ca", "#88b7d0", "#f0b36c"), icon_treatment="waterline", motion_intensity="gentle", spacing=10, font_family="Segoe UI, Microsoft YaHei", font_size=14, panel_radius=17, bubble_radius=16, control_radius=10, chrome="rain-glass",
    ),
    "watercolor": _theme(
        key="watercolor", label="水彩课堂", description="水彩晕染、柔软色块与轻盈的创作课堂", symbol="◌",
        palette=_palette("#fff8f2", "#e9f0f8", "#fffdf9", "#f5f1ee", "#d8c7d5", "#8e78b5", "#5e4c84", "#eee7fa", "#3a3343", "#786f80", "#fffaf3", "#e9e3f5"),
        background_pattern="wash", bubble_style="watercolor", border_style="solid", shadow="pigment-soft", teacher_stage="painted-easel", decorative_layer="wash-blobs", texture="paper-cotton", ambient_overlay=("#efb0b2", "#b6cee8", "#ebd09b"), icon_treatment="brush", motion_intensity="gentle", spacing=12, font_family="Segoe Print, Microsoft YaHei", font_size=14, panel_radius=20, bubble_radius=22, control_radius=12, background_direction="diagonal", chrome="wash",
    ),
    "notebook": _theme(
        key="notebook", label="纸张学习手帐", description="横线纸、彩色批注与可亲近的学习手帐", symbol="▤",
        palette=_palette("#fffef9", "#f2f2dc", "#fffefa", "#faf8e9", "#d8d2ac", "#4d83a8", "#2f5773", "#e7f0f8", "#273640", "#6d7777", "#fffefa", "#e5eef5"),
        background_pattern="ruled-paper", bubble_style="notebook", border_style="dashed", shadow="page-curl", teacher_stage="journal-clip", decorative_layer="margin-notes", texture="lined-paper", ambient_overlay=("#a7cbe3", "#e6d68d", "#f3bbb6"), icon_treatment="sticker", motion_intensity="quiet", spacing=9, font_family="Comic Sans MS, Microsoft YaHei", font_size=14, panel_radius=6, bubble_radius=7, control_radius=5, chrome="notebook",
    ),
    "cyber": _theme(
        key="cyber", label="赛博学院", description="深紫霓虹、模块化面板与未来实验室", symbol="⌬",
        palette=_palette("#18152b", "#0b0d19", "#252143", "#171631", "#514b89", "#d96df5", "#f4c0ff", "#3a2c58", "#f3edff", "#b0a7cd", "#29264b", "#392b50"),
        background_pattern="circuit", bubble_style="neon-panel", border_style="solid", shadow="neon-edge", teacher_stage="holo-stage", decorative_layer="scanline", texture="carbon", ambient_overlay=("#ce61e8", "#5a76e5", "#78eadf"), icon_treatment="hologram", motion_intensity="active", spacing=8, font_family="Cascadia Code, Microsoft YaHei", font_size=13, panel_radius=5, bubble_radius=6, control_radius=3, chrome="cyber",
    ),
    "whiteboard": _theme(
        key="whiteboard", label="极简白板", description="大面积留白、黑色记号与极简课堂控制", symbol="□",
        palette=_palette("#ffffff", "#eef0f2", "#ffffff", "#f7f8f9", "#c8cdd1", "#3e596c", "#1f303d", "#e7edf1", "#202a31", "#68747b", "#ffffff", "#edf1f4"),
        background_pattern="whiteboard", bubble_style="minimal", border_style="solid", shadow="hairline", teacher_stage="marker-board", decorative_layer="marker-dots", texture="dry-erase", ambient_overlay=("#bbd2e0", "#e5edf2", "#ffffff"), icon_treatment="line", motion_intensity="quiet", spacing=8, font_family="Segoe UI, Microsoft YaHei", font_size=14, panel_radius=5, bubble_radius=5, control_radius=3, chrome="minimal",
    ),
    "retro_lab": _theme(
        key="retro_lab", label="复古电脑实验室", description="复古终端绿、像素边框与老派计算机教室", symbol="▣",
        palette=_palette("#e8f0d3", "#b7ca9e", "#f1f5dc", "#dae6c4", "#78905e", "#4e754e", "#254d38", "#d0e5b4", "#25372a", "#5d745e", "#edf6d8", "#cde2b5"),
        background_pattern="pixel-grid", bubble_style="pixel", border_style="double", shadow="pixel-offset", teacher_stage="crt-stage", decorative_layer="scanlines", texture="crt", ambient_overlay=("#8ebd77", "#bdd68e", "#eff3c8"), icon_treatment="pixel", motion_intensity="active", spacing=7, font_family="Consolas, Microsoft YaHei", font_size=13, panel_radius=2, bubble_radius=2, control_radius=1, chrome="retro",
    ),
    "autumn": _theme(
        key="autumn", label="秋日课堂", description="枫叶橙、羊皮纸与温暖的秋日复习", symbol="❦",
        palette=_palette("#fff9ef", "#f2dbc0", "#fffdf8", "#faeee0", "#e0bb92", "#bd713b", "#7c452b", "#fae2c9", "#453128", "#80675a", "#fff7eb", "#f5e0ca"),
        background_pattern="leaves", bubble_style="warm-card", border_style="solid", shadow="leaf-shadow", teacher_stage="autumn-window", decorative_layer="falling-leaves", texture="parchment", ambient_overlay=("#e2a064", "#cf7a51", "#f4d18e"), icon_treatment="leaf", motion_intensity="gentle", spacing=10, font_family="Cambria, Microsoft YaHei", font_size=14, panel_radius=13, bubble_radius=15, control_radius=8, background_direction="diagonal", chrome="autumn",
    ),
    "snow": _theme(
        key="snow", label="雪日自习室", description="冰雪蓝、霜花纹理与安静的冬日专注", symbol="❄",
        palette=_palette("#f8fdff", "#dbeaf4", "#ffffff", "#edf6fb", "#c0d8e6", "#6b9fba", "#3d6c83", "#deeff8", "#253944", "#6d818b", "#f9fdff", "#e5f1f7"),
        background_pattern="snowflakes", bubble_style="frost", border_style="solid", shadow="ice-soft", teacher_stage="snow-window", decorative_layer="snow-drift", texture="frost", ambient_overlay=("#9ccbe0", "#cde8f1", "#ffffff"), icon_treatment="crystal", motion_intensity="quiet", spacing=11, font_family="Segoe UI Light, Microsoft YaHei", font_size=14, panel_radius=16, bubble_radius=18, control_radius=10, chrome="ice",
    ),
    "coffee": _theme(
        key="coffee", label="咖啡学习室", description="咖啡棕、木桌与适合长时间阅读的暖色对比", symbol="☕",
        palette=_palette("#fbf6ef", "#e6d1bf", "#fffaf5", "#f4e8dc", "#d2b09a", "#9a6046", "#63382a", "#f1ddcf", "#3d2d28", "#7e6960", "#fff6ed", "#f0ded0"),
        background_pattern="coffee-rings", bubble_style="cafe-card", border_style="solid", shadow="tabletop", teacher_stage="cafe-window", decorative_layer="steam", texture="kraft-paper", ambient_overlay=("#d69a6d", "#efc09a", "#f9e1b7"), icon_treatment="stamp", motion_intensity="quiet", spacing=10, font_family="Book Antiqua, Microsoft YaHei", font_size=14, panel_radius=11, bubble_radius=13, control_radius=7, background_direction="diagonal", chrome="cafe",
    ),
}


def normalize_classroom_theme(value: object) -> str:
    key = str(value or "").strip().casefold()
    return key if key in CLASSROOM_THEMES else DEFAULT_CLASSROOM_THEME


def classroom_theme(value: object) -> dict[str, Any]:
    return CLASSROOM_THEMES[normalize_classroom_theme(value)]


def classroom_theme_options() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (key, str(theme["label"]), str(theme["description"]))
        for key, theme in CLASSROOM_THEMES.items()
    )


def featured_classroom_theme_options() -> tuple[tuple[str, str, str], ...]:
    """Return the four recommended, visibly different classroom themes."""

    return tuple(
        (
            key,
            FEATURED_CLASSROOM_THEME_LABELS.get(key, str(CLASSROOM_THEMES[key]["label"])),
            str(CLASSROOM_THEMES[key]["description"]),
        )
        for key in FEATURED_CLASSROOM_THEME_KEYS
        if key in CLASSROOM_THEMES
    )


def classroom_theme_render_signature(value: object) -> tuple[object, ...]:
    """Return the structural tokens consumed by the native Qt renderers."""

    theme = classroom_theme(value)
    descriptor_keys = (
        "background_pattern", "bubble_style", "bubble_shape", "bubble_padding",
        "bubble_edge_width", "shadow", "bubble_shadow_width", "bubble_shadow_side",
        "stage_frame", "teacher_stage", "decorative_family", "decorative_layer",
        "texture_family", "texture", "icon_treatment", "chrome", "border_style",
        "panel_radius", "bubble_radius", "control_radius", "background_direction",
        "motion_intensity",
    )
    return tuple(theme.get(key) for key in descriptor_keys)
