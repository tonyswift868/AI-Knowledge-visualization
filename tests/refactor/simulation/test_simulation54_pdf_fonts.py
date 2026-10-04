"""The headless simulator must render real Chinese glyphs, not empty boxes."""
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows offscreen font adapter')
def test_simulation54_offscreen_pdf_has_chinese_and_numeric_glyphs():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont, QFontDatabase, QRawFont
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication.instance() or QApplication([])
    ensure_offscreen_fonts()
    assert 'Microsoft YaHei' in QFontDatabase.families()
    font = QRawFont.fromFont(QFont('Microsoft YaHei', 12))
    assert font.isValid()
    assert all(font.supportsCharacter(ord(char)) for char in '学习报告123')
    before = QFontDatabase.families()
    ensure_offscreen_fonts()
    assert QFontDatabase.families() == before
    assert app is not None
