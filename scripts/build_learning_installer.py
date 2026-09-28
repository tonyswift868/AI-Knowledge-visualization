"""Build the new UI with the existing Inno/per-user signed update protocol.

freeze: requires only a PUBLIC release key, feed URL and pinned voice catalog.
release: prepare unsigned metadata and content-addressed publish objects.
sign: publisher-only offline Ed25519 signing (private key never copied).
installer: verify the signed release against BOTH compiled authorities, then
compile the existing Inno shell. No legacy GUI payload is required or bundled.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXCLUDES = ["backend", "desktop.main", "desktop.app", "desktop.login_gate",
            "desktop.test_device_profile", "scripts.test_storage_policy", "test_storage_policy",
            "refactor.learning.main_window", "pytest", "_pytest", "unittest", "torch", "transformers",
            "tensorflow", "pandas", "scipy", "matplotlib", "tkinter", "IPython"]


def canonical(value):
    from desktop.update_protocol import canonical as encode
    return encode(value)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(command, work, log):
    env = {**os.environ, "TEMP": str(work), "TMP": str(work), "TMPDIR": str(work),
           "PYTHONDONTWRITEBYTECODE": "1", "PYINSTALLER_CONFIG_DIR": str(work / "pyinstaller-cache")}
    # PyInstaller resolves transitive DLLs through PATH. Unrelated tools may
    # provide same-named ICU/CRT DLLs with incompatible exports; never bundle
    # those ambient dependencies into the signed application.
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join(map(str, (Path(sys.executable).parent,
                                           Path(sys.base_prefix), windows / "System32", windows)))
    with Path(log).open("w", encoding="utf-8") as output:
        subprocess.run(list(map(str, command)), cwd=ROOT, env=env, stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def validate_feed(url, app_id):
    from scripts.install_product import Product
    product = Product(app_id)
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or not parts.path.endswith("/stable/stable.json")):
        raise ValueError("explicit signed stable metadata URL required")
    import ipaddress
    try:
        local = not ipaddress.ip_address(parts.hostname).is_global
    except ValueError:
        local = parts.hostname in {"localhost"} or parts.hostname.endswith(".localhost")
    if not product.uat and (parts.scheme != "https" or local):
        raise ValueError("production releases require a public HTTPS feed")
    return product


def read_key(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    public = base64.b64decode(value["public_key"], validate=True)
    if len(public) != 32 or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value["key_id"]):
        raise ValueError("invalid Ed25519 public key")
    return value["key_id"], public


def include_payload_data(name):
    """Qt wheels contain CMake build intermediates, not runtime resources."""
    parts = name.replace("\\", "/").casefold().split("/")
    return not (any(part in {"objects-debug", "objects-release", "cmakefiles", "tests"} for part in parts)
                or name.lower().endswith((".obj", ".pdb")))


def freeze(args):
    product = validate_feed(args.feed, args.app_id)
    key_id, public = read_key(args.public_key)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise ValueError("numeric release version required")
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if (work / "app/MentorFlow").exists():
        raise ValueError("existing build preserved; use a new work directory")
    if not (ROOT / "refactor/learning/web/dist/index.html").is_file():
        raise ValueError("build the new web UI first")
    from refactor.learning.voice.tts.component_manager import ComponentManager
    manager = ComponentManager(work / "catalog-validation", catalog=args.voice_catalog)
    if manager.simulation and not product.uat:
        raise ValueError("simulated voice components cannot enter a production installer")
    authority = work / "mentorflow_delivery_authority.py"
    authority.write_text(f"APP_ID={args.app_id!r}\nPUBLIC_KEYS={ {key_id: public}!r}\n"
                         f"ONE_CLICK_TEST_FIXTURE={product.uat!r}\nRECOVERY_FEED_URL={args.feed!r}\n"
                         f"RECOVERY_RELEASE_URL={args.feed!r}\n", encoding="utf-8")
    version = {"schema": "mentorflow.version.v1", "product": "MentorFlow", "version": args.version,
               "channel": "stable", "update": {"protocol": "mentorflow.signed-file-release.v1", "enabled": True,
                   "UPDATE_FEED_URL": args.feed, "metadata_url": args.feed,
                   "public_keys": {key_id: base64.b64encode(public).decode()}}}
    (work / "mentorflow-version.json").write_bytes(canonical(version))
    resources = work / "resources"
    shutil.copytree(ROOT / "refactor/desktop/resources", resources)
    if args.public_runtime:
        shutil.copyfile(args.public_runtime, resources / "public-runtime.json")
    if args.account_ca:
        shutil.copyfile(args.account_ca, resources / "trust/account-api.pem")
    (work / "component-catalog.json").write_bytes(args.voice_catalog.read_bytes())
    from PIL import Image
    icon_path = ROOT / "frontend/public/teacher2d/tsubaki-live2d/icon.png"
    with Image.open(icon_path) as icon:
        icon.save(work / "MentorFlow.ico", sizes=[(16,16), (32,32), (48,48), (256,256)])

    base = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
            "--noupx", "--paths", ROOT, "--paths", work, "--hidden-import", "mentorflow_delivery_authority",
            "--specpath", work, "--icon", work / "MentorFlow.ico"]
    runtime = base + ["--name", "UserRuntime", "--distpath", work / "runtime", "--workpath", work / "runtime-build"]
    for module in EXCLUDES + ["PySide6", "numpy", "docling"]:
        runtime += ["--exclude-module", module]
    run(runtime + [ROOT / "scripts/per_user_install.py"], work, work / "runtime-build.log")
    data = [(resources, "refactor/desktop/resources"),
            (ROOT / "refactor/learning/web/dist", "refactor/learning/web/dist"),
            (ROOT / "frontend/public/vendor/live2d", "frontend/public/vendor/live2d"),
            (ROOT / "frontend/public/teacher2d", "frontend/public/teacher2d"),
            (ROOT / "refactor/learning/voice/tts/engine_server.py", "refactor/learning/voice/tts"),
            (work / "component-catalog.json", "refactor/learning/voice/tts"),
            (work / "mentorflow-version.json", ".")]
    spec = work / "MentorFlow.spec"
    spec.write_text("from PyInstaller.utils.hooks import collect_data_files\n"
        "from scripts.build_learning_installer import include_payload_data\n"
        f"data = {[(p.as_posix(), target) for p, target in data]!r}\n"
        "for package in ('sherpa_onnx', 'rapidocr_onnxruntime', 'tzdata'):\n"
        "    data += collect_data_files(package)\n"
        f"a = Analysis({[(ROOT / 'refactor/desktop/packaged_main.py').as_posix()]!r}, "
        f"pathex={[ROOT.as_posix(), work.as_posix()]!r}, datas=data, "
        f"hiddenimports=['mentorflow_delivery_authority', 'keyring.backends.Windows', 'sherpa_onnx'], excludes={EXCLUDES!r})\n"
        "a.datas = [item for item in a.datas if include_payload_data(item[0])]\n"
        "pyz = PYZ(a.pure)\n"
        f"exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MentorFlow', console=False, upx=False, icon={(work / 'MentorFlow.ico').as_posix()!r})\n"
        "coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MentorFlow')\n", encoding="utf-8")
    run([sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--distpath", work / "app",
         "--workpath", work / "app-build", spec], work, work / "app-build.log")
    payload = work / "app/MentorFlow"
    shutil.copytree(work / "runtime/UserRuntime", payload / "UserRuntime")
    shutil.copyfile(work / "mentorflow-version.json", payload / "mentorflow-version.json")
    audit_payload(payload)
    print(json.dumps({"payload": str(payload), "bytes": sum(p.stat().st_size for p in payload.rglob("*") if p.is_file())}))


def audit_payload(payload):
    from PyInstaller.archive.readers import CArchiveReader
    for executable in (payload / "MentorFlow.exe", payload / "UserRuntime/UserRuntime.exe"):
        archive = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz")
        forbidden = [key for key in archive.toc if key == "backend" or key.startswith("backend.")
                     or key in {"desktop.main", "desktop.desktop_api", "desktop.classroom._page", "refactor.learning.main_window"}
                     or key in {"desktop.test_device_profile", "scripts.test_storage_policy", "test_storage_policy"}
                     or key.startswith(("tests.", "pytest", "_pytest"))]
        if forbidden:
            raise ValueError("unexpected legacy/test runtime: " + repr(forbidden))
    for path in payload.rglob("*"):
        parts = {part.casefold() for part in path.relative_to(payload).parts}
        if "tests" in parts or path.suffix.lower() in {".ckpt", ".pth"}:
            raise ValueError("test or model unexpectedly bundled: " + str(path))


def release(args):
    from scripts.prepare_gui_bootstrap_closure import collect_closure
    from scripts.verify_windows_package import build_manifest
    payload = args.payload.resolve()
    version = json.loads((payload / "mentorflow-version.json").read_bytes())
    product = validate_feed(args.feed, args.app_id)
    key_id, _ = read_key(args.public_key)
    check = payload / "SELF-CHECK"
    check.mkdir(exist_ok=True)
    (check / "gui-bootstrap-closure.json").write_bytes(canonical(collect_closure(payload)))
    manifest_path = build_manifest(payload)
    manifest = json.loads(manifest_path.read_bytes())
    manifest_path.write_bytes(canonical(manifest))
    rows = manifest["files"] + [{"path": "SELF-CHECK/package-manifest.json", "size": manifest_path.stat().st_size, "sha256": digest(manifest_path)}]
    origin = args.feed.removesuffix("stable/stable.json")
    objects = args.output / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    assets = []
    for row in rows:
        destination = objects / row["sha256"]
        if not destination.exists():
            shutil.copyfile(payload / row["path"], destination)
        if digest(destination) != row["sha256"]:
            raise ValueError("publish object hash mismatch")
        assets.append({**row, "url": origin + "objects/" + row["sha256"]})
    now = datetime.now(timezone.utc)
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree_sha = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    signed = dict(schema_version=1, app_id=product.app_id, channel="stable", version=version["version"],
                  metadata_version=args.epoch, issued_at=now.isoformat(), published_at=now.isoformat(),
                  expires_at=(now + timedelta(days=args.valid_days)).isoformat(), key_id=key_id,
                  rollout_percentage=100, rollout_seed="mentorflow-new-ui-v1", withdrawn=False,
                  min_supported_version=args.minimum_version, min_updater_version="1.0.0", mandatory=False,
                  mandatory_reason="", release_notes=args.notes, full_release={"format": "file-set-v1", "size": sum(a["size"] for a in assets), "assets": assets},
                  incremental_assets=[row["path"] for row in rows], file_manifest=manifest,
                  hash_algorithm="sha256", signature_algorithm="ed25519", base_versions=args.base_versions,
                  source_sha=source_sha, tree_sha=tree_sha)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "unsigned-release.json").write_bytes(canonical(signed))
    print(str(args.output / "unsigned-release.json"))


def sign(args):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key_id, public = read_key(args.public_key)
    private = Ed25519PrivateKey.from_private_bytes(args.private_key.read_bytes())
    if private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
        raise ValueError("private/public signing key mismatch")
    signed = json.loads(args.unsigned.read_bytes())
    if signed["key_id"] != key_id:
        raise ValueError("release key id mismatch")
    raw = canonical({"signed": signed, "key_id": key_id, "signature": base64.b64encode(private.sign(canonical(signed))).decode()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)


def installer(args):
    from PyInstaller.archive.readers import CArchiveReader
    from scripts.build_signed_per_user_installer import literal_authority
    from desktop import update_protocol
    from scripts.per_user_install import verify_tree
    from scripts.build_inno_installer import collect_payload
    authorities = [literal_authority(CArchiveReader(str(args.payload / name)).open_embedded_archive("PYZ.pyz").extract("mentorflow_delivery_authority"))
                   for name in ("MentorFlow.exe", "UserRuntime/UserRuntime.exe")]
    if authorities[0] != authorities[1]:
        raise ValueError("client/updater authority mismatch")
    authority = authorities[0]
    update_protocol.APP_ID = authority["APP_ID"]
    verified = update_protocol.verify_release(args.release.read_bytes(), authority["PUBLIC_KEYS"], current="0.0.0")
    verify_tree(args.payload, verified)
    audit_payload(args.payload)
    files, _ = collect_payload(args.payload)
    args.work.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("installer output must be empty")
    from PIL import Image
    source = ROOT / "frontend/public/teacher2d/tsubaki-live2d/icon.png"
    with Image.open(source) as icon:
        icon.save(args.work / "MentorFlow.ico", sizes=[(16,16), (32,32), (48,48), (256,256)])
    shutil.copyfile(source, args.work / "MentorFlow.png")
    (args.work / "shell.ini").write_text("[Shell]\nAppId=" + authority["APP_ID"] +
        "\nMode=per_user\nEngine=UserRuntime\nLauncher=Updater\\UserRuntime.exe launch\n", encoding="utf-16")
    options = {"PayloadDir": args.payload.resolve(), "OutputDir": args.output.resolve(), "BuildInput": args.work.resolve(),
               "ProductVersion": verified.version, "PayloadExeSHA256": digest(args.payload / "MentorFlow.exe"),
               "RequiredBytes": sum(size for _, size in files) + 256 * 1024 ** 2,
               "ProductAppId": authority["APP_ID"], "ShortcutName": "MentorFlow 预览版" if ".UAT." in authority["APP_ID"] else "MentorFlow",
               "SignedRelease": args.release.resolve()}
    run([args.compiler, "--no-ide-signtools", *[f"/D{k}={v}" for k, v in options.items()], ROOT / "installer/inno/MentorFlow.iss"],
        args.work.resolve(), args.work / "inno.log")
    print(json.dumps([{ "path": str(p), "size": p.stat().st_size, "sha256": digest(p)} for p in args.output.glob("*.exe")]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("freeze")
    for name in ("work", "public-key", "voice-catalog"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--version", required=True); p.add_argument("--feed", required=True)
    p.add_argument("--app-id", default="MentorFlow")
    p.add_argument("--public-runtime", type=Path); p.add_argument("--account-ca", type=Path)
    p = sub.add_parser("release")
    for name in ("payload", "public-key", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--feed", required=True); p.add_argument("--app-id", default="MentorFlow")
    p.add_argument("--epoch", type=int, required=True); p.add_argument("--minimum-version", required=True)
    p.add_argument("--base-versions", nargs="*", default=[]); p.add_argument("--valid-days", type=int, default=30)
    p.add_argument("--notes", required=True)
    p = sub.add_parser("sign")
    for name in ("unsigned", "public-key", "private-key", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p = sub.add_parser("installer")
    for name in ("payload", "release", "work", "output", "compiler"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    globals()[args.action](args)


if __name__ == "__main__":
    main()
