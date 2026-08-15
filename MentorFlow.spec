from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


root = Path.cwd()
datas = [
    (str(root / "backend" / "alembic"), "backend/alembic"),
    (str(root / "backend" / "alembic.ini"), "backend"),
    (str(root / "backend" / "config"), "backend/config"),
    (str(root / "frontend" / "dist"), "frontend/dist"),
    (str(root / "frontend" / "public"), "frontend/public"),
    (str(root / "data" / "presets"), "data/presets"),
    (str(root / "content"), "content"),
    (str(root / "story_packs"), "story_packs"),
    (str(root / "docs" / "distribution-readme.txt"), "."),
    (str(root / "mentorflow-version.json"), "."),
    (str(root / "docs" / "knowledge-map-v1-third-party.md"), "docs"),
    (str(root / "docs" / "knowledge-map-v1-sbom.json"), "docs"),
]
binaries = []
hiddenimports = collect_submodules("backend.app") + collect_submodules("keyring.backends")

for package in ("docling", "rapidocr", "instructor"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for package in ("keyring", "argon2-cffi", "docling", "instructor"):
    try:
        datas += copy_metadata(package, recursive=True)
    except Exception:
        pass

a = Analysis(
    [str(root / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MentorFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / "release" / "native" / "MentorFlow.ico"),
)

# Keep the package verifier/update checker as a small standalone console
# executable.  It intentionally has no application imports or user-data
# dependencies, so shipped BAT entrypoints do not require Python to be
# installed.  The source .py remains in SELF-CHECK for auditable fallback and
# review, while this one-file EXE is what ordinary users run.
selfcheck_a = Analysis(
    [str(root / "scripts" / "verify_windows_package.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
selfcheck_pyz = PYZ(selfcheck_a.pure)
selfcheck_exe = EXE(
    selfcheck_pyz,
    selfcheck_a.scripts,
    selfcheck_a.binaries,
    selfcheck_a.datas,
    [],
    name="MentorFlowSelfCheck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    selfcheck_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MentorFlow",
)
