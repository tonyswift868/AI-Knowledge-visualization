"""Regression for a regular settings save overwriting a concurrent teacher-preset edit."""
import threading

from refactor.learning.app.settings import AppSettings
from refactor.learning.course.store import CourseStore


def test_theme_save_does_not_replace_concurrently_saved_teacher_presets(tmp_path):
    account = "preset-race-228"
    store = CourseStore(account, tmp_path / "data")
    settings = AppSettings(account, tmp_path / "data", store_provider=lambda: store)
    try:
        before = settings.load()
        old = before["teacher_presets"][0]
        updated = dict(old, persona="新保存的老师偏好")
        new_group = dict(presets=[updated], default_id=before["default_teacher_preset_id"])

        theme_reached_mirror = threading.Event()
        release_theme = threading.Event()
        preset_done = threading.Event()
        errors = []
        save_setting = store.save_account_setting

        def pause_stale_mirror(identifier, value):
            if threading.current_thread().name == "theme-save" and identifier == "teacher_presets":
                theme_reached_mirror.set()
                assert release_theme.wait(4), "test did not release the paused theme save"
            return save_setting(identifier, value)

        store.save_account_setting = pause_stale_mirror

        def theme_save():
            try:
                settings.save({"theme": "aurora"})
            except Exception as exc:  # surfaced below with thread context
                errors.append(exc)

        def preset_save():
            try:
                settings.save_teacher_presets(new_group["presets"], new_group["default_id"], store)
            except Exception as exc:
                errors.append(exc)
            finally:
                preset_done.set()

        theme_thread = threading.Thread(target=theme_save, name="theme-save")
        preset_thread = None
        theme_thread.start()
        try:
            if theme_reached_mirror.wait(3):
                # The theme worker has already read the old preset group. Let
                # the preset transaction commit while its stale mirror waits.
                preset_thread = threading.Thread(target=preset_save, name="preset-save")
                preset_thread.start()
                preset_done.wait(0.25)
            else:
                # A fixed implementation may avoid mirroring presets on an
                # unrelated theme patch. Wait for that path to finish before
                # saving the preset, which must then remain authoritative.
                theme_thread.join(4)
            release_theme.set()
            theme_thread.join(4)
            if preset_thread is not None:
                preset_thread.join(4)
            elif not theme_thread.is_alive():
                preset_save()
        finally:
            release_theme.set()
            theme_thread.join(4)
            if preset_thread is not None:
                preset_thread.join(4)

        assert not theme_thread.is_alive() and (preset_thread is None or not preset_thread.is_alive())
        assert not errors, errors
        saved_group = store.load_account_setting("teacher_presets")
        assert saved_group == new_group
        assert settings.load()["teacher_presets"] == [updated]
    finally:
        store.close()
