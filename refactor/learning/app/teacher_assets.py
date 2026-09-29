"""Read-only, allowlisted Live2D files for the new app's private profile.

Discovery/settings reuse app.live2d_assets. No servers, model substitution,
downloads or account data are involved. The bridge worker only publishes an
immutable resource snapshot; Qt replies and folder selection stay on the UI thread.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import threading
from urllib.parse import quote, unquote

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QUrl
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler
from PySide6.QtWidgets import QFileDialog

from refactor.desktop.web_schemes import register_teacher_stage_scheme
from refactor.learning.app.live2d_assets import inspect_live2d_assets
from refactor.learning.app.live2d_assets import saved_asset_root
from refactor.learning.app.live2d_assets import save_asset_root
from .bridge import BridgeError

register_teacher_stage_scheme()
PUBLIC_ROOT = Path(__file__).resolve().parents[2] / "desktop/resources"
CORE = PUBLIC_ROOT / "vendor/live2d/live2dcubismcore.min.js"
_MIME = {".json": "application/json", ".moc3": "application/octet-stream", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".js": "text/javascript"}
_INSTALL_HELP = "选择已安装的椿模型目录（含 manifest.json），也可选择包含 teacher2d 的资源目录。当前不自动下载；安装后点击重新检查。"


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("invalid resource path")
    if (any(c in value for c in "\\:%?#\x00") or any(ord(c) < 32 for c in value)
            or any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ValueError("invalid resource path")
    return value


def _inside(root: Path, relative: str, suffixes: set[str]) -> Path:
    path = (root / _relative(relative)).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in suffixes or not path.is_file():
        raise ValueError("missing or out-of-root resource")
    return path


def _json(path: Path) -> dict:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("oversized model metadata")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("model metadata must be an object")
    return value


@dataclass(frozen=True)
class Resource:
    path: Path
    root: Path
    mime: str
    content: bytes | None = None

    def safe(self) -> bool:
        # Recheck on each request: a link substituted after discovery must not
        # turn an allowlisted texture into a read of a different local directory.
        resolved = self.path.resolve()
        return resolved == self.path and resolved.is_relative_to(self.root) and resolved.is_file()


class TeacherAssetCatalog:
    def __init__(self, asset_root: Path | None = None, *, core: Path = CORE):
        self.explicit_root = asset_root
        self.core = core.resolve()
        self._files: dict[str, Resource] = {}
        self._lock = threading.Lock()

    def status(self) -> dict:
        files: dict[str, Resource] = {}
        result = {"ready": False, "code": "LIVE2D_ASSET_NOT_FOUND", "message": "老师形象未安装",
                  "teacher_id": "tsubaki-live2d", "manifest": None,
                  "installation": {"method": "teacher.choose_assets", "message": _INSTALL_HELP}}
        try:
            root = Path(self.explicit_root or saved_asset_root() or PUBLIC_ROOT).resolve()
            status = inspect_live2d_assets(root)
            if not status.available:
                result["code"] = status.code
            else:
                manifest_path = status.manifest_path.resolve()
                if not manifest_path.is_relative_to(root):
                    raise ValueError("manifest escapes the selected root")
                manifest = _json(manifest_path)
                if manifest.get("id") != "tsubaki-live2d" or manifest.get("renderer") != "live2d2d":
                    raise ValueError("only the configured Tsubaki model is supported")
                package = manifest_path.parent
                skeleton = status.skeleton_path.resolve()
                if not skeleton.is_relative_to(package):
                    raise ValueError("model escapes its package")
                settings = _json(skeleton)
                refs = settings.get("FileReferences")
                if settings.get("Version") != 3 or not isinstance(refs, dict):
                    raise ValueError("invalid model3 metadata")
                # Do not expose or replay the old generated idle/shake preset.
                refs.pop("Motions", None)
                allowed_refs = {"Moc", "Textures", "Physics", "DisplayInfo", "Expressions", "Pose"}
                if set(refs) - allowed_refs:
                    raise ValueError("unsupported model reference")
                requested = [(refs.get("Moc"), {".moc3"})]
                textures = refs.get("Textures")
                if not isinstance(textures, list) or not 1 <= len(textures) <= 16:
                    raise ValueError("missing model textures")
                requested.extend((name, {".png", ".jpg", ".jpeg", ".webp"}) for name in textures)
                for key in ("Physics", "DisplayInfo", "Pose"):
                    if refs.get(key):
                        requested.append((refs[key], {".json"}))
                expressions = refs.get("Expressions", [])
                if not isinstance(expressions, list) or len(expressions) > 64:
                    raise ValueError("invalid expressions")
                for entry in expressions:
                    if not isinstance(entry, dict):
                        raise ValueError("invalid expression")
                    requested.append((entry.get("File"), {".json"}))
                fingerprint = hashlib.sha256((str(package) + str(manifest_path.stat().st_mtime_ns)
                                              + str(skeleton.stat().st_mtime_ns)).encode()).hexdigest()[:20]
                prefix = f"/live2d/{fingerprint}/"
                for relative, suffixes in requested:
                    path = _inside(skeleton.parent, relative, suffixes)
                    files[prefix + "model/" + relative] = Resource(path, package, _MIME[path.suffix.lower()])
                settings_name = _relative(skeleton.name)
                files[prefix + "model/" + settings_name] = Resource(
                    skeleton, package, "application/json", json.dumps(settings, ensure_ascii=False).encode("utf-8"))
                if not self.core.is_file():
                    raise ValueError("bundled Cubism runtime missing")
                files[prefix + "runtime/core.js"] = Resource(self.core, self.core.parent, "text/javascript")
                base = "mentorflow://assets" + prefix
                result.update(ready=True, code="LIVE2D_ASSET_READY", message="老师形象已就绪",
                              manifest={"id": "tsubaki-live2d", "displayName": "椿", "renderer": "live2d2d",
                                        "skeleton": base + "model/" + quote(settings_name),
                                        "live2d": {"coreScript": base + "runtime/core.js", "textureLod": "single-auto"}})
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
            files = {}
            result.update(code="LIVE2D_ASSET_INVALID", message="老师形象资源不完整或无效，请重新选择模型目录。")
        with self._lock:
            self._files = files
        return result

    def resolve(self, url: QUrl) -> Resource | None:
        if (url.scheme() != "mentorflow" or url.host() != "assets" or url.port() != -1
                or url.userName() or url.password() or url.hasQuery() or url.hasFragment()):
            return None
        try:
            key = unquote(url.path(QUrl.ComponentFormattingOption.FullyEncoded), errors="strict")
            _relative(key.lstrip("/"))
            with self._lock:
                resource = self._files.get(key)
            return resource if resource and resource.safe() else None
        except (OSError, ValueError, RuntimeError):
            return None


class TeacherAssets(QWebEngineUrlSchemeHandler):
    def __init__(self, profile, *, catalog: TeacherAssetCatalog | None = None):
        super().__init__(profile)
        self.catalog = catalog or TeacherAssetCatalog()
        profile.installUrlSchemeHandler(b"mentorflow", self)

    def allows(self, url: QUrl) -> bool:
        return self.catalog.resolve(url) is not None

    def register(self, bridge, window):
        def no_params(params):
            if params:
                raise BridgeError("INVALID_PARAMS", "老师资源接口不接受路径或其他参数。")

        def status(params, ctx):
            no_params(params)
            return self.catalog.status()

        def choose(params, ctx):
            no_params(params)
            selected = QFileDialog.getExistingDirectory(window, "选择已安装的椿模型目录")
            if selected:
                save_asset_root(Path(selected))
                self.catalog.explicit_root = None
            return {"selected": bool(selected)}

        bridge.register("teacher.assets", status)
        bridge.register("teacher.choose_assets", choose, ui=True)

    def requestStarted(self, job):  # noqa: N802 - Qt API
        resource = self.catalog.resolve(job.requestUrl()) if bytes(job.requestMethod()) == b"GET" else None
        if resource is None:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        try:
            data = resource.content if resource.content is not None else resource.path.read_bytes()
            buffer = QBuffer(job)  # Lifetime follows this request, without an unbounded retained buffer list.
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(QByteArray(resource.mime.encode("ascii")), buffer)
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
