import os
import hashlib
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


root = Path.cwd()
uat_config = os.getenv('MENTORFLOW_PACKAGED_UPDATE_UAT_CONFIG', '')
if uat_config:
    import json
    uat_path = Path(uat_config).absolute()
    if not uat_path.is_relative_to(Path('D:/CodexScratch/MentorFlow')):
        raise ValueError('UAT public build configuration must be in isolated scratch')
    uat_doc = json.loads(uat_path.read_bytes())
    if set(uat_doc) != {'schema', 'root', 'public_key', 'key_id', 'source_sha', 'tree_sha'} or uat_doc['schema'] != 'mentorflow.packaged-update-uat.v1':
        raise ValueError('invalid UAT public build configuration')
backend_root = root / "backend"
account_ui = root / 'artifacts/account-lifecycle-ui'
# Build-time module search is declared through Analysis.pathex below.
# Enumerate canonical names without mutating the build interpreter's sys.path.
backend_modules = ["app"]
for source in sorted((backend_root / "app").rglob("*.py")):
    parts = source.relative_to(backend_root).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    for length in range(1, len(parts) + 1):
        backend_modules.append(".".join(parts[:length]))

datas = [
    # source-inputs.json is build provenance, never runtime/user payload.
    *[(str(path), str(Path('account-lifecycle') / path.relative_to(account_ui).parent))
      for path in sorted(account_ui.rglob('*')) if path.is_file() and path.name != 'source-inputs.json'],
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
# ignored ``__pycache__`` entries. Add only the tracked migration sources.
for migration in sorted((root / "backend" / "alembic" / "versions").glob("*.py")):
    datas.append((str(migration), "backend/alembic/versions"))
# Preserve existing file resources for consumers such as migrations. Executable
# backend imports use canonical app.* in PYZ, never this backend resource tree.
for app_source in sorted((backend_root / "app").rglob("*.py")):
    relative_parent = app_source.relative_to(backend_root).parent
    destination = Path("backend") / relative_parent
    datas.append((str(app_source), destination.as_posix()))

# OR-Tools native extensions depend on the DLLs shipped in ``ortools/.libs``.
# Preserve that wheel layout so its existing fail-closed preloader can resolve
# the exact bundled libraries before CP-SAT is imported.
binaries = collect_dynamic_libs("ortools")
hiddenimports = sorted(set(backend_modules)) + collect_submodules("app") + collect_submodules("keyring.backends")

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

if uat_config:
    datas.append((str(uat_path), 'update-uat'))
    hiddenimports += ['scripts.windows_update_apply']

a = Analysis(
    [str(root / ('desktop_update_uat_entry.py' if uat_config else "desktop_gui_entry.py" if os.getenv("MENTORFLOW_BUILD_PROTECTED_GUI") == "1" else "desktop_entry.py"))],
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
# The root-destination contract is equivalent to
# ``Path(str(entry[0])).parent == Path(".")``; entry[0] is authoritative.
def _forbidden_dest(entry):
    return Path(str(entry[0])).name.casefold() in _conflicting_root_icu

def _filter_forbidden_dest(entries, producer):
    removed = [entry for entry in entries if _forbidden_dest(entry)]
    for entry in removed:
        print(f"REMOVED_FORBIDDEN_ICU producer={producer} dest={entry[0]} src={entry[1]} type={entry[2]}")
    return [entry for entry in entries if not _forbidden_dest(entry)]

_removed_conflicting_icu = [
    entry for entry in a.binaries
    if Path(str(entry[0])).name.casefold() in _conflicting_root_icu
]
for _entry in _removed_conflicting_icu:
    print(f"REMOVED_FORBIDDEN_ICU dest={_entry[0]} src={_entry[1]} type={_entry[2]}")
a.binaries = [
    entry for entry in a.binaries
    if Path(str(entry[0])).name.casefold() not in _conflicting_root_icu
]
_forbidden_icu_dest_count = sum(
    Path(str(entry[0])).name.casefold() == "icuuc.dll" for entry in a.binaries
)
assert _forbidden_icu_dest_count == 0, (
    f"FORBIDDEN_ICU_DEST_COUNT={_forbidden_icu_dest_count}"
)
pyz = PYZ(a.pure)

# Some wheel license trees exceed MAX_PATH even inside the prescribed D: build
# root. Preserve every license byte under a deterministic, bounded destination;
# these are attribution data, not importable/runtime resources. Do not omit the
# dependency or its licenses to make COLLECT succeed.
a.datas = [
    (str(Path(Path(entry[0]).parts[0]) / 'licenses' /
         (hashlib.sha256(str(entry[0]).encode('utf-8')).hexdigest() + '.txt')),
     *entry[1:])
    if '.dist-info' in Path(entry[0]).parts[0] and
       'licenses' in Path(entry[0]).parts and len(str(entry[0])) > 140
    else entry
    for entry in a.datas
]

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

install_gate_a = Analysis(
    [str(root / "scripts" / "run_windows_install_gate.py")],
    pathex=[str(root)], binaries=[], datas=[], hiddenimports=[], hookspath=[],
    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
install_gate_pyz = PYZ(install_gate_a.pure)
install_gate_exe = EXE(
    install_gate_pyz, install_gate_a.scripts, [],
    exclude_binaries=True, name="MentorFlowInstallGate", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=True, console=False,
    uac_admin=False,
)

# The self-check executable shares the final COLLECT directory; apply the
# same destination-level ICU guard to its collected binaries as well.
selfcheck_a.binaries = [
    entry for entry in selfcheck_a.binaries
    if Path(str(entry[0])).name.casefold() not in _conflicting_root_icu
]
install_gate_a.binaries = _filter_forbidden_dest(install_gate_a.binaries, "install_gate_a.binaries")
_pre_collect_counts = {
    "a.binaries": sum(_forbidden_dest(e) for e in a.binaries),
    "install_gate_a.binaries": sum(_forbidden_dest(e) for e in install_gate_a.binaries),
    "selfcheck_a.binaries": sum(_forbidden_dest(e) for e in selfcheck_a.binaries),
    "install_gate_a.datas": sum(_forbidden_dest(e) for e in install_gate_a.datas),
}
_pre_collect_total = sum(_pre_collect_counts.values())
for _producer, _count in _pre_collect_counts.items():
    print(f"PRODUCER_NAME={_producer} FORBIDDEN_DEST_COUNT={_count}")
assert _pre_collect_total == 0, f"PRE_COLLECT_FORBIDDEN_ICU_DEST_COUNT={_pre_collect_total}"

coll = COLLECT(
    exe,
    selfcheck_exe,
    install_gate_exe,
    install_gate_a.binaries,
    install_gate_a.datas,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="App" if uat_config else "MentorFlow",
)
