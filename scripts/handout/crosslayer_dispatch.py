"""Resolve Python name-based dispatches that the syntax call graph cannot join.

The extractor is intentionally limited to literal registries/branches and
finite action sets guarded in the pinned source. It reports remaining relevant
``getattr`` sites without guessing a target.
"""
from __future__ import annotations

import ast
from pathlib import Path
import posixpath
import re


def _endpoint(path: str, line: int, function: str) -> dict:
    return {"file": path, "line": line, "function": function}


class _PythonIndex:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.trees: dict[str, ast.Module] = {}
        self.defs: dict[tuple[str, str, str | None], ast.AST] = {}
        for path in sorted(self.root.rglob("*.py")):
            rel = path.relative_to(self.root).as_posix()
            if not (rel.startswith("refactor/learning/app/") or rel.startswith("refactor/desktop/")):
                continue
            try:
                with path.open("r", encoding="utf-8-sig") as stream:
                    source = "".join(stream)
                tree = ast.parse(source, filename=rel)
            except (OSError, SyntaxError, UnicodeError):
                continue
            self.trees[rel] = tree
            visitor = _DefinitionIndex(rel, self.defs)
            visitor.visit(tree)

    def find_def(self, path: str, name: str, class_name: str | None = None):
        node = self.defs.get((path, name, class_name))
        if node is None and class_name is None:
            candidates = [(key, value) for key, value in self.defs.items()
                          if key[0] == path and key[1] == name and key[2] is None]
            if candidates:
                node = candidates[0][1]
        return node

    def exact_method(self, name: str, *, path_prefix: str | None = None, class_name: str | None = None):
        matches = [(key, value) for key, value in self.defs.items()
                   if key[1] == name and (class_name is None or key[2] == class_name)
                   and (path_prefix is None or key[0].startswith(path_prefix))]
        return ((matches[0][0][0], matches[0][1]) if len(matches) == 1 else None)

    def endpoint(self, path: str, name: str, *, class_name: str | None = None, fallback_line: int | None = None):
        node = self.find_def(path, name, class_name)
        if node is None:
            return None
        return _endpoint(path, node.lineno, f"{class_name + '.' if class_name else ''}{name}")


class _DefinitionIndex(ast.NodeVisitor):
    def __init__(self, path: str, definitions: dict):
        self.path = path
        self.definitions = definitions
        self.class_name = None

    def visit_ClassDef(self, node: ast.ClassDef):
        previous, self.class_name = self.class_name, node.name
        self.generic_visit(node)
        self.class_name = previous

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.definitions.setdefault((self.path, node.name, self.class_name), node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def _walk_function(node: ast.AST):
    return list(ast.walk(node))


def _invoke_names(value: ast.AST, nested_functions: dict[str, ast.FunctionDef]):
    """Return literal first arguments of invoke(...) in a registry value."""
    if isinstance(value, ast.Name) and value.id in nested_functions:
        value = nested_functions[value.id]
    found = []
    for node in _walk_function(value):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "invoke" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            found.append(node.args[0].value)
    return list(dict.fromkeys(found))


def _classroom(index: _PythonIndex):
    path = "refactor/learning/app/classroom.py"
    tree = index.trees.get(path)
    if tree is None:
        return [], [], []
    register = index.find_def(path, "register")
    klass = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Classroom"), None)
    if register is None or klass is None:
        return [], [], []
    nested = {n.name: n for n in ast.walk(register) if isinstance(n, ast.FunctionDef)}
    class_methods = {n.name: n for n in klass.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    method_dict = next((n.value for n in ast.walk(register)
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "methods" for t in n.targets)
                        and isinstance(n.value, ast.Dict)), None)
    if method_dict is None:
        return [], [], []
    connections, unresolved, dynamic = [], [], []
    perform = class_methods.get("perform")
    perform_action_write = next((n for n in ast.walk(perform) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "action"
                and isinstance(t.value, ast.Attribute) and t.value.attr == "_local"
                for t in n.targets)), None) if perform else None
    perform_getattr = next((n for n in ast.walk(perform) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "getattr"
        and len(n.args) > 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"
        and isinstance(n.args[1], ast.Name) and n.args[1].id == "action"), None) if perform else None
    retry = class_methods.get("retry")
    run = class_methods.get("run")
    resume = class_methods.get("_resume_materials")
    wait = class_methods.get("_wait")
    guarded_actions = set()
    if wait:
        for node in ast.walk(wait):
            if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                    and node.left.id == "action"):
                continue
            for comparator in node.comparators:
                if isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                    guarded_actions.update(item.value for item in comparator.elts
                                           if isinstance(item, ast.Constant) and isinstance(item.value, str))
    registrations = {}
    for key_node, value_node in zip(method_dict.keys, method_dict.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            continue
        actions = _invoke_names(value_node, nested)
        if actions:
            registrations[key_node.value] = (key_node, actions)
            for action in actions:
                target_node = class_methods.get(action)
                if target_node is None or perform is None or perform_getattr is None:
                    unresolved.append({"kind": "dispatch", "name": key_node.value,
                        "source": _endpoint(path, key_node.lineno, "register.methods"),
                        "reason": f"registry maps to {action!r}, but no matching Classroom method was found"})
                    continue
                connections.append({
                    "kind": "dispatch", "name": key_node.value,
                    "source": _endpoint(path, perform_getattr.lineno, "Classroom.perform getattr(self, action)"),
                    "target": _endpoint(path, target_node.lineno, f"Classroom.{action}"),
                    "via": [_endpoint(path, key_node.lineno, "classroom.register.methods")],
                    "rule": (f"classroom.register 的 {key_node.value!r} 值调用 invoke({action!r})；"
                             "perform 收到同一个 action 后以 getattr(self, action) 选取同名 Classroom 方法。")})
    # The legacy preparation wait is guarded by _wait's literal allow-list.
    if resume and wait and guarded_actions:
        getattr_nodes = [n for n in ast.walk(resume) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                         and len(n.args) > 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"]
        if getattr_nodes:
            node = getattr_nodes[0]
            for action in sorted(guarded_actions):
                target_node = class_methods.get(action)
                if target_node:
                    connections.append({"kind": "dispatch", "name": f"material_wait.action={action}",
                        "source": _endpoint(path, node.lineno, "Classroom._resume_materials"),
                        "target": _endpoint(path, target_node.lineno, f"Classroom.{action}"),
                        "via": [_endpoint(path, wait.lineno, "Classroom._wait action allow-list")],
                        "rule": "_wait 只保存 start_test/start_check 两个允许值；其它旧状态会先由 normalizer 清理。"})
            dynamic.append({"kind": "dispatch", "name": "material_wait.action",
                "source": _endpoint(path, node.lineno, "Classroom._resume_materials"),
                "reason": "运行时从已保存状态读取；可恢复目标限于 _wait 的两个字面允许值，具体值要看该状态记录。"})
    # A retry action comes from the finite bridge registry and is also persisted.
    if retry and registrations:
        getattr_nodes = [n for n in ast.walk(retry) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                         and len(n.args) > 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"]
        if getattr_nodes:
            node = getattr_nodes[0]
            failure_writes = []
            if run:
                for candidate in ast.walk(run):
                    if not isinstance(candidate, ast.Assign) or not isinstance(candidate.value, ast.Call):
                        continue
                    if (isinstance(candidate.targets[0], ast.Subscript)
                            and isinstance(candidate.targets[0].value, ast.Name)
                            and candidate.targets[0].value.id == "state"
                            and isinstance(candidate.targets[0].slice, ast.Constant)
                            and candidate.targets[0].slice.value == "failure"
                            and isinstance(candidate.value.func, ast.Name) and candidate.value.func.id == "dict"):
                        failure_writes.append(candidate)
            if not failure_writes:
                unresolved.append({"kind": "dispatch", "name": "failure.action",
                    "source": _endpoint(path, node.lineno, "Classroom.retry"),
                    "reason": "未能定位 Classroom.run 将实际 action 值写入 failure 记录的赋值；不以桥注册名代替。"})
            for bridge_name, (key_node, actions) in registrations.items():
                for action in actions:
                    target_node = class_methods.get(action)
                    if target_node and perform_action_write and failure_writes:
                        registry_value = next((value for key, value in zip(method_dict.keys, method_dict.values)
                                               if key is key_node), None)
                        nested_value = nested.get(registry_value.id) if isinstance(registry_value, ast.Name) else registry_value
                        invoke_site = next((candidate for candidate in _walk_function(nested_value)
                            if isinstance(candidate, ast.Call) and isinstance(candidate.func, ast.Name)
                            and candidate.func.id == "invoke" and candidate.args
                            and isinstance(candidate.args[0], ast.Constant) and candidate.args[0].value == action), None)
                        if invoke_site is None:
                            continue
                        via = [_endpoint(path, key_node.lineno, f"classroom.register.methods {bridge_name}"),
                               _endpoint(path, invoke_site.lineno, f"invoke({action})"),
                               _endpoint(path, perform_action_write.lineno, "Classroom.perform writes self._local.action")]
                        via.extend(_endpoint(path, candidate.lineno, "Classroom.run writes failure.action = action[0]")
                                   for candidate in failure_writes)
                        connections.append({"kind": "dispatch", "name": f"failure.action={action}",
                            "source": _endpoint(path, node.lineno, "Classroom.retry"),
                            "target": _endpoint(path, target_node.lineno, f"Classroom.{action}"),
                            "via": via,
                            "rule": f"桥方法 {bridge_name!r} 调用 invoke({action!r})；perform 将该 action 存入 action[0]，run 保存为 failure.action，retry 再按此值 getattr 选择 Classroom.{action}。"})
            dynamic.append({"kind": "dispatch", "name": "failure.action",
                "source": _endpoint(path, node.lineno, "Classroom.retry"),
                "reason": "action 从持久化 failure 记录读取；当前代码的写入来自 perform 的注册动作集，但记录值由运行时决定。"})
    return connections, unresolved, dynamic


def _resolve_update_dispatch(index: _PythonIndex):
    con, unresolved, dynamic = [], [], []
    handlers = "refactor/learning/app/handlers.py"
    shell = "refactor/desktop/account_shell_main.py"
    updates = "refactor/desktop/updates.py"
    core = "refactor/desktop/delivery/per_user_update_core.py"
    startup = "refactor/desktop/delivery/startup_integrity.py"
    htree = index.trees.get(handlers)
    atree = index.trees.get(shell)
    utree = index.trees.get(updates)
    stree = index.trees.get(startup)
    ctree = index.trees.get(core)
    # Resolve each independent route from whichever source files are present.
    if not any((htree, atree, utree, stree, ctree)):
        return con, unresolved, dynamic
    # Exact QApplication property assignment to an imported DesktopUpdates type.
    assignment = next((n for n in (ast.walk(atree) if atree else [])
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "_mentorflow_updates"
                                             and isinstance(t.value, ast.Name) for t in n.targets)
        and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == "DesktopUpdates"), None)
    app_name = next((t.value.id for t in assignment.targets if isinstance(t, ast.Attribute)
                     and t.attr == "_mentorflow_updates" and isinstance(t.value, ast.Name)), None) if assignment else None
    app_binding = None
    if atree and app_name:
        for node in ast.walk(atree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not any(isinstance(t, ast.Name) and t.id == app_name for t in targets) or not isinstance(value, ast.Call):
                continue
            function = value.func
            is_app = (isinstance(function, ast.Name) and function.id == "QApplication") or (
                isinstance(function, ast.Attribute) and function.attr == "instance")
            if is_app and node.lineno < assignment.lineno:
                app_binding = node
    if assignment and app_binding is None:
        assignment = None
    init = index.endpoint(updates, "__init__", class_name="DesktopUpdates")
    if atree is not None and htree is not None and utree is not None and (assignment is None or init is None):
        unresolved.append({"kind": "dispatch", "name": "QApplication._mentorflow_updates",
            "source": _endpoint(handlers, 133, "handlers.update_action"),
            "reason": "未能唯一匹配 QApplication 属性的 DesktopUpdates 构造赋值。"})
        # Other independent literal dispatches below may still be resolved.
    bind = (_endpoint(shell, assignment.lineno, "main app._mentorflow_updates = DesktopUpdates(...)" )
            if assignment is not None else None)
    app_construct = (_endpoint(shell, app_binding.lineno, "main app = QApplication(...) / QApplication.instance()")
                     if app_binding is not None else None)
    handler = index.find_def(handlers, "update_action")
    get_site = next((n for n in ast.walk(handler) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) > 1
        and isinstance(n.args[0], ast.Call) and isinstance(n.args[0].func, ast.Attribute)
        and n.args[0].func.attr == "instance"), None) if handler else None
    # Read the literal bridge registration loop and the actual branch selecting snapshot/run.
    action_values = []
    registration = index.find_def(handlers, "register")
    registration_call = None
    if registration:
        for node in ast.walk(registration):
            if not isinstance(node, ast.For) or not isinstance(node.iter, (ast.Tuple, ast.List)):
                continue
            if isinstance(node.target, ast.Name) and node.target.id == "action":
                values = [item.value for item in node.iter.elts
                          if isinstance(item, ast.Constant) and isinstance(item.value, str)]
                call = next((n for n in ast.walk(node) if isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute) and n.func.attr == "register"
                             and len(n.args) > 1 and isinstance(n.args[0], ast.BinOp)
                             and isinstance(n.args[0].op, ast.Add)
                             and isinstance(n.args[0].left, ast.Constant)
                             and isinstance(n.args[0].left.value, str)
                             and isinstance(n.args[0].right, ast.Name) and n.args[0].right.id == "action"), None)
                if call and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                and n.func.id == "update_action" for n in ast.walk(call.args[1])):
                    action_values, registration_call = values, call
                    break
    selected_method = None
    if handler:
        for node in ast.walk(handler):
            if not isinstance(node, ast.IfExp) or not isinstance(node.test, ast.Compare):
                continue
            test = node.test
            if (isinstance(test.left, ast.Name) and test.left.id == "action"
                    and any(isinstance(v, ast.Constant) and v.value == "status" for v in test.comparators)):
                selected_method = {
                    "status": node.body.func.attr if isinstance(node.body, ast.Call) and isinstance(node.body.func, ast.Attribute) else None,
                    "other": node.orelse.func.attr if isinstance(node.orelse, ast.Call) and isinstance(node.orelse.func, ast.Attribute) else None,
                }
                break
    for action in action_values:
        method = (selected_method or {}).get("status" if action == "status" else "other")
        if method not in {"snapshot", "run"}:
            continue
        target = index.endpoint(updates, method, class_name="DesktopUpdates")
        if target and handler and get_site and bind:
            con.append({"kind": "dispatch", "name": f"update.{action}",
                "source": _endpoint(handlers, get_site.lineno, "handlers.update_action"),
                "target": target, "via": [app_construct, bind, _endpoint(handlers, registration_call.lineno, "handlers.register bridge.register('update.' + action, ...)")],
                "rule": f"register 的字面 action 集包含 {action!r}；update_action 中 action == 'status' 走 {method}，其余分支走 run。QApplication.instance() 属性由 main 中同一 app 实例绑定。"})
    # DesktopUpdates.run passes an operation variable selected from the shell state.
    run = index.find_def(updates, "run", "DesktopUpdates")
    operation_assign = next((n for n in ast.walk(run) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "operation" for t in n.targets)
        and isinstance(n.value, ast.IfExp)), None) if run else None
    operation_call = next((n for n in ast.walk(run) if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "operation"), None) if run else None
    if run and operation_assign and operation_call:
        expression = operation_assign.value
        test = expression.test
        condition = None
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Call)
                and isinstance(test.left.func, ast.Attribute) and test.left.func.attr == "get"
                and isinstance(test.left.func.value, ast.Attribute) and test.left.func.value.attr == "state"
                    and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)):
                condition = f"state.get({test.left.args[0].value!r}) == {test.comparators[0].value!r}" if (
                    len(test.left.args) == 1 and isinstance(test.left.args[0], ast.Constant)) else None
        branches = ((expression.body, condition), (expression.orelse, f"not ({condition})" if condition else None))
        for branch_value, branch in branches:
            if not isinstance(branch_value, ast.Attribute):
                continue
            name = branch_value.attr
            target = index.endpoint(core, name)
            if target is None:
                continue
            con.append({"kind": "dispatch", "name": f"install operation ({branch})",
                "source": _endpoint(updates, operation_assign.lineno, "DesktopUpdates.run worker"),
                "target": target,
                "via": [_endpoint(updates, operation_call.lineno, "DesktopUpdates.run operation(...)"), *([bind] if bind else [])],
                "rule": f"operation 来自 IfExp 的 {branch!r} 分支；运行时 shell 状态决定实际目标。"})
    # A background integrity failure routes the explicit Repair button to the same updater.
    click = next((n for n in ast.walk(stree) if isinstance(n, ast.FunctionDef) and n.name == "clicked"), None) if stree else None
    if click:
        call = next((n for n in ast.walk(click) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "updates"), None)
        if call:
            con.append({"kind": "dispatch", "name": "background integrity Repair button → update.install",
                "source": _endpoint(startup, call.lineno, "clicked"),
                "target": index.endpoint(updates, "run", class_name="DesktopUpdates"), "via": [bind] if bind else [],
                "rule": "schedule(app, ...) 在已创建 updater 后排队显示修复按钮；点击分支先设置 shell='repair'，再调用 run('install')。"})
    # Resolve the literal method-name conditional in handlers.model_ready.
    model_ready = index.find_def(handlers, "model_ready")
    if model_ready:
        getattrs = [n for n in ast.walk(model_ready) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) > 1]
        for node in getattrs:
            names = []
            for item in ast.walk(node.args[1]):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    names.append(item.value)
            for name in dict.fromkeys(names):
                match = index.exact_method(name, path_prefix="refactor/learning/", class_name="AppServices")
                if match:
                    target_path, target_node = match
                    con.append({"kind": "dispatch", "name": f"model_ready.{name}",
                        "source": _endpoint(handlers, node.lineno, "model_ready getattr(current, name)"),
                        "target": _endpoint(target_path, target_node.lineno, f"AppServices.{name}"),
                        "via": [index.endpoint("refactor/learning/app/window.py", "bind_feature_context", class_name="LearningWindow")],
                        "rule": "recovered 为真时取 on_network_restored，否则取 start_model_auto_check；window.py 在 bind_feature_context 将 self.services 绑定为 AppServices 实例。"})
    return con, unresolved, dynamic


def _typescript_routes(root: Path):
    """Join literal page navigation to App's imported route component table."""
    root = Path(root)
    from .crosslayer_bridge import _ts_function_at, _ts_functions
    app_rel = "refactor/learning/web/src/App.tsx"
    store_rel = "refactor/learning/web/src/store.ts"
    app_path, store_path = root / app_rel, root / store_rel
    if not app_path.is_file() or not store_path.is_file():
        return [], [], []

    def read_lines(path):
        with path.open("r", encoding="utf-8-sig") as stream:
            return list(stream)

    app_lines = read_lines(app_path)
    store_lines = read_lines(store_path)
    app_text, store_text = "".join(app_lines), "".join(store_lines)
    imports = {}
    import_lines = {}
    for number, line in enumerate(app_lines, 1):
        match = re.match(r"\s*import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", line)
        if not match:
            continue
        module = match.group(2)
        for spec in match.group(1).split(","):
            item = re.match(r"\s*(\w+)(?:\s+as\s+(\w+))?\s*", spec)
            if not item:
                continue
            original, alias = item.group(1), item.group(2) or item.group(1)
            imports[alias] = (original, module, number)
            import_lines[alias] = number

    route_match = re.search(r"(?P<object>\{[^{}]*\})\s*\[\s*s\.route\s*\]", app_text, re.S)
    render_match = re.search(r"<Page\b[^>]*?/\s*>", app_text)
    store_match = re.search(r"^[ \t]*go:\s*\(route[^)]*\)\s*=>\s*set\(", store_text, re.M)
    if route_match is None or render_match is None or store_match is None:
        return [], [{"kind": "dispatch", "name": "web route table",
                     "source": _endpoint(app_rel, 1, "App"),
                     "reason": "未能同时证明 App 的 s.route 对象索引、<Page /> render 和 store.go 对 route 状态的写入。"}], []

    table_start = app_text.count("\n", 0, route_match.start("object")) + 1
    route_map = {}
    entry_pattern = re.compile(r"(?:(['\"])([^'\"]+)\1|([A-Za-z_$][\w$-]*))\s*:\s*([A-Za-z_$][\w$]*)")
    for match in entry_pattern.finditer(route_match.group("object")):
        key = match.group(2) or match.group(3)
        component = match.group(4)
        line = table_start + route_match.group("object")[:match.start()].count("\n")
        route_map[key] = (component, line)

    go_line = store_text.count("\n", 0, store_match.start()) + 1
    render_line = app_text.count("\n", 0, render_match.start()) + 1
    connections, unresolved, dynamic = [], [], []
    page_root = root / "refactor/learning/web/src/pages"
    if not page_root.is_dir():
        return [], [], []
    page_files = sorted(page_root.glob("*.tsx"))
    store_resolved = posixpath.normpath(posixpath.join("refactor/learning/web/src", "./store")) + ".ts"
    for page_path in page_files:
        page_rel = page_path.relative_to(root).as_posix()
        lines = read_lines(page_path)
        text = "".join(lines)
        function_defs = _ts_functions(lines)
        for call in re.finditer(r"(?:\bgo|\bs\.go)\s*\(\s*(['\"])([^'\"]+)\1", text):
            route = call.group(2)
            source_line = text.count("\n", 0, call.start()) + 1
            caller = _ts_function_at(function_defs, source_line)
            scopes = [(start, end, name) for start, end, name in function_defs
                      if start <= source_line <= end]
            outer_scope_start = min((start for start, _end, _name in scopes), default=1)
            function_start = sum(len(line) for line in lines[:outer_scope_start - 1])
            source = _endpoint(page_rel, source_line, caller)
            import_useapp = False
            for import_line in lines:
                imported = re.match(r"\s*import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", import_line)
                if not imported or "useApp" not in {part.strip().split(" as ", 1)[0] for part in imported.group(1).split(",")}:
                    continue
                module_path = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), imported.group(2)))
                if not posixpath.splitext(module_path)[1]:
                    module_path += ".ts"
                if module_path == store_resolved:
                    import_useapp = True
                    break
            call_name = call.group(0).lstrip().split("(", 1)[0]
            bound_go = False
            if import_useapp:
                before = text[function_start:call.start()]
                if call_name == "go":
                    bound_go = bool(re.search(r"\bconst\s+go\s*=\s*useApp\s*\(\s*\(\s*(\w+)\s*\)\s*=>\s*\1\.go\s*\)", before)
                                      or re.search(r"\bconst\s*\{[^}]*\bgo\b[^}]*\}\s*=\s*useApp\s*\(\s*\)", before))
                elif call_name == "s.go":
                    bound_go = bool(re.search(r"\bconst\s+s\s*=\s*useApp\s*\(\s*\)", before))
            if not bound_go:
                unresolved.append({"kind": "dispatch", "name": f"go({route!r})", "source": source,
                                   "reason": "未能在此组件确认 go/s.go 来自 useApp().go，且 useApp 必须从本项目 store 模块导入。"})
                continue
            target_spec = route_map.get(route)
            if target_spec is None:
                unresolved.append({"kind": "dispatch", "name": f"go({route!r})",
                                   "source": source,
                                   "reason": "页面 route 字面值未出现在 App 的 s.route 组件表中。"})
                continue
            alias, table_line = target_spec
            imported = imports.get(alias)
            if imported is None:
                unresolved.append({"kind": "dispatch", "name": f"route {route}", "source": source,
                                   "reason": f"App route table 指向 {alias}，但没有对应的静态 import。"})
                continue
            original, module, import_line = imported
            if not module.startswith("."):
                unresolved.append({"kind": "dispatch", "name": f"route {route}", "source": source,
                                   "reason": "目标组件不是相对模块导入，无法在快照内解析文件。"})
                continue
            target_path = posixpath.normpath(posixpath.join("refactor/learning/web/src", module))
            if not posixpath.splitext(target_path)[1]:
                target_path += ".tsx"
            target_file = root / target_path
            if not target_file.is_file():
                unresolved.append({"kind": "dispatch", "name": f"route {route}", "source": source,
                                   "reason": f"导入目标文件不存在于快照：{target_path}"})
                continue
            target_lines = read_lines(target_file)
            target_text = "".join(target_lines)
            component_match = re.search(rf"(?:export\s+)?(?:default\s+)?function\s+{re.escape(original)}\s*\(", target_text)
            if component_match is None:
                unresolved.append({"kind": "dispatch", "name": f"route {route}", "source": source,
                                   "reason": f"导入文件中未找到组件定义 {original}。"})
                continue
            component_line = target_text.count("\n", 0, component_match.start()) + 1
            via = [_endpoint(store_rel, go_line, "store.go route -> set({ route })"),
                   _endpoint(app_rel, table_line, f"App route table {route}: {alias}"),
                   _endpoint(app_rel, render_line, "App render <Page />"),
                   _endpoint(app_rel, import_line, f"App import {alias} from {module}")]
            connections.append({"kind": "dispatch", "name": f"go({route})",
                "source": source, "target": _endpoint(target_path, component_line, original), "via": via,
                "rule": f"页面发出字面 route {route!r}；store.go 写入 s.route，App 用 s.route 索引字面组件表，{alias} 来自相对导入 {module} 并由 <Page /> 渲染。"})

    return connections, unresolved, dynamic


def extract(root: Path) -> dict[str, list[dict]]:
    """Return statically resolvable dispatches and explicitly unresolved sites."""
    index = _PythonIndex(Path(root))
    connections, unresolved, dynamic = [], [], []
    for extractor in (_classroom, _resolve_update_dispatch):
        rows, missing, open_sites = extractor(index)
        connections.extend(rows)
        unresolved.extend(missing)
        dynamic.extend(open_sites)
    rows, missing, open_sites = _typescript_routes(Path(root))
    connections.extend(rows)
    unresolved.extend(missing)
    dynamic.extend(open_sites)
    unique = {}
    for row in connections:
        key = (row["kind"], row["name"], row["source"]["file"], row["source"]["line"],
               row["target"]["file"], row["target"]["line"])
        unique[key] = row
    connections = sorted(unique.values(), key=lambda row: (row["target"]["file"], row["target"]["line"],
                                                            row["source"]["file"], row["source"]["line"], row["name"]))
    return {"connections": connections, "unresolved": unresolved, "dynamic": dynamic}
