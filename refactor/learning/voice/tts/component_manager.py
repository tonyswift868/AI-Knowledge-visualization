"""Installer-provisioned, hash-pinned engine + bases + teacher voice catalog.

The signed client bundles metadata only. Official engine/base downloads and
the publisher's voice archive install together atomically, after consent.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..download import ArchiveMember, AssetDownloader, DownloadAsset
from ..storage import save_storage, storage_root

CATALOG = Path(__file__).with_name("component-catalog.json")


class ComponentManager(AssetDownloader):
    def __init__(self, data_root, *, storage=None, catalog=CATALOG):
        self.data_root = Path(data_root)
        self.storage = Path(storage) if storage is not None else storage_root(data_root, "tts")
        document = json.loads(Path(catalog).read_text(encoding="utf-8"))
        if document.get("schema") != "mentorflow.tts-components.v1":
            raise ValueError("朗读组件清单格式无效")
        self.simulation = document.get("simulation") is True
        assets = tuple(DownloadAsset(
            name=row["name"], urls=tuple(row["urls"]), size=row["size"], sha256=row["sha256"],
            install_path=row.get("install_path", row["name"]), archive=row.get("archive", "zip"),
            members=tuple(ArchiveMember(**member) for member in row.get("members", ()))) for row in document["assets"])
        self.component_sizes = {name: 0 for name in ("engine", "base", "voice")}
        present = set()
        for asset in assets:
            paths = [m.install_path for m in asset.members] if asset.archive else [asset.install_path]
            groups = {path.split("/", 1)[0] for path in paths}
            if len(groups) != 1 or not groups <= self.component_sizes.keys():
                raise ValueError("朗读组件安装目录无效")
            group = groups.pop()
            self.component_sizes[group] += asset.size
            present.add(group)
        if present != set(self.component_sizes):
            raise ValueError("朗读组件清单不完整")
        super().__init__(self.storage / "tts/components-v1", assets, keep_partial_on_network_error=True)

    def download(self, progress=None, cancel=None):
        import threading
        result = super().download(progress=progress or (lambda done, total: None),
                                  cancel=cancel if cancel is not None else threading.Event())
        # Publish the location only after the complete installation is verified.
        save_storage(self.data_root, "tts", self.storage)
        return result

    def status(self):
        return {**super().status(), "model_name": "GPT-SoVITS 与弗洛洛朗读组件"}
