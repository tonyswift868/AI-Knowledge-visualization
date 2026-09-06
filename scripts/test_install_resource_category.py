"""Fail-closed classification; no filesystem, database or network access."""
import unittest

from scripts.windows_install_trust import category


class ResourceCategoryTests(unittest.TestCase):
    def test_only_canonical_designer_png_is_static(self):
        self.assertEqual(category('_internal/PySide6/qml/QtQuick/Controls/designer/images/button.png'), 'STATIC_RESOURCE')
        self.assertEqual(category('_internal/PySide6/qml/QtQuick/designer/images/button.png'), 'STATIC_RESOURCE')

    def test_aliases_and_behavioral_files_are_critical(self):
        good = '_internal/PySide6/qml/QtQuick/designer/images/button.png'
        candidates = [
            good.replace('/', '\\'), good + '/', good + '.py',
            good.replace('/QtQuick/', '/./QtQuick/'),
            good.replace('/QtQuick/', '/other/../QtQuick/'),
            good.replace('/QtQuick/', '//QtQuick/'),
            good.replace('PySide6', 'pyside6'),
            good.replace('.png', '.PNG'),
            good.replace('.png', '.svg'), good.replace('.png', '.qml'),
            good.replace('.png', '.dll'), good.replace('.png', '.pyd'),
            good.replace('.png', '.json'), 'C:/' + good, '/' + good,
            '_internal/PySide6/qml/designer/images/button.png',
            'Config/network.png', 'MentorFlow.exe',
        ]
        for name in candidates:
            with self.subTest(name=name):
                self.assertEqual(category(name), 'CRITICAL')


if __name__ == '__main__':
    unittest.main()
