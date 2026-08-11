"""Deterministic classic-Windows path budgets for relocatable packages."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .windows_safe_relative import WindowsSafeRelativePathError, validate_windows_safe_relative


SCHEMA = "mentorflow.windows-install-path-budget.v1"
LEGACY_VISIBLE_PATH_LIMIT_CHARS = 259


class WindowsInstallPathBudgetError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def windows_utf16_chars(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def calculate_windows_install_path_budget(relative_paths: Iterable[str]) -> dict[str, Any]:
    try:
        normalized = sorted({validate_windows_safe_relative(value).as_posix() for value in relative_paths})
    except WindowsSafeRelativePathError as exc:
        raise WindowsInstallPathBudgetError(
            "INSTALL_PATH_BUDGET_INVALID", f"install path set contains an unsafe Windows path: {exc}"
        ) from exc
    if not normalized:
        raise WindowsInstallPathBudgetError(
            "INSTALL_PATH_SET_EMPTY", "install path budget requires at least one file"
        )
    longest = max(normalized, key=lambda value: (windows_utf16_chars(value), value))
    longest_chars = windows_utf16_chars(longest)
    maximum_root_chars = LEGACY_VISIBLE_PATH_LIMIT_CHARS - 1 - longest_chars
    if maximum_root_chars < 3:
        raise WindowsInstallPathBudgetError(
            "INSTALL_RELATIVE_PATH_TOO_LONG",
            f"relative path cannot fit beneath any Windows drive root: {longest}",
        )
    canonical_paths = (json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    return {
        "schema": SCHEMA,
        "relative_path_count": len(normalized),
        "relative_paths_sha256": hashlib.sha256(canonical_paths).hexdigest(),
        "max_relative_path": longest,
        "max_relative_path_chars": longest_chars,
        "legacy_windows_visible_path_limit_chars": LEGACY_VISIBLE_PATH_LIMIT_CHARS,
        "root_separator_chars": 1,
        "minimum_safe_install_root_budget": maximum_root_chars,
        "budget_semantics": "maximum_absolute_install_root_utf16_characters",
    }


def validate_windows_install_root_budget(
    install_root: Path,
    path_budget: Mapping[str, Any],
    *,
    relative_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(path_budget, Mapping):
        raise WindowsInstallPathBudgetError(
            "INSTALL_PATH_BUDGET_INVALID", "install path budget must be an object"
        )
    declared = dict(path_budget)
    if relative_paths is not None:
        recalculated = calculate_windows_install_path_budget(relative_paths)
        if declared != recalculated:
            raise WindowsInstallPathBudgetError(
                "INSTALL_PATH_BUDGET_MISMATCH",
                "declared install path budget does not match the exact relative file set",
            )
    required_fields = {
        "schema",
        "relative_path_count",
        "relative_paths_sha256",
        "max_relative_path",
        "max_relative_path_chars",
        "legacy_windows_visible_path_limit_chars",
        "root_separator_chars",
        "minimum_safe_install_root_budget",
        "budget_semantics",
    }
    if set(declared) != required_fields or declared.get("schema") != SCHEMA:
        raise WindowsInstallPathBudgetError(
            "INSTALL_PATH_BUDGET_INVALID", "install path budget schema or fields are invalid"
        )
    root = Path(os.path.abspath(os.fspath(install_root)))
    root_chars = windows_utf16_chars(str(root))
    maximum_root_chars = declared.get("minimum_safe_install_root_budget")
    if isinstance(maximum_root_chars, bool) or not isinstance(maximum_root_chars, int):
        raise WindowsInstallPathBudgetError(
            "INSTALL_PATH_BUDGET_INVALID", "install root budget must be an integer"
        )
    if root_chars > maximum_root_chars:
        raise WindowsInstallPathBudgetError(
            "INSTALL_ROOT_PATH_BUDGET_EXCEEDED",
            f"install root length {root_chars} exceeds safe budget {maximum_root_chars}: {root}",
        )
    return {
        "root": str(root),
        "root_chars": root_chars,
        "maximum_root_chars": maximum_root_chars,
        "max_full_path_chars": root_chars + 1 + int(declared["max_relative_path_chars"]),
    }
