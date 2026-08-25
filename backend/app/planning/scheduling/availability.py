"""Read-only dependency capability reporting for the future CP-SAT adapter."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

from .contracts import ScheduleDependencyStatus


ORTOOLS_AVAILABLE = find_spec("ortools") is not None
DEPENDENCY_CHANGE_REQUIRED = None if ORTOOLS_AVAILABLE else "ortools"


def ortools_dependency_status() -> ScheduleDependencyStatus:
    dependency_version: str | None = None
    if ORTOOLS_AVAILABLE:
        try:
            dependency_version = version("ortools")
        except PackageNotFoundError:
            dependency_version = None
    return ScheduleDependencyStatus(
        available=ORTOOLS_AVAILABLE,
        version=dependency_version,
        dependency_change_required=DEPENDENCY_CHANGE_REQUIRED,
    )


__all__ = [
    "DEPENDENCY_CHANGE_REQUIRED",
    "ORTOOLS_AVAILABLE",
    "ortools_dependency_status",
]
