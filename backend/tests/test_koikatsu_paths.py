from __future__ import annotations

from backend.app import koikatsu_paths


def test_koikatsu_resources_share_one_root() -> None:
    resource_root = koikatsu_paths.KOIKATSU_RESOURCE_ROOT.resolve()
    roots = (
        koikatsu_paths.KKS_RUNTIME_ROOT.resolve(),
        koikatsu_paths.KKS_WEB_RESOURCE_ROOT.resolve(),
        koikatsu_paths.KKS_TEACHER_REGISTRY_ROOT.resolve(),
        koikatsu_paths.TEACHER_AVATAR_REGISTRY_ROOT.resolve(),
        koikatsu_paths.KKS_CARD_PRESET_ROOT.resolve(),
    )
    assert all(root == resource_root or resource_root in root.parents for root in roots)


def test_bundled_web_driver_is_inside_the_unified_resource_root() -> None:
    driver = koikatsu_paths.KKS_WEB_RESOURCE_ROOT / "kks-body-shape-driver.json"
    assert driver.is_file()
    assert driver.resolve().parent == koikatsu_paths.KKS_WEB_RESOURCE_ROOT.resolve()
