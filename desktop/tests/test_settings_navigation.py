from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from desktop.main_window import MainWindow  # noqa: E402
from desktop.account_portability import PackageMode  # noqa: E402
from desktop.system_page import SystemPage  # noqa: E402


class _NavigationHost:
    pass


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def navigation_host(qapp: QApplication) -> tuple[object, QListWidget, QStackedWidget]:
    navigation = QListWidget()
    navigation.addItems(
        ["概览", "我的计划", "资料管理", "原生课堂", "创建计划", "模型连接", "设置", "更多工具"]
    )
    pages = QStackedWidget()
    for _ in range(11):
        pages.addWidget(QWidget())

    host = _NavigationHost()
    from types import SimpleNamespace
    from unittest.mock import Mock
    host.materials_page = SimpleNamespace(refresh_workspaces=Mock())
    host.navigation = navigation
    host.pages = pages
    host._navigation_page_indices = [0, 1, 3, 5, 6, 8, 9, 10]
    navigation.setCurrentRow(0)
    navigation.currentRowChanged.connect(lambda index: MainWindow._navigate(host, index))
    qapp.processEvents()
    return host, navigation, pages


def _provider_button(page: SystemPage) -> QPushButton:
    return next(
        button
        for button in page.findChildren(QPushButton)
        if button.text() == "打开模型连接"
    )


@pytest.mark.parametrize("activation", ["mouse", "space"])
def test_settings_model_connection_button_activates_provider_page(
    activation: str,
    qapp: QApplication,
    navigation_host: tuple[object, QListWidget, QStackedWidget],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    host, navigation, pages = navigation_host
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))

    # Keep this focused UI test independent of the user's local databases.
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    page = SystemPage(
        {},
        "http://127.0.0.1:0",
        lambda: None,
        object(),
        lambda section: MainWindow._open_settings_section(host, section),
    )
    page.show()
    qapp.processEvents()
    button = _provider_button(page)
    try:
        if activation == "mouse":
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        else:
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Space)
        qapp.processEvents()
        assert navigation.currentRow() == 5
        assert pages.currentIndex() == 8
    finally:
        page.close()


def test_settings_classroom_theme_gallery_is_structural_and_resettable(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("desktop.system_page.QSettings", lambda *_args, **_kwargs: settings)
    changes: list[str] = []
    page = SystemPage({}, "http://127.0.0.1:0", lambda: None, object(), classroom_theme_changed=changes.append)
    try:
        cards = page.findChildren(QFrame, "classroomThemeCard")
        assert len(cards) == 4
        assert {str(card.property("themeKey")) for card in cards} == {
            "sakura", "after_school", "rainy_window", "cyber"
        }
        apply_button = next(
            button for button in page.findChildren(QPushButton)
            if button.accessibleName() == "应用课堂皮肤：赛博学院"
        )
        QTest.mouseClick(apply_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert settings.value("appearance/classroom_theme") == "cyber"
        assert changes[-1:] == ["cyber"]
        reset = next(button for button in page.findChildren(QPushButton) if button.text() == "重置为樱花教室")
        QTest.mouseClick(reset, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert settings.value("appearance/classroom_theme") == "sakura"
        assert changes[-1:] == ["sakura"]
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_settings_sidebar_row_still_uses_settings_page(
    qapp: QApplication,
    navigation_host: tuple[object, QListWidget, QStackedWidget],
) -> None:
    _host, navigation, pages = navigation_host
    navigation.setCurrentRow(6)
    qapp.processEvents()
    assert pages.currentIndex() == 9


def test_settings_portable_data_section_exposes_quick_full_import_and_exclusions(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    page = SystemPage(
        {
            "issuer": "https://identity.example.test/realms/mentorflow",
            "subject": "learner-1",
        },
        "http://127.0.0.1:0",
        lambda: None,
        object(),
    )
    try:
        button_texts = {button.text() for button in page.findChildren(QPushButton)}
        assert {
            "Quick Account Export",
            "Full Portable Export",
            "在干净账号 Vault 导入",
        } <= button_texts
        labels = "\n".join(label.text() for label in page.findChildren(QLabel))
        for visible_exclusion in (
            "模型权重",
            "access/refresh token",
            "Provider 密钥",
            "绝对路径",
            "其他账号 Vault",
            "Linked 文件",
        ):
            assert visible_exclusion in labels
        personal = next(
            checkbox
            for checkbox in page.findChildren(QCheckBox)
            if "Personal Local" in checkbox.text()
        )
        assert personal.isChecked() is False
        assert page.data_root == tmp_path.resolve()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_settings_portable_actions_bind_current_account_without_switching_vault(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "current-vault"
    root.mkdir()
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(root))
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    identity = {
        "issuer": "https://identity.example.test/realms/mentorflow",
        "subject": "learner-1",
    }
    page = SystemPage(identity, "http://127.0.0.1:0", lambda: None, object())
    calls: list[tuple[str, dict]] = []

    def fake_start(kind, work):
        calls.append((kind, work()))
        return True

    page._start_data_action = fake_start  # type: ignore[method-assign]
    monkeypatch.setattr(
        "desktop.system_page.discover_portability_catalogs",
        lambda _root: {
            "structured_snapshot": {"plans": []},
            "asset_manifests": [],
            "model_manifests": [],
        },
    )
    export_calls: list[dict] = []

    def fake_export(_root, destination, **kwargs):
        export_calls.append({"root": _root, "destination": destination, **kwargs})
        return {
            "archive": destination,
            "package_format_version": 1,
            "members": 6,
            "raw_assets": 0,
        }

    monkeypatch.setattr("desktop.system_page.export_account_package", fake_export)
    destinations = iter(
        [
            str(tmp_path / "quick.mentorflowpack"),
            str(tmp_path / "full.mentorflowpack"),
        ]
    )
    monkeypatch.setattr(
        "desktop.system_page.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (next(destinations), ""),
    )
    try:
        page.export_migration()
        page.export_full_portable()
        assert [call["mode"] for call in export_calls] == [
            PackageMode.QUICK,
            PackageMode.FULL,
        ]
        assert all(call["root"] == root.resolve() for call in export_calls)
        assert all(call["account_identity"] == identity for call in export_calls)
        assert [kind for kind, _result in calls] == ["quick_export", "full_export"]

        package = tmp_path / "quick.mentorflowpack"
        package.write_bytes(b"test package selection")
        target = tmp_path / "new-vault"
        target.mkdir()
        import_calls: list[dict] = []

        def fake_import(archive, target_root, **kwargs):
            import_calls.append(
                {"archive": archive, "target": target_root, **kwargs}
            )
            return {
                "target": target_root,
                "package_mode": "QUICK_ACCOUNT_EXPORT",
                "raw_assets_restored": 0,
                "duplicate": False,
            }

        monkeypatch.setattr("desktop.system_page.import_account_package", fake_import)
        monkeypatch.setattr(
            "desktop.system_page.QFileDialog.getOpenFileName",
            lambda *_args, **_kwargs: (str(package), ""),
        )
        monkeypatch.setattr(
            "desktop.system_page.QFileDialog.getExistingDirectory",
            lambda *_args, **_kwargs: str(target),
        )
        monkeypatch.setattr(
            "desktop.system_page.QMessageBox.question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        page.import_migration()
        assert calls[-1][0] == "portable_import"
        assert import_calls == [
            {
                "archive": package,
                "target": target,
                "expected_account_identity": identity,
                "allow_personal_local": False,
            }
        ]
        assert "Vault 未切换" not in page.migration_status.text()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    ("section", "expected_row", "expected_page"),
    [("resources", 2, 3), ("data", 6, 9), ("unknown", 6, 9)],
)
def test_settings_section_routes_follow_current_sidebar_mapping(
    section: str,
    expected_row: int,
    expected_page: int,
    qapp: QApplication,
    navigation_host: tuple[object, QListWidget, QStackedWidget],
) -> None:
    host, navigation, pages = navigation_host
    MainWindow._open_settings_section(host, section)
    qapp.processEvents()
    assert navigation.currentRow() == expected_row
    assert pages.currentIndex() == expected_page


def test_source_mode_data_root_matches_repository_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MENTORFLOW_USER_DATA_ROOT", "MENTORFLOW_DB", "MENTORFLOW_ORM_DB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert SystemPage._resolve_data_root() == Path(__file__).resolve().parents[2]


def test_settings_classroom_skin_defaults_to_sakura_and_updates_live_without_export(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("desktop.system_page.QSettings", lambda *_args, **_kwargs: settings)
    changes: list[str] = []
    page = SystemPage(
        {},
        "http://127.0.0.1:0",
        lambda: None,
        object(),
        classroom_theme_changed=changes.append,
    )
    try:
        combo = page.findChild(QComboBox, "")
        assert combo is page.classroom_theme_combo
        assert combo.currentData() == "sakura"
        assert combo.count() == 4
        assert set(page.classroom_theme_cards) == {
            "sakura", "after_school", "rainy_window", "cyber"
        }
        assert settings.value("appearance/classroom_theme") is None

        cyber_index = combo.findData("cyber")
        combo.setCurrentIndex(cyber_index)
        qapp.processEvents()
        assert changes == ["cyber"]
        assert settings.value("appearance/classroom_theme") is None
        assert page.classroom_theme_cancel_button.isEnabled()
        QTest.mouseClick(page.classroom_theme_cancel_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert combo.currentData() == "sakura"
        # A new-device migration snapshot must not silently carry a personal
        # visual choice; the recipient starts from the product default.
        from desktop.system_page import _safe_qsettings_snapshot

        assert "appearance/classroom_theme" not in _safe_qsettings_snapshot()
    finally:
        page.close()


def test_free_chat_prompt_is_local_editable_clearable_and_not_exported(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MENTORFLOW_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("desktop.system_page.diagnostics_text", lambda *_args, **_kwargs: "ok")
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr("desktop.system_page.QSettings", lambda *_args, **_kwargs: settings)
    page = SystemPage({}, "http://127.0.0.1:0", lambda: None, object())
    try:
        prompt = "像老朋友一样自然聊天，不添加产品话题规则。"
        page.free_chat_prompt.setPlainText(prompt)
        page._save_free_chat_prompt()
        assert settings.value("classroom/free_chat_prompt") == prompt
        assert "已保存" in page.free_chat_prompt_status.text()

        from desktop.system_page import _safe_qsettings_snapshot

        assert "classroom/free_chat_prompt" not in _safe_qsettings_snapshot()
        page._clear_free_chat_prompt()
        assert page.free_chat_prompt.toPlainText() == ""
        assert settings.value("classroom/free_chat_prompt") is None
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()
