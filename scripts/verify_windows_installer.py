"""Static contract checks for the MentorFlow offline Windows installer.

The production payload is intentionally not rebuilt by this verifier.  It
checks the installer source and (optionally) a compiled artifact so release
review can prove the uninstall/data contract without touching MentorFlowData.
"""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = ROOT / "installer" / "MentorFlow.nsi"
DEFAULT_PAYLOAD = ROOT / "release" / "distributable" / "MentorFlow"
VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class InstallerContractError(RuntimeError):
    pass


def _read_script(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InstallerContractError(f"cannot read installer script {path}: {exc}") from exc


def _require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise InstallerContractError(f"missing {label}: {needle}")


def verify_script(path: Path = DEFAULT_SCRIPT) -> dict[str, object]:
    source = _read_script(path)
    checks = {
        "unicode": "Unicode True" in source,
        "per_user": "RequestExecutionLevel user" in source,
        "selectable_directory": 'InstallDir "${INSTALL_DIR}"' in source
        and "MUI_PAGE_DIRECTORY" in source,
        "install_dir_length_budget": all(
            needle in source
            for needle in (
                "!ifndef MAX_INSTALL_DIR_CHARS",
                'StrLen $4 "$INSTDIR"',
                "IntCmp $4 ${MAX_INSTALL_DIR_CHARS}",
                "INSTALL_DIR_TOO_LONG",
            )
        ),
        "install_dir_outside_data_roots": all(
            needle in source
            for needle in (
                "Function .onInit",
                "Call .onVerifyInstDir",
                '${GetRoot} "$INSTDIR" $0',
                'StrLen $0 "${DATA_ROOT_CANONICAL}"',
                'StrCpy $1 "$INSTDIR" $0',
                'StrCpy $1 "${DATA_ROOT_CANONICAL}" $0',
                'StrLen $0 "${DATA_ROOT_LOCALAPPDATA}"',
                'StrCpy $1 "${DATA_ROOT_LOCALAPPDATA}" $0',
            )
        ),
        "stable_product_key": 'PRODUCT_KEY "Software\\MentorFlow"' in source,
        "hkcu_registration": 'WriteRegStr HKCU "${UNINSTALL_KEY}"' in source,
        "compile_time_overrides": all(
            needle in source
            for needle in (
                '!ifndef PRODUCT_KEY',
                '!ifndef UNINSTALL_KEY',
                '!ifndef SHORTCUT_FOLDER',
                '!ifndef SHORTCUT_NAME',
                '!ifndef DATA_ROOT_CANONICAL',
                '!ifndef DATA_ROOT_LOCALAPPDATA',
                '!ifndef INSTALL_DIR',
                '!ifndef FILE_VERSION',
            )
        ),
        "production_defaults": all(
            needle in source
            for needle in (
                '!define PRODUCT_KEY "Software\\MentorFlow"',
                '!define UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MentorFlow"',
                '!define SHORTCUT_FOLDER "MentorFlow"',
                '!define SHORTCUT_NAME "MentorFlow.lnk"',
                '!define DATA_ROOT_CANONICAL "E:\\MentorFlowData"',
                '!define DATA_ROOT_LOCALAPPDATA "$LOCALAPPDATA\\MentorFlow"',
                '!define INSTALL_DIR "$LOCALAPPDATA\\Programs\\MentorFlow"',
            )
        ),
        "start_menu_shortcuts": all(
            needle in source
            for needle in (
                'CreateDirectory "$SMPROGRAMS\\${SHORTCUT_FOLDER}"',
                'CreateShortCut "$SMPROGRAMS\\${SHORTCUT_FOLDER}\\${SHORTCUT_NAME}"',
                'Delete "$SMPROGRAMS\\${SHORTCUT_FOLDER}\\${SHORTCUT_NAME}"',
            )
        ),
        "optional_desktop_shortcut": 'Section /o "Desktop shortcut"' in source,
        "data_root_contract": 'DATA_ROOT_CANONICAL "E:\\MentorFlowData"' in source
        and 'DATA_ROOT_LOCALAPPDATA "$LOCALAPPDATA\\MentorFlow"' in source
        and 'StrCpy $UnLocalDataRoot "${DATA_ROOT_LOCALAPPDATA}"' in source,
        "unchecked_delete_option": "${NSD_SetState} $UnDataCheckbox ${BST_UNCHECKED}" in source,
        "second_confirmation": "MB_YESNO|MB_DEFBUTTON2" in source,
        "silent_preserve": 'StrCpy $UnDeleteData "0"' in source and "IfSilent un.silent_init" in source,
        "reparse_fail_closed": "REPARSE_POINT" in source and "DEVICE" in source,
        "program_tree_fail_closed": 'Push "$INSTDIR"' in source
        and "Call un.CheckSafeTree" in source
        and "SetErrorLevel 2" in source,
        "empty_directory_is_safe_only": "GetLastError() i .r1" in source
        and 'StrCmp $1 "2" un.safe_tree_empty' in source
        and 'StrCmp $1 "18" un.safe_tree_empty' in source
        and "after enumeration has started" in source,
        "exact_root_helper": "Function un.DeleteAllowedRoot" in source
        and 'StrCmp "$0" "${DATA_ROOT_CANONICAL}"' in source
        and 'StrCmp "$0" "$UnLocalDataRoot"' in source,
        "offline": "inetc::" not in source and "NSISdl::" not in source,
        "chinese_first_ui": all(
            needle in source
            for needle in (
                'Unicode True',
                '欢迎使用 MentorFlow',
                'LangString UN_DATA_LABEL 2052',
                '!insertmacro MUI_LANGUAGE "SimpChinese"',
                '!insertmacro MUI_LANGUAGE "English"',
            )
        )
        and source.index('!insertmacro MUI_LANGUAGE "SimpChinese"')
        < source.index('!insertmacro MUI_LANGUAGE "English"'),
        "unsigned_truthful": "unsigned" in source.lower()
        and "No Authenticode certificate" in source
        and "signtool" not in source.lower(),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise InstallerContractError("installer contract failed: " + ", ".join(failures))
    return {"script": str(path), "checks": checks}


def _validate_payload_root(payload_root: Path) -> Path:
    try:
        item = payload_root.lstat()
    except OSError as exc:
        raise InstallerContractError(f"payload root is unavailable: {payload_root}: {exc}") from exc
    if not payload_root.is_dir():
        raise InstallerContractError(f"payload root is not a directory: {payload_root}")
    if payload_root.is_symlink() or (int(getattr(item, "st_file_attributes", 0) or 0) & 0x400):
        raise InstallerContractError(f"payload root is a symlink/reparse point: {payload_root}")
    if not (payload_root / "MentorFlow.exe").is_file():
        raise InstallerContractError(f"payload executable is missing: {payload_root / 'MentorFlow.exe'}")
    return payload_root


def verify_compiled_artifact(path: Path) -> dict[str, object]:
    try:
        item = path.stat()
    except OSError as exc:
        raise InstallerContractError(f"compiled installer is unavailable: {path}: {exc}") from exc
    if not stat.S_ISREG(item.st_mode) or item.st_size < 32 * 1024:
        raise InstallerContractError(f"compiled installer is unexpectedly small: {path} ({item.st_size} bytes)")
    # NSIS PE files carry the ASCII marker in their stub.  This is intentionally
    # a lightweight check; execution belongs to the isolated Windows smoke test.
    try:
        head = path.read_bytes()[:2]
    except OSError as exc:
        raise InstallerContractError(f"cannot inspect compiled installer {path}: {exc}") from exc
    if head != b"MZ":
        raise InstallerContractError(f"compiled installer is not a PE executable: {path}")
    return {"artifact": str(path), "bytes": item.st_size, "pe": True}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify MentorFlow offline installer contract")
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--artifact", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report: dict[str, object] = verify_script(args.script)
        if args.payload_root is not None:
            report["payload"] = str(_validate_payload_root(args.payload_root))
        if args.artifact is not None:
            report.update(verify_compiled_artifact(args.artifact))
        print("MENTORFLOW_INSTALLER_CHECK=PASS")
        return 0
    except InstallerContractError as exc:
        print(f"MENTORFLOW_INSTALLER_CHECK_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
