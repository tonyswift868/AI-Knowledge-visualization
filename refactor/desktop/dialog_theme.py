"""Qt-dialog styling that matches the new window's two themes (UI-00).

Account dialogs stay native Qt (ACC-02: logic unchanged); only their look follows
web/src/theme.css. Buttons opt into a look with the "variant" property:
primary / danger / ghost; everything else is the plain surface button.
"""
from __future__ import annotations

PALETTES = {
    "sakura": dict(bg="#fbf4f7", card="#ffffff", border="rgba(185, 96, 136, 0.18)", border_strong="rgba(185, 96, 136, 0.32)",
                   text="#35202f", text2="#75596a", text3="#a78b9a", accent="#d65a86", accent2="#b8457a", accent3="#f29bb8",
                   soft="rgba(214, 90, 134, 0.10)", input="#fffafc", danger="#cf4b5b", on_accent="#ffffff"),
    "aurora": dict(bg="#0c0e1c", card="#181b34", border="rgba(170, 176, 255, 0.14)", border_strong="rgba(170, 176, 255, 0.28)",
                   text="#eef0ff", text2="#b3b7de", text3="#7a7fab", accent="#9b8cff", accent2="#7f6dff", accent3="#4fe0cd",
                   soft="rgba(155, 140, 255, 0.14)", input="#1e2240", danger="#ff7a8c", on_accent="#0c0e1c"),
}


def dialog_qss(theme: str) -> str:
    c = PALETTES.get(theme, PALETTES["sakura"])
    return f"""
QDialog {{ background: {c['bg']}; color: {c['text']};
  font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QLabel {{ color: {c['text2']}; background: transparent; }}
QLabel#pageHeading {{ color: {c['text']}; font-size: 26px; font-weight: 800; }}
QFrame#settingsCard {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QFrame#settingsCard QLabel {{ color: {c['text']}; }}
QLineEdit {{ min-height: 34px; padding: 4px 12px; border-radius: 12px; border: 1px solid {c['border_strong']};
  background: {c['input']}; color: {c['text']}; selection-background-color: {c['accent3']}; }}
QLineEdit:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit:disabled {{ color: {c['text3']}; }}
QPushButton {{ min-height: 36px; padding: 4px 16px; border-radius: 12px; border: 1px solid {c['border_strong']};
  background: {c['card']}; color: {c['text']}; font-weight: 600; }}
QPushButton:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['soft']}; }}
QPushButton:disabled {{ color: {c['text3']}; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ border: 0; color: {c['on_accent']};
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['accent3']}, stop:0.45 {c['accent']}, stop:1 {c['accent2']}); }}
QPushButton[variant="primary"]:hover {{ color: {c['on_accent']};
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c['accent']}, stop:1 {c['accent2']}); }}
QPushButton[variant="primary"]:disabled {{ background: {c['soft']}; color: {c['text3']}; }}
QPushButton[variant="danger"] {{ color: {c['danger']}; border-color: {c['border']}; background: transparent; }}
QPushButton[variant="danger"]:hover {{ border-color: {c['danger']}; background: rgba(207, 75, 91, 0.08); }}
QPushButton[variant="ghost"] {{ border: 0; background: transparent; color: {c['text2']}; }}
QPushButton[variant="ghost"]:hover {{ color: {c['accent']}; background: {c['soft']}; }}
QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 8px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def theme_of(widget) -> str:
    """The new window records its theme; any other parent means the default."""
    return getattr(widget, "current_theme", "sakura") if widget is not None else "sakura"


def apply(dialog, theme: str, variants: dict | None = None) -> None:
    for button, variant in (variants or {}).items():
        button.setProperty("variant", variant)
    dialog.setStyleSheet(dialog_qss(theme))
