from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SecretDomain(str, Enum):
    PROVIDER = "PROVIDER"
    OIDC = "OIDC"
    DATABASE = "DATABASE"
    OBJECT_STORE = "OBJECT_STORE"


_RAW_SECRET_NAMES = frozenset(
    {
        "api_key",
        "access_key",
        "client_secret",
        "password",
        "private_key",
        "secret",
        "secret_key",
    }
)
_ENV_SECRET_MARKERS = ("API_KEY", "ACCESS_KEY", "CLIENT_SECRET", "PASSWORD", "PRIVATE_KEY", "SECRET_KEY")
_COMPATIBILITY_ENVIRONMENT_BOUNDARIES = frozenset(
    {
        (
            "teacher_avatar_registry.py",
            "LegacyLocalVroidHubCompatibilityAdapter",
            "credential_snapshot",
            "VROID_HUB_CLIENT_SECRET",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class SecretAccessViolation:
    code: str
    path: str
    line: int
    symbol: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class SecretCompatibilityBoundary:
    path: str
    line: int
    class_name: str
    function_name: str
    symbol: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "class_name": self.class_name,
            "function_name": self.function_name,
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class SecretAccessReport:
    policy_version: str
    scanned_python_files: int
    governed_domains: tuple[str, ...]
    resolver_consumers: tuple[str, ...]
    lexical_fallback_files: tuple[str, ...]
    compatibility_boundaries: tuple[SecretCompatibilityBoundary, ...]
    violations: tuple[SecretAccessViolation, ...]
    admin_plaintext_secret_fields: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.violations and not self.admin_plaintext_secret_fields

    def to_public_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "scanned_python_files": self.scanned_python_files,
            "governed_domains": self.governed_domains,
            "resolver_consumers": self.resolver_consumers,
            "lexical_fallback_files": self.lexical_fallback_files,
            "compatibility_boundaries": tuple(
                item.to_public_dict() for item in self.compatibility_boundaries
            ),
            "violations": tuple(item.to_public_dict() for item in self.violations),
            "admin_plaintext_secret_fields": self.admin_plaintext_secret_fields,
            "admin_plaintext_secret_read_allowed": False,
            "complete": self.complete,
        }


class SecretAccessVerifier:
    """AST-based policy check for Provider/OIDC/DB/object-store secret ingress."""

    POLICY_VERSION = "mentorflow.enterprise-secret-access.v2"

    @staticmethod
    def _literal_string(node: ast.AST) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    @classmethod
    def _environment_name(cls, node: ast.Call) -> str | None:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "getenv":
            return None
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "os":
            return None
        return cls._literal_string(node.args[0]) if node.args else None

    @classmethod
    def _environment_subscript_name(cls, node: ast.Subscript) -> str | None:
        value = node.value
        if (
            not isinstance(value, ast.Attribute)
            or value.attr != "environ"
            or not isinstance(value.value, ast.Name)
            or value.value.id != "os"
        ):
            return None
        return cls._literal_string(node.slice)

    @staticmethod
    def _is_raw_secret_environment(name: str) -> bool:
        normalized = name.upper()
        if normalized.endswith("_REF") or normalized.endswith("_REFERENCE"):
            return False
        return any(marker in normalized for marker in _ENV_SECRET_MARKERS)

    @staticmethod
    def _imports_secret_resolver(tree: ast.AST) -> bool:
        return any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.endswith("credential_store")
            and any(alias.name == "get_secret" for alias in node.names)
            for node in ast.walk(tree)
        )

    @staticmethod
    def _admin_secret_fields(tree: ast.AST, relative_path: str) -> tuple[str, ...]:
        if not relative_path.replace("\\", "/").endswith("governance/admin_api.py"):
            return ()
        fields: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    field_name = child.target.id.lower()
                    if field_name in _RAW_SECRET_NAMES:
                        fields.append(f"{node.name}.{field_name}")
        return tuple(sorted(fields))

    @staticmethod
    def _enclosing_boundary(
        node: ast.AST,
        parents: dict[ast.AST, ast.AST],
    ) -> tuple[str, str]:
        class_name = ""
        function_name = ""
        current = parents.get(node)
        while current is not None:
            if not function_name and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_name = current.name
            if isinstance(current, ast.ClassDef):
                class_name = current.name
                break
            current = parents.get(current)
        return class_name, function_name

    def scan(self, app_root: Path) -> SecretAccessReport:
        root = app_root.resolve()
        python_files = tuple(sorted(root.rglob("*.py")))
        violations: list[SecretAccessViolation] = []
        consumers: list[str] = []
        admin_fields: list[str] = []
        lexical_fallbacks: list[str] = []
        compatibility_boundaries: list[SecretCompatibilityBoundary] = []
        for path in python_files:
            relative = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                lexical_fallbacks.append(relative)
                if re.search(r"credential_store\s+import[^\n]*\bget_secret\b", source):
                    consumers.append(relative)
                if relative != "credential_store.py":
                    for match in re.finditer(
                        r"(?:os\.getenv\(\s*|os\.environ\[\s*)['\"]([^'\"]+)['\"]",
                        source,
                    ):
                        environment_name = match.group(1)
                        if self._is_raw_secret_environment(environment_name):
                            violations.append(
                                SecretAccessViolation(
                                    code="PLAINTEXT_SECRET_ENVIRONMENT_READ",
                                    path=relative,
                                    line=source.count("\n", 0, match.start()) + 1,
                                    symbol=environment_name,
                                )
                            )
                continue
            if self._imports_secret_resolver(tree):
                consumers.append(relative)
            admin_fields.extend(self._admin_secret_fields(tree, relative))
            if relative == "credential_store.py":
                continue
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                environment_name = (
                    self._environment_name(node)
                    if isinstance(node, ast.Call)
                    else self._environment_subscript_name(node)
                    if isinstance(node, ast.Subscript)
                    else None
                )
                if environment_name and self._is_raw_secret_environment(environment_name):
                    class_name, function_name = self._enclosing_boundary(node, parents)
                    boundary = (relative, class_name, function_name, environment_name)
                    if boundary in _COMPATIBILITY_ENVIRONMENT_BOUNDARIES:
                        compatibility_boundaries.append(
                            SecretCompatibilityBoundary(
                                path=relative,
                                line=node.lineno,
                                class_name=class_name,
                                function_name=function_name,
                                symbol=environment_name,
                            )
                        )
                        continue
                    violations.append(
                        SecretAccessViolation(
                            code="PLAINTEXT_SECRET_ENVIRONMENT_READ",
                            path=relative,
                            line=node.lineno,
                            symbol=environment_name,
                        )
                    )
        return SecretAccessReport(
            policy_version=self.POLICY_VERSION,
            scanned_python_files=len(python_files),
            governed_domains=tuple(domain.value for domain in SecretDomain),
            resolver_consumers=tuple(sorted(consumers)),
            lexical_fallback_files=tuple(sorted(lexical_fallbacks)),
            compatibility_boundaries=tuple(
                sorted(
                    compatibility_boundaries,
                    key=lambda item: (item.path, item.line, item.symbol),
                )
            ),
            violations=tuple(sorted(violations, key=lambda item: (item.path, item.line))),
            admin_plaintext_secret_fields=tuple(sorted(set(admin_fields))),
        )

    def assert_complete(self, app_root: Path) -> SecretAccessReport:
        report = self.scan(app_root)
        if not report.complete:
            details = [
                *(f"{item.path}:{item.line}:{item.symbol}" for item in report.violations),
                *report.admin_plaintext_secret_fields,
            ]
            raise AssertionError("secret access policy violations: " + ", ".join(details))
        return report


__all__ = [
    "SecretCompatibilityBoundary",
    "SecretAccessReport",
    "SecretAccessVerifier",
    "SecretAccessViolation",
    "SecretDomain",
]
