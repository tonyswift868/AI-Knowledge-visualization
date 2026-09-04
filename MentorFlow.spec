import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


root = Path.cwd()
backend_root = root / "backend"
# Desktop runtime imports the canonical backend namespace as ``app`` after
# selecting the account-scoped data root.  Make that same namespace visible
# while PyInstaller enumerates hidden imports; collecting ``backend.app``
# creates a second, unusable module identity and misses dynamic ``app.*``
# imports such as the cost boundaries.
sys.path.insert(0, str(backend_root))
datas = [
    (str(root / "backend" / "alembic" / "env.py"), "backend/alembic"),
    (str(root / "backend" / "alembic" / "script.py.mako"), "backend/alembic"),
    (str(root / "backend" / "alembic.ini"), "backend"),
    # Only checked-in, non-secret runtime configuration belongs in a release.
    # Never replace these entries with the whole config tree: local voice
    # credentials are intentionally ignored by git but may still exist here.
    (str(root / "backend" / "config" / "knowledge_policy_v1.json"), "backend/config"),
    (str(root / "backend" / "config" / "prompts" / "casual_chat_teacher_prompt.md"), "backend/config/prompts"),
    (str(root / "backend" / "config" / "voice" / "qwen3_tts_runtime.json"), "backend/config/voice"),
    (str(root / "backend" / "config" / "voice" / "release_trust_roots.v1.json"), "backend/config/voice"),
    (str(root / "backend" / "config" / "voice" / "teacher_voice_pack_catalog.v1.json"), "backend/config/voice"),
    (str(root / "backend" / "config" / "voice" / "teacher_voice_release_slot.v1.json"), "backend/config/voice"),
    # The card preset manifest and its three tiny parameter-only cards are
    # optional runtime resources.  Keep the package scoped to this directory;
    # the full Koikatsu game tree and external assets must never be bundled.
    (str(root / "legacy" / "koikatsu-3d" / "resources" / "card-presets" / "1.0.0" / "manifest.json"), "legacy/koikatsu-3d/resources/card-presets/1.0.0"),
    (str(root / "legacy" / "koikatsu-3d" / "resources" / "card-presets" / "1.0.0" / "mentorflow-balanced-default.json"), "legacy/koikatsu-3d/resources/card-presets/1.0.0"),
    (str(root / "legacy" / "koikatsu-3d" / "resources" / "card-presets" / "1.0.0" / "kks-br-chan-maki.json"), "legacy/koikatsu-3d/resources/card-presets/1.0.0"),
    (str(root / "legacy" / "koikatsu-3d" / "resources" / "card-presets" / "1.0.0" / "kks-br-chan-nozomu.json"), "legacy/koikatsu-3d/resources/card-presets/1.0.0"),
    # Public, tracked content is listed by pack/version.  In particular, do
    # not collect ``content`` or ``story_packs`` recursively: those roots are
    # also used for ignored development fixtures and local user exports.
    (str(root / "content" / "knowledge_cards" / "offline_facts.v1.json"), "content/knowledge_cards"),
    (str(root / "content" / "expedition_art_packs" / "mentorflow_expedition_art_v1" / "1.0.0"), "content/expedition_art_packs/mentorflow_expedition_art_v1/1.0.0"),
    (str(root / "content" / "expedition_art_packs" / "mentorflow_expedition_art_v1" / "1.1.0"), "content/expedition_art_packs/mentorflow_expedition_art_v1/1.1.0"),
    (str(root / "story_packs" / "reference_echo_before_rain"), "story_packs/reference_echo_before_rain"),
    (str(root / "docs" / "distribution-readme.txt"), "."),
    (str(root / "mentorflow-version.json"), "."),
    (str(root / "docs" / "knowledge-map-v1-third-party.md"), "docs"),
    (str(root / "docs" / "knowledge-map-v1-sbom.json"), "docs"),
]


def add_frontend_tree(source_root: Path, destination_root: str) -> None:
    """Add frontend assets while keeping private local models out of releases."""

    include_private = os.getenv("MENTORFLOW_INCLUDE_PRIVATE_TEACHER_ASSETS", "").strip() == "1"
    private_subtree = Path("teacher2d/tsubaki-live2d")
    for candidate in sorted(source_root.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(source_root)
        if not include_private and (relative == private_subtree or private_subtree in relative.parents):
            continue
        destination = Path(destination_root) / relative.parent
        datas.append((str(candidate), destination.as_posix()))


# ``frontend/dist`` is the runtime web root.  ``frontend/public`` remains for
# native-shell assets that are accessed directly in source/private builds.
# Public builds omit the unlicensed local Live2D subtree from both copies.
add_frontend_tree(root / "frontend" / "dist", "frontend/dist")
add_frontend_tree(root / "frontend" / "public", "frontend/public")
# The catalogue is public, but its source tree may contain local build notes or
# ignored generated files.  Keep the release input list file-level and stable.
for catalog_relative in (
    "CANDIDATE_SELECTION_METHODOLOGY.md", "CATALOG_INTERFACE_SPEC.md",
    "CODEX_INTEGRATION_TASK.md", "README.md", "SHA256SUMS.json",
    "SOURCE_AND_LICENSE_NOTES.md", "build_provenance.json",
    "builtin_catalog_overlay.json", "candidate_index_9959.jsonl",
    "candidate_index_schema_v2.json", "catalog_manifest.json",
    "catalog_stats.json", "curated_dna_cards_41.jsonl",
    "curation_queue_top500.jsonl", "integrity_report.json",
    "narrative_catalog_v2.sqlite", "source_connector_specs_v2.json",
    "story_catalog_api_v2.openapi.yaml", "unified_story_catalog_10000.jsonl",
    "unified_story_catalog_10000_compact.csv",
    "legacy_v1_seed/COLLECTION_METHODOLOGY.md",
    "legacy_v1_seed/NARRATIVE_DNA_LIBRARY_V1.md",
    "legacy_v1_seed/README_FOR_CODEX.md", "legacy_v1_seed/SHA256SUMS.json",
    "legacy_v1_seed/learning_narrative_model_v1.json",
    "legacy_v1_seed/leetcode150_match_example.json",
    "legacy_v1_seed/library_manifest.json", "legacy_v1_seed/matcher_rules_v1.json",
    "legacy_v1_seed/narrative_dna_families_v1.json",
    "legacy_v1_seed/narrative_dna_library_v1.jsonl",
    "legacy_v1_seed/narrative_dna_schema_v1.json", "legacy_v1_seed/sources.csv",
):
    catalog_path = root / "content" / "narrative_catalog" / "v2" / catalog_relative
    datas.append((str(catalog_path), f"content/narrative_catalog/v2/{catalog_path.parent.name}" if catalog_path.parent.name == "legacy_v1_seed" else "content/narrative_catalog/v2"))
# Alembic migration files are public runtime inputs, but a checkout may have
# ignored ``__pycache__`` entries.  Add only the tracked migration sources.
for migration in sorted((root / "backend" / "alembic" / "versions").glob("*.py")):
    datas.append((str(migration), "backend/alembic/versions"))
# ``desktop.runtime`` activates an account-scoped data root before importing
# ``app.main`` and deliberately exposes ``_internal/backend`` as the canonical
# import root.  Keep the tracked application sources at that runtime location;
# hidden imports in the PYZ alone are not discoverable through this boundary in
# the frozen process.
for app_source in sorted((backend_root / "app").rglob("*.py")):
    relative_parent = app_source.relative_to(backend_root).parent
    destination = Path("backend") / relative_parent
    datas.append((str(app_source), destination.as_posix()))

# OR-Tools native extensions depend on the DLLs shipped in ``ortools/.libs``.
# Preserve that wheel layout so its existing fail-closed preloader can resolve
# the exact bundled libraries before CP-SAT is imported.
binaries = collect_dynamic_libs("ortools")
hiddenimports = collect_submodules("app") + collect_submodules("keyring.backends")

for package in ("docling", "rapidocr", "instructor", "yt_dlp"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for package in ("keyring", "argon2-cffi", "docling", "instructor", "yt-dlp"):
    try:
        datas += copy_metadata(package, recursive=True)
    except Exception:
        pass

a = Analysis(
    [str(root / "desktop_entry.py")],
    pathex=[str(root), str(backend_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "playwright"],
    noarchive=False,
)

# Qt 6 on supported Windows releases resolves the platform ICU shim from
# System32.  Some optional document dependencies can make PyInstaller collect
# an unrelated versioned ICU pair at the package root under the same generic
# ``icuuc.dll`` name.  That file wins DLL search order and makes QtCore fail
# with ERROR_PROC_NOT_FOUND before the Desktop UI can start.  No packaged
# extension imports the versioned data DLL directly; keep the conflicting
# pair out of the root so Qt loads the Windows platform shim it was built for.
_conflicting_root_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry
    for entry in a.binaries
    if not (
        Path(str(entry[0])).parent == Path(".")
        and Path(str(entry[0])).name.casefold() in _conflicting_root_icu
    )
]
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
