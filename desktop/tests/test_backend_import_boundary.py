"""Protect all Desktop startup routes, including the real spawned API."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_startup_never_mutates_import_search_paths():
    protected = [p for p in (ROOT / "desktop").rglob("*.py") if "tests" not in p.parts]
    protected += list(ROOT.glob("desktop*entry.py"))
    protected += [ROOT / "MentorFlow.spec", ROOT / "scripts/windows_installed_launch.py"]
    protected += [ROOT / "backend/app/orm.py"]
    for path in protected:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sys_names = {"sys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                sys_names.update(alias.asname or alias.name for alias in node.names if alias.name == "sys")
            if isinstance(node, ast.ImportFrom) and node.module == "sys":
                assert all(alias.name not in {"path", "*"} for alias in node.names), (path, node.lineno)
        for node in ast.walk(tree):
            # No aliases, slice writes, insert/append/extend, or reassignment
            # of sys.path can hide in a protected module.
            if isinstance(node, ast.Attribute):
                assert not (isinstance(node.value, ast.Name) and node.value.id in sys_names and node.attr == "path"), (path, node.lineno)
                assert node.attr != "__path__", (path, node.lineno)
            if isinstance(node, ast.Constant) and node.value == "PYTHONPATH":
                # The installed-launch sanitizer may REMOVE inherited paths.
                assert path.name == "windows_installed_launch.py", (path, node.lineno)
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "prepend_sys_path":
                assert len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "", (path, node.lineno)


@pytest.mark.parametrize("module_name", ["app", "app.enterprise"])
def test_conflicting_namespace_is_rejected_without_repair(module_name):
    code = f'''
import importlib.util, sys, types
from desktop.backend_imports import prepare_backend_imports
prepare_backend_imports()
name = {module_name!r}
foreign = types.ModuleType(name)
foreign.__spec__ = importlib.util.spec_from_file_location(name, 'foreign/__init__.py')
sys.modules[name] = foreign
before = list(sys.path)
try:
    prepare_backend_imports()
except ImportError as exc:
    assert 'namespace conflict' in str(exc)
else:
    raise AssertionError('Conflicting package was accepted')
assert sys.modules[name] is foreign
assert sys.path == before
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_real_source_account_backend_starts_from_root_only(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    target = tmp_path / "backend"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.account_backend_import_smoke", str(target)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads((target / "result.json").read_text())
    assert Path(evidence["origin"]).resolve() == ROOT / "backend/app/main.py"
    assert evidence["search_path_unchanged"]
    assert evidence["exitcode"] == 0
    assert evidence["health"]["service"] == "MentorFlow"


def test_all_runtime_backend_entries_use_the_seam():
    for relative in ("desktop/account_backend.py", "desktop/runtime.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "from .backend_imports import load_app" in text
        assert "app = load_app()" in text
        assert "from app.main" not in text
    text = (ROOT / "desktop/enterprise_oidc.py").read_text(encoding="utf-8")
    assert "from .backend_imports import prepare_backend_imports" in text
    bootstrap = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8")
    assert "sys.path" not in bootstrap and "PYTHONPATH" not in bootstrap
    assert "from desktop.backend_imports import prepare_backend_imports" in bootstrap


def test_build_enumerates_canonical_modules_and_keeps_migration_resources():
    tree = ast.parse((ROOT / "MentorFlow.spec").read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "backend_modules" for t in node.targets):
            selected.append(node)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == "source":
            selected.append(node)
    context = {"backend_root": ROOT / "backend"}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "spec-module-list", "exec"), context)
    names = set(context["backend_modules"])
    assert {"app", "app.main", "app.enterprise.identity.issuer_migration", "app.provider"} <= names
    assert all(name == "app" or name.startswith("app.") for name in names)
    text = (ROOT / "MentorFlow.spec").read_text(encoding="utf-8")
    assert 'datas.append((str(migration), "backend/alembic/versions"))' in text
    assert 'pathex=[str(root), str(backend_root)]' in text


def test_resources_keep_project_layout_in_source_and_frozen(monkeypatch, tmp_path):
    from desktop.backend_imports import prepare_backend_imports
    prepare_backend_imports()
    from app import runtime_resources
    assert runtime_resources.project_resource_root() == ROOT
    assert runtime_resources.backend_resource_root() == ROOT / "backend"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert runtime_resources.project_resource_root() == tmp_path
    assert runtime_resources.backend_resource_root() == tmp_path / "backend"


def test_backend_resource_consumers_do_not_infer_frozen_project_depth():
    for path in (ROOT / "backend/app").glob("*.py"):
        if path.name == "runtime_resources.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "Path(__file__).resolve().parents[2]" not in text, path
        assert "Path(__file__).resolve().parents[1]" not in text, path
