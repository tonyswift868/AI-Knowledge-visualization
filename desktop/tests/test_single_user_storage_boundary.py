from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from scripts.test_storage_policy import configured_test_temp_root

import pytest


# Importing desktop.main configures its storage defaults. Keep that import
# explicitly inside the disposable D: scratch area rather than touching a user's
# normal data directory.
pytest.importorskip("PySide6")
os.environ.setdefault(
    "MENTORFLOW_USER_DATA_ROOT",
    str(
        configured_test_temp_root() / 'single-user-boundary-import'
    ),
)
os.environ.setdefault("MENTORFLOW_LOCAL_DATA_MODE", "1")

from desktop.main import _copy_missing_tree  # noqa: E402
import desktop.local_migration as local_migration_module  # noqa: E402


TEST_ROOT = configured_test_temp_root() / 'single-user-boundary'


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction regression requires Windows")
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        pytest.skip(f"junction creation unavailable: {exc}")
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")


def _remove_junction(path: Path) -> None:
    if os.name == "nt" and path.exists():
        try:
            path.rmdir()
        except OSError:
            pass


@pytest.fixture()
def storage_roots() -> tuple[Path, Path]:
    source = Path(os.path.abspath(str(TEST_ROOT / "desktop-storage-source")))
    destination = Path(os.path.abspath(str(TEST_ROOT / "desktop-storage-destination")))
    shutil.rmtree(source, ignore_errors=True)
    shutil.rmtree(destination, ignore_errors=True)
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    try:
        yield source, destination
    finally:
        shutil.rmtree(source, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)


def test_copy_missing_tree_copies_plain_files_and_retains_source(storage_roots: tuple[Path, Path]) -> None:
    source, destination = storage_roots
    (source / "nested").mkdir()
    (source / "nested" / "notes.txt").write_text("local notes", encoding="utf-8")

    _copy_missing_tree(source, destination / "copied")

    assert (destination / "copied" / "nested" / "notes.txt").read_text(encoding="utf-8") == "local notes"
    assert (source / "nested" / "notes.txt").is_file()


def test_copy_missing_tree_rejects_source_link_before_traversal(storage_roots: tuple[Path, Path]) -> None:
    source, destination = storage_roots
    outside = Path(os.path.abspath(str(TEST_ROOT / "desktop-storage-outside")))
    shutil.rmtree(outside, ignore_errors=True)
    outside.mkdir(parents=True)
    (outside / "secret.txt").write_text("must stay outside", encoding="utf-8")
    link = source / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        shutil.rmtree(outside, ignore_errors=True)
        pytest.skip(f"symlink creation unavailable: {exc}")
    try:
        with pytest.raises(RuntimeError, match="符号链接|重解析点|逃逸"):
            _copy_missing_tree(source, destination / "copied")
        assert not (destination / "copied" / "escape").exists()
        assert (outside / "secret.txt").read_text(encoding="utf-8") == "must stay outside"
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_copy_missing_tree_rejects_destination_link(storage_roots: tuple[Path, Path]) -> None:
    source, destination = storage_roots
    (source / "notes.txt").write_text("local notes", encoding="utf-8")
    outside = Path(os.path.abspath(str(TEST_ROOT / "desktop-storage-destination-outside")))
    shutil.rmtree(outside, ignore_errors=True)
    outside.mkdir(parents=True)
    link = destination / "copied"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        shutil.rmtree(outside, ignore_errors=True)
        pytest.skip(f"symlink creation unavailable: {exc}")
    try:
        with pytest.raises(RuntimeError, match="符号链接|重解析点|逃逸"):
            _copy_missing_tree(source, link)
        assert not (outside / "notes.txt").exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_copy_missing_tree_rejects_source_hardlink_before_target_write(
    storage_roots: tuple[Path, Path],
) -> None:
    source, destination = storage_roots
    outside = TEST_ROOT / "desktop-storage-hardlink-outside.txt"
    outside.unlink(missing_ok=True)
    outside.write_text("outside", encoding="utf-8")
    (source / "hardlink.txt").unlink(missing_ok=True)
    try:
        os.link(outside, source / "hardlink.txt")
    except OSError as exc:
        outside.unlink(missing_ok=True)
        pytest.skip(f"hardlink creation unavailable: {exc}")
    try:
        with pytest.raises(RuntimeError, match="硬链接"):
            _copy_missing_tree(source, destination / "copied")
        assert not (destination / "copied").exists()
        assert outside.read_text(encoding="utf-8") == "outside"
    finally:
        outside.unlink(missing_ok=True)


def test_copy_missing_tree_rejects_nested_junction_before_target_write(
    storage_roots: tuple[Path, Path],
) -> None:
    source, destination = storage_roots
    outside = TEST_ROOT / "desktop-storage-junction-outside"
    shutil.rmtree(outside, ignore_errors=True)
    outside.mkdir(parents=True)
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    junction = source / "escape"
    _make_junction(junction, outside)
    try:
        with pytest.raises(RuntimeError, match="符号链接|重解析点|逃逸"):
            _copy_missing_tree(source, destination / "copied")
        assert not (destination / "copied").exists()
        assert (outside / "secret.txt").read_text(encoding="utf-8") == "outside"
    finally:
        _remove_junction(junction)
        shutil.rmtree(outside, ignore_errors=True)


def test_copy_missing_tree_destination_hardlink_swap_cannot_overwrite_outside(
    storage_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = storage_roots
    (source / "notes.txt").write_text("inside", encoding="utf-8")
    outside = TEST_ROOT / "desktop-storage-race-hardlink-outside.txt"
    outside.unlink(missing_ok=True)
    outside.write_text("must stay outside", encoding="utf-8")
    target = destination / "copied" / "notes.txt"
    target.parent.mkdir()
    native_open = local_migration_module._win_open_handle
    swapped = False

    def install_hardlink_once(path: Path, **kwargs):
        nonlocal swapped
        if Path(path) == target and kwargs.get("creation") == local_migration_module._WIN_CREATE_NEW:
            os.link(outside, target)
            swapped = True
        return native_open(path, **kwargs)

    monkeypatch.setattr(local_migration_module, "_win_open_handle", install_hardlink_once)
    try:
        with pytest.raises(RuntimeError, match="安全打开|硬链接|文件已存在"):
            _copy_missing_tree(source, destination / "copied")
        assert swapped
        assert outside.read_text(encoding="utf-8") == "must stay outside"
    finally:
        target.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_copy_missing_tree_destination_junction_swap_cannot_write_outside(
    storage_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = storage_roots
    (source / "notes.txt").write_text("inside", encoding="utf-8")
    outside = TEST_ROOT / "desktop-storage-race-junction-outside"
    shutil.rmtree(outside, ignore_errors=True)
    outside.mkdir(parents=True)
    (outside / "sentinel.txt").write_text("must stay outside", encoding="utf-8")
    target = destination / "copied" / "notes.txt"
    target.parent.mkdir()
    native_open = local_migration_module._win_open_handle
    swapped = False

    def install_junction_once(path: Path, **kwargs):
        nonlocal swapped
        if Path(path) == target and kwargs.get("creation") == local_migration_module._WIN_CREATE_NEW:
            _make_junction(target, outside)
            swapped = True
        return native_open(path, **kwargs)

    monkeypatch.setattr(local_migration_module, "_win_open_handle", install_junction_once)
    try:
        with pytest.raises(RuntimeError, match="安全打开|重解析点|文件已存在"):
            _copy_missing_tree(source, destination / "copied")
        assert swapped
        assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "must stay outside"
    finally:
        _remove_junction(target)
        shutil.rmtree(outside, ignore_errors=True)
