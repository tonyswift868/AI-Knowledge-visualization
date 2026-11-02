"""Extract source-proven process entry and handoff links from a snapshot.

This intentionally recognizes the installer's finite, statically visible
handoffs. It never imports or runs any project module or child process.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _read(root: Path, rel: str) -> tuple[Path, list[str], ast.Module | None]:
    path = root / rel
    if not path.is_file():
        return path, [], None
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        lines = [line.rstrip("\r\n") for line in stream]
    try:
        tree = ast.parse("\n".join(lines), filename=rel)
    except SyntaxError:
        tree = None
    return path, lines, tree


def _ep(rel: str, line: int, function: str) -> dict[str, Any]:
    return {"file": rel, "line": line, "function": function}


def _scope_ep(tree: ast.Module | None, rel: str, line: int) -> dict[str, Any]:
    scope = _enclosing_function(tree, line)
    return _ep(rel, line, scope.name if scope is not None else "<module>")


def _function(tree: ast.Module | None, name: str) -> ast.AST | None:
    if tree is None:
        return None
    direct = next((node for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)
    if direct is not None:
        return direct
    return min((node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name),
               key=lambda node: node.lineno, default=None)


def _enclosing_function(tree: ast.Module | None, line: int) -> ast.AST | None:
    if tree is None:
        return None
    matches = [node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.lineno <= line <= getattr(node, "end_lineno", node.lineno)]
    return min(matches, key=lambda node: getattr(node, "end_lineno", node.lineno) - node.lineno,
               default=None)


def _find(lines: list[str], contains: str, *, start: int = 0) -> int | None:
    return next((index + 1 for index, line in enumerate(lines[start:], start)
                 if contains in line), None)


def _call_line(tree: ast.Module | None, func_name: str, *, within: ast.AST | None) -> int | None:
    if tree is None:
        return None
    hits = []
    if within is None:
        return None
    start = within.lineno
    end = getattr(within, "end_lineno", start)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not start <= node.lineno <= end:
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == func_name:
            hits.append(node.lineno)
        if isinstance(func, ast.Name) and func.id == func_name:
            hits.append(node.lineno)
    return min(hits, default=None)


def _process_call(tree: ast.Module, node: ast.Call) -> str | None:
    """Recognize process APIs without confusing asyncio/Qt methods named run/call."""
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    module = func.value.id
    aliases = {"subprocess"}
    os_aliases = {"os"}
    for item in tree.body:
        if isinstance(item, ast.Import):
            for alias in item.names:
                if alias.name == "subprocess":
                    aliases.add(alias.asname or "subprocess")
                elif alias.name == "os":
                    os_aliases.add(alias.asname or "os")
    if module in aliases and func.attr in {"Popen", "run", "call", "check_call", "check_output"}:
        return func.attr
    if module in os_aliases and func.attr == "startfile":
        return "startfile"
    return None


def _tts_launch_proven(tree: ast.Module | None) -> bool:
    """Require both the default Popen binding and the literal engine argv."""
    launch = _function(tree, "_launch")
    init = _function(tree, "__init__")
    if launch is None or init is None or tree is None:
        return False
    default_popen = False
    for node in ast.walk(init):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Attribute) and target.attr == "_factory"
                   and isinstance(target.value, ast.Name) and target.value.id == "self"
                   for target in targets):
            continue
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or) and len(value.values) >= 2:
            fallback = value.values[-1]
            default_popen = (isinstance(fallback, ast.Attribute) and fallback.attr == "Popen"
                             and isinstance(fallback.value, ast.Name) and fallback.value.id == "subprocess")
        if default_popen:
            break
    if not default_popen:
        return False

    for node in ast.walk(launch):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "_factory" or not isinstance(node.func.value, ast.Name) or node.func.value.id != "self":
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        argv = node.args[0].elts
        has_config = any(isinstance(arg, ast.Constant) and arg.value == "--config" for arg in argv)
        engine_path = False
        for arg in argv:
            if not (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "str" and arg.args):
                continue
            path_expr = arg.args[0]
            if (isinstance(path_expr, ast.Call) and isinstance(path_expr.func, ast.Attribute)
                    and path_expr.func.attr == "with_name" and path_expr.args
                    and isinstance(path_expr.args[0], ast.Constant)
                    and path_expr.args[0].value == "engine_server.py"):
                engine_path = True
        if engine_path and has_config:
            return True
    return False


def _edge(name, source, target, via, rule):
    return dict(kind="process", name=name, source=source, target=target, via=via, rule=rule)


def extract(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Return statically supported process connections and unresolved calls."""
    root = Path(root)
    connections: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []

    build_rel = "scripts/build_learning_installer.py"
    build_path, build_lines, build_tree = _read(root, build_rel)
    install_rel = "refactor/desktop/delivery/per_user_install.py"
    install_path, install_lines, install_tree = _read(root, install_rel)
    packaged_rel = "refactor/desktop/packaged_main.py"
    _, packaged_lines, packaged_tree = _read(root, packaged_rel)
    shell_rel = "refactor/desktop/account_shell_main.py"
    _, shell_lines, shell_tree = _read(root, shell_rel)

    packaged_main = _function(packaged_tree, "main")
    shell_main = _function(shell_tree, "main")
    runtime_name = _find(build_lines, '"--name", "UserRuntime"')
    runtime_entry = _find(build_lines, 'run(runtime + [ROOT / "refactor/desktop/delivery/per_user_install.py"]')
    analysis = _find(build_lines, "Analysis(")
    mentorflow_exe = _find(build_lines, "name='MentorFlow'")

    # GUI -> account-shell process. The entry parser consumes the arguments,
    # then calls the account-shell main function.
    launch_health = _function(install_tree, "launch_health")
    app_popen = _call_line(install_tree, "Popen", within=launch_health)
    run_shell = _find(packaged_lines, "return run_shell(")
    packaged_import = _find(packaged_lines, "import main as run_shell")
    packaging_evidence = _find(build_lines, "Analysis(")
    if app_popen and packaged_main and shell_main and run_shell and packaged_import and packaging_evidence and mentorflow_exe:
        connections.append(_edge(
            "MentorFlow.exe --install-mode per_user",
            _ep(install_rel, app_popen, "launch_health"),
            _ep(shell_rel, shell_main.lineno, "main"),
            [_scope_ep(build_tree, build_rel, packaging_evidence),
             _scope_ep(build_tree, build_rel, mentorflow_exe),
             _ep(packaged_rel, packaged_main.lineno, "main"),
             _ep(packaged_rel, run_shell, "main")],
            "launch_health 的 Popen 启动 MentorFlow.exe；打包入口解析参数后将控制权交给 account_shell_main.main。"))

    # Stable UserRuntime apply coordinator handoff.
    handoff = _function(install_tree, "handoff")
    apply_popen = _call_line(install_tree, "Popen", within=handoff)
    coordinate = _function(install_tree, "coordinate")
    apply_dispatch = _find(install_lines, "if args.action == 'apply': return coordinate")
    parse_args = _find(install_lines, "args = parser.parse_args()")
    runtime_build = runtime_entry
    if apply_popen and coordinate and apply_dispatch and parse_args and runtime_build and runtime_name:
        connections.append(_edge(
            "UserRuntime.exe apply",
            _ep(install_rel, apply_popen, "handoff"),
            _ep(install_rel, coordinate.lineno, "coordinate"),
            [_scope_ep(build_tree, build_rel, runtime_name),
             _scope_ep(build_tree, build_rel, runtime_build),
             _ep(install_rel, parse_args, "main"),
             _ep(install_rel, apply_dispatch, "_execute")],
            "Popen 参数首项为 UserRuntime.exe、动作字面量为 apply；main 解析 action 后 _execute 将 apply 交给 coordinate。"))

    # App -> verified recovery runtime. Only connect to the named action
    # branch; the runtime executable's path is returned by verification.
    pending = _function(install_tree, "handoff_pending")
    pending_popen = _call_line(install_tree, "Popen", within=pending)
    verify = _function(install_tree, "verify_recovery_runtime")
    verify_return = _find(install_lines, "return runtime / 'UserRuntime.exe'", start=verify.lineno - 1 if verify else 0)
    apply_latest_branch = _find(install_lines, "if args.action == 'apply-latest':")
    if pending_popen and verify and verify_return and apply_latest_branch and parse_args and runtime_build and runtime_name:
        connections.append(_edge(
            "UserRuntime.exe apply-latest",
            _ep(install_rel, pending_popen, "handoff_pending"),
            _ep(install_rel, apply_latest_branch, "_execute"),
            [_ep(install_rel, verify.lineno, "verify_recovery_runtime"),
             _ep(install_rel, verify_return, "verify_recovery_runtime"),
             _scope_ep(build_tree, build_rel, runtime_name),
             _scope_ep(build_tree, build_rel, runtime_build),
             _ep(install_rel, parse_args, "main")],
            "handoff_pending 在验签返回的 UserRuntime.exe 上传入 apply-latest；_execute 中该 action 的条件分支处理自退出接管流程。"))

    # Repair's conditional command has two statically known action values.
    repair = _function(install_tree, "handoff_repair")
    repair_popen = _call_line(install_tree, "Popen", within=repair)
    repair_command = _find(install_lines, "command = [str(runtime), 'repair-update' if shell else 'repair-installation'")
    repair_update_branch = _find(install_lines, "if args.action == 'repair-update':")
    repair_install_branch = _find(install_lines, "if args.action == 'repair-installation':")
    launcher_rel = "refactor/desktop/delivery/per_user_launcher.py"
    _, launcher_lines, launcher_tree = _read(root, launcher_rel)
    repair_shell = _function(launcher_tree, "repair_update_shell")
    core_rel = "refactor/desktop/delivery/per_user_update_core.py"
    _, core_lines, core_tree = _read(root, core_rel)
    repair_core = _function(core_tree, "repair_installation")
    if repair_popen and repair_command and repair_update_branch and repair_shell and parse_args and runtime_build and runtime_name:
        connections.append(_edge(
            "UserRuntime.exe repair-update (shell=True)",
            _ep(install_rel, repair_popen, "handoff_repair"),
            _ep(launcher_rel, repair_shell.lineno, "repair_update_shell"),
            [_ep(install_rel, repair_command, "handoff_repair"),
             _scope_ep(build_tree, build_rel, runtime_name),
             _scope_ep(build_tree, build_rel, runtime_build),
             _ep(install_rel, parse_args, "main"),
             _ep(install_rel, repair_update_branch, "_execute")],
            "shell=True 令条件表达式选择 repair-update；_execute 分支调用 per_user_launcher.repair_update_shell。"))
    if repair_popen and repair_command and repair_install_branch and repair_core and parse_args and runtime_build and runtime_name:
        connections.append(_edge(
            "UserRuntime.exe repair-installation (shell=False)",
            _ep(install_rel, repair_popen, "handoff_repair"),
            _ep(core_rel, repair_core.lineno, "repair_installation"),
            [_ep(install_rel, repair_command, "handoff_repair"),
             _scope_ep(build_tree, build_rel, runtime_name),
             _scope_ep(build_tree, build_rel, runtime_build),
             _ep(install_rel, parse_args, "main"),
             _ep(install_rel, repair_install_branch, "_execute")],
            "shell=False 令条件表达式选择 repair-installation；_execute 分支调用 per_user_update_core.repair_installation。"))

    # TTS has an injectable process factory. This link is valid only for its
    # default factory (or the real runtime configuration which leaves it unset).
    tts_rel = "refactor/learning/voice/tts/runtime.py"
    _, tts_lines, tts_tree = _read(root, tts_rel)
    engine_rel = "refactor/learning/voice/tts/engine_server.py"
    _, engine_lines, engine_tree = _read(root, engine_rel)
    tts_launch = _function(tts_tree, "_launch")
    tts_factory = _find(tts_lines, "self._factory = process_factory or subprocess.Popen")
    tts_spawn = _call_line(tts_tree, "_factory", within=tts_launch)
    engine_main = _function(engine_tree, "main")
    if tts_launch and tts_factory and tts_spawn and engine_main and _tts_launch_proven(tts_tree):
        connections.append(_edge(
            "TTS engine_server.py --config",
            _ep(tts_rel, tts_spawn, "_launch"),
            _ep(engine_rel, engine_main.lineno, "main"),
            [_ep(tts_rel, tts_factory, "TtsRuntime.__init__")],
            "_launch 的 argv 明确指向同目录 engine_server.py；仅当未注入 process_factory、采用默认 subprocess.Popen 时成立。"))

    # Record other project child-process calls as one-ended unless a proven
    # route above describes them. No executable name is guessed from variables.
    known_sources = {(row["source"]["file"], row["source"]["line"]) for row in connections}
    for file in sorted((root / "refactor").rglob("*.py")):
        rel = file.relative_to(root).as_posix()
        if rel in {install_rel, tts_rel}:
            # Their remaining process calls, if any, are still discovered below.
            pass
        _, lines, tree = _read(root, rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _process_call(tree, node)
            if not name:
                continue
            if (rel, node.lineno) in known_sources:
                continue
            parent = next((candidate for candidate in ast.walk(tree)
                           if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and candidate.lineno <= node.lineno <= getattr(candidate, "end_lineno", candidate.lineno)), None)
            function = parent.name if parent else "<module>"
            unresolved.append(dict(kind="process", name=f"{name} (target unresolved)",
                source=_ep(rel, node.lineno, function),
                reason="发现跨进程/外部命令调用，但目标由运行时可执行文件、参数或部署环境决定；快照没有可证明的仓库内接收入口。"))

    connections.sort(key=lambda row: (row["source"]["file"], row["source"]["line"], row["name"]))
    unresolved.sort(key=lambda row: (row["source"]["file"], row["source"]["line"], row["name"]))
    dynamic.sort(key=lambda row: (row["source"]["file"], row["source"]["line"], row["name"]))
    return {"connections": connections, "unresolved": unresolved, "dynamic": dynamic}
