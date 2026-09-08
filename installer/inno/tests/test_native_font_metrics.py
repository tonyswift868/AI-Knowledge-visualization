"""Offscreen Win32 font metrics at virtual DPI; no window/installer is created.

This exercises native text measurement, not screenshots or live VCL layout.
No display configuration, registry, process DPI-awareness or host theme changes.
"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
UI = (ROOT / 'installer/inno/branded-pages.iss').read_text(encoding='utf-8-sig')
GDI = ctypes.WinDLL('gdi32', use_last_error=True)
USER = ctypes.WinDLL('user32', use_last_error=True)
GDI.CreateCompatibleDC.argtypes = [wintypes.HDC]
GDI.CreateCompatibleDC.restype = wintypes.HDC
GDI.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
GDI.CreateFontW.restype = wintypes.HANDLE
GDI.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
GDI.SelectObject.restype = wintypes.HANDLE
GDI.DeleteObject.argtypes = [wintypes.HANDLE]
GDI.DeleteDC.argtypes = [wintypes.HDC]
USER.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                           ctypes.POINTER(wintypes.RECT), wintypes.UINT]
USER.DrawTextW.restype = ctypes.c_int


def measure(text, point_size, width, dpi, bold=False):
    dc = GDI.CreateCompatibleDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    font = GDI.CreateFontW(-round(point_size*dpi/72), 0, 0, 0, 700 if bold else 400,
                          0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
    if not font:
        GDI.DeleteDC(dc)
        raise ctypes.WinError(ctypes.get_last_error())
    old = GDI.SelectObject(dc, font)
    try:
        box = wintypes.RECT(0, 0, round(width*dpi/96), 0)
        if not USER.DrawTextW(dc, text, len(text), ctypes.byref(box), 0x400 | 0x10 | 0x800):
            raise ctypes.WinError(ctypes.get_last_error())
        return box.right-box.left, box.bottom-box.top
    finally:
        GDI.SelectObject(dc, old)
        GDI.DeleteObject(font)
        GDI.DeleteDC(dc)


class NativeFontMetrics(unittest.TestCase):
    def test_static_copy_fits_at_four_virtual_dpis(self):
        labels = re.findall(r"LabelAt\('([^']+)', \d+, \d+, (\d+), (\d+), (\d+), \w+, (True|False)\)", UI)
        # Empty/dynamic path fields require live acceptance separately.
        self.assertGreaterEqual(len(labels), 5)
        for dpi in (96, 120, 144, 192):
            for text, width, height, size, bold in labels:
                if text == '✓':
                    continue  # This uses Segoe UI Symbol, not the text font.
                with self.subTest(dpi=dpi, text=text):
                    w, h = measure(text, int(size), int(width), dpi, bold == 'True')
                    self.assertLessEqual(w, round(int(width)*dpi/96))
                    self.assertLessEqual(h, round(int(height)*dpi/96))

    def test_page_titles_and_primary_captions_fit(self):
        for dpi in (96, 120, 144, 192):
            for text in ('选择安装位置', '正在安装 MentorFlow', 'MentorFlow 已安装', '空间不足'):
                with self.subTest(dpi=dpi, text=text):
                    w, h = measure(text, 23, 648, dpi, True)
                    self.assertLessEqual(w, round(648*dpi/96))
                    self.assertLessEqual(h, round(42*dpi/96))
            w, h = measure('启动 MentorFlow', 10, 148, dpi, True)
            self.assertLessEqual(w, round(148*dpi/96))
            self.assertLessEqual(h, round(40*dpi/96))


if __name__ == '__main__':
    unittest.main(verbosity=2)
