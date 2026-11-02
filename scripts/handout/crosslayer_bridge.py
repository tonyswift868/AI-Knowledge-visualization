"""Resolve literal React/Python bridge methods from a pinned source tree.

This joins source evidence, rather than treating matching names as a call edge:
the frontend method string must reach a Python bridge registration, and the
registered handler must have a statically identifiable implementation.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
import re


METHOD_CALL = re.compile(r"\b(?:call|request|act)\s*(?:<[^\n>]+>)?\s*\(\s*(['\"])([^'\"]+)\1")
EVENT_ON = re.compile(r"\bon\s*\(\s*(['\"])([A-Za-z][\w.-]*)\1\s*,")
EVENT_PUSH = re.compile(r"\b(?:self\.)?bridge\.push\s*\(\s*(['\"])([A-Za-z][\w.-]*)\1")
TS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\b|"
    r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^\n]*?\)|[A-Za-z_$][\w$]*)\s*=>|"
    r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?useCallback\s*\("
)


def _endpoint(file: str, line: int, function: str) -> dict[str, object]:
    return {"file": file, "line": line, "function": function}


def _py_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _py_name(node.func)
    return "<inline>"


def _function_at(tree: ast.AST, line: int) -> str:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    choices = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= line <= end:
                choices.append((end - node.lineno, node.lineno, node))
    if not choices:
        return "module"
    node = min(choices)[2]
    names = [node.name]
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.ClassDef):
            names.insert(0, parent.name)
        parent = parents.get(parent)
    return ".".join(names)


def _ts_functions(lines: list[str]) -> list[tuple[int, int, str]]:
    found = []
    for number, text in enumerate(lines, 1):
        match = TS_FUNCTION.match(text)
        if match:
            name = next(group for group in match.groups() if group)
            depth = 0
            opened = False
            end = number
            for cursor in range(number - 1, len(lines)):
                # This bounded brace scan is deliberately conservative. If a
                # declaration has no block body, only its declaration line is
                # assigned; anonymous/nested callbacks are reported by callsite.
                row = lines[cursor].split("//", 1)[0]
                depth += row.count("{") - row.count("}")
                opened = opened or "{" in row
                end = cursor + 1
                if opened and depth <= 0:
                    break
            found.append((number, end, name))
    return found


def _ts_function_at(functions: list[tuple[int, int, str]], line: int) -> str:
    candidates = [(end - start, name) for start, end, name in functions if start <= line <= end]
    return min(candidates)[1] if candidates else "module/callsite"


def _lit(node: ast.AST | None, env: dict[str, object]) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, tuple, bool)):
        return node.value
    if isinstance(node, ast.Attribute):
        # Keep method references intact until the enclosing class is known.
        return node
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [_lit(item, env) for item in node.elts]
        return tuple(values) if all(value is not None for value in values) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _lit(node.left, env), _lit(node.right, env)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            k, v = _lit(key, env), value
            if not isinstance(k, str):
                return None
            result[k] = v
        return result
    return None


def _assignments(fn: ast.FunctionDef | ast.AsyncFunctionDef | None) -> dict[str, object]:
    env: dict[str, object] = {}
    if fn is None:
        return env
    pending = [fn]
    while pending:
        current = pending.pop()
        for child in ast.iter_child_nodes(current):
            if child is not current and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Assign):
                value = _lit(child.value, env)
                if value is not None:
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            env[target.id] = value
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                value = _lit(child.value, env)
                if value is not None:
                    env[child.target.id] = value
            pending.append(child)
    return env


def _resolve_target(node: ast.AST, env: dict[str, object], funcs: dict[str, list[ast.AST]], file: str, modules,
                   scope_class: str | None = None):
    """Return target, evidence rule, and evidenced intermediate endpoints."""
    def resolved(target, rule, via=None):
        return target, rule, via or []

    def flow_method_endpoint(method: str):
        # A `flow()` receiver is accepted only when its local factory creates
        # and returns PlanFlow instances; matching method names alone is weak.
        flow_factories = funcs.get("flow", [])
        if len(flow_factories) != 1:
            return None
        factory = flow_factories[0]
        creates = any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                      and call.func.id == "PlanFlow" for call in ast.walk(factory))
        returns_cached = any(isinstance(ret, ast.Return) and isinstance(ret.value, ast.Subscript)
                             and isinstance(ret.value.value, ast.Name) and ret.value.value.id == "flows"
                             for ret in ast.walk(factory))
        assigned_instance = any(isinstance(assign, ast.Assign)
                                and any(isinstance(target, ast.Subscript)
                                        and isinstance(target.value, ast.Name) and target.value.id == "flows"
                                        for target in assign.targets)
                                and isinstance(assign.value, ast.Call)
                                and isinstance(assign.value.func, ast.Name)
                                and assign.value.func.id == "PlanFlow"
                                for assign in ast.walk(factory))
        if not (creates and returns_cached and assigned_instance):
            return None
        item = modules.get(file)
        if not item:
            return None
        for cls in ast.walk(item[2]):
            if isinstance(cls, ast.ClassDef) and cls.name == "PlanFlow":
                for method_node in cls.body:
                    if isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and method_node.name == method:
                        return _endpoint(file, method_node.lineno, f"PlanFlow.{method}")
        return None

    def flow_calls(tree_node):
        found = []
        for call_node in ast.walk(tree_node):
            if (isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Attribute)
                    and isinstance(call_node.func.value, ast.Call)
                    and isinstance(call_node.func.value.func, ast.Name)
                    and call_node.func.value.func.id == "flow"):
                found.append((call_node.func.attr, call_node))
        return found

    expression = node
    if isinstance(expression, ast.Call) and _py_name(expression.func) not in {"<inline>", "register"}:
        # Common transparent wrappers such as wrap(fn), guarded(fn).
        if expression.args:
            expression = expression.args[0]
    if isinstance(expression, ast.Name) and isinstance(env.get(expression.id), ast.AST):
        expression = env[expression.id]
    if isinstance(expression, ast.Name):
        candidates = funcs.get(expression.id, [])
        if len(candidates) == 1:
            fn = candidates[0]
            endpoint = _endpoint(file, fn.lineno, fn.name)
            # plan_detail is a transparent wrapper around its verified flow()
            # factory and PlanFlow.detail implementation.
            if fn.name == "plan_detail":
                factory_vars = {target.id for assignment in ast.walk(fn) if isinstance(assignment, ast.Assign)
                                and isinstance(assignment.value, ast.Call)
                                and isinstance(assignment.value.func, ast.Name)
                                and assignment.value.func.id == "flow"
                                for target in assignment.targets if isinstance(target, ast.Name)}
                detail = any(isinstance(call_node, ast.Call) and isinstance(call_node.func, ast.Attribute)
                             and call_node.func.attr == "detail"
                             and isinstance(call_node.func.value, ast.Name)
                             and call_node.func.value.id in factory_vars for call_node in ast.walk(fn))
                actual = flow_method_endpoint("detail") if detail else None
                if actual:
                    return resolved(endpoint, "plan_detail wrapper delegates through verified flow() factory",
                                    [actual])
            return resolved(endpoint, fn.name)
        if len(candidates) > 1:
            return resolved(None, f"函数名 {expression.id} 在本文件有多个定义")
        return resolved(None, f"注册处理器 {expression.id} 未在同一文件静态定义")
    if isinstance(expression, ast.Attribute):
        if isinstance(expression.value, ast.Name) and expression.value.id == "self" and scope_class:
            item = modules.get(file)
            cls = next((candidate for candidate in ast.walk(item[2])
                        if isinstance(candidate, ast.ClassDef) and candidate.name == scope_class), None) if item else None
            method_node = next((child for child in cls.body
                                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and child.name == expression.attr), None) if cls else None
            if method_node:
                endpoint = _endpoint(file, method_node.lineno, f"{scope_class}.{expression.attr}")
                return resolved(endpoint, f"self.{expression.attr} resolves to a method declared on enclosing class {scope_class}")
        return resolved(None, f"处理器通过接收者属性 {expression.attr} 取得，实例类型未在此注册点静态绑定")
    if isinstance(expression, ast.Lambda):
        calls = flow_calls(expression.body)
        run_call = next((call_node for attr, call_node in calls if attr == "run"), None)
        callback_call = next((inner for attr, call_node in calls if attr == "run"
                              for argument in call_node.args if isinstance(argument, ast.Lambda)
                              for nested_attr, nested in flow_calls(argument.body)
                              for inner in [nested] if nested_attr != "run"), None)
        if run_call and callback_call:
            run_endpoint = flow_method_endpoint("run")
            callback_name = next((attr for attr, call_node in calls
                                  if attr != "run" and call_node is callback_call), None)
            callback_endpoint = flow_method_endpoint(callback_name) if callback_name else None
            if run_endpoint and callback_endpoint:
                return resolved(callback_endpoint,
                    "verified flow() factory returns PlanFlow; PlanFlow.run receives callback and invokes it only when run executes that callback",
                    [run_endpoint, callback_endpoint])
        direct_flow_method = next((attr for attr, _call in calls if attr != "run"), None)
        if direct_flow_method:
            endpoint = flow_method_endpoint(direct_flow_method)
            if endpoint:
                return resolved(endpoint, "verified flow() factory returns PlanFlow; direct method call",
                                [endpoint])
        call = next((n for n in ast.walk(expression.body) if isinstance(n, ast.Call)), None)
        if call:
            name = _py_name(call.func)
            if isinstance(call.func, ast.Attribute) and name != "sync_now":
                return resolved(None, f"lambda调用接收者属性 {name}，receiver没有经工厂/构造绑定证明")
            candidates = funcs.get(name, [])
            if len(candidates) == 1:
                fn = candidates[0]
                return resolved(_endpoint(file, fn.lineno, fn.name), f"lambda → {name}")
            if name == "sync_now":
                # window.py passes self to handlers.register, so this receiver
                # resolves to the MainWindow instance at this call site.
                item = modules.get("refactor/learning/app/window.py")
                endpoint = None
                if item:
                    endpoint = next((_endpoint("refactor/learning/app/window.py", child.lineno,
                                               f"{cls.name}.sync_now")
                                     for cls in ast.walk(item[2]) if isinstance(cls, ast.ClassDef)
                                     for child in cls.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                     and child.name == "sync_now"), None)
                if endpoint:
                    return resolved(endpoint, "lambda → window.sync_now; handlers.register(self.bridge, self) binds MainWindow")
        return resolved(None, "注册处理器是内联lambda，但其业务目标的接收者未能静态绑定")
    return resolved(_endpoint(file, getattr(expression, "lineno", 1), _py_name(expression)), "内联表达式未能静态归结到唯一函数")


def _local_functions(tree: ast.Module):
    result: dict[str, list[ast.AST]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name].append(node)
    return result


def _direct_scope_functions(scope, parents, fallback):
    """Prefer helpers lexically nested in this register() over same-named methods."""
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return fallback
    scoped = defaultdict(list)
    for node in ast.walk(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and parents.get(node) is scope:
            scoped[node.name].append(node)
    merged = dict(fallback)
    merged.update(scoped)
    return merged


def _registered(root: Path):
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    unresolved = []
    dynamic = []
    modules: dict[str, tuple[str, list[str], ast.Module, dict[str, list[ast.AST]]]] = {}
    # Runtime bridge registrations live in the app package. Tests contain
    # mock registrations with matching names, but are not runtime edges.
    for path in sorted((root / "refactor/learning/app").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            with path.open(encoding="utf-8") as stream:
                text = "".join(stream)
            tree = ast.parse(text, filename=rel)
        except (OSError, UnicodeError, SyntaxError):
            continue
        lines = text.splitlines()
        modules[rel] = (text, lines, tree, _local_functions(tree))

    for rel, (_text, lines, tree, functions) in modules.items():
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _py_name(node.func) != "register" or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) or _py_name(node.func.value) not in {"bridge", "self"}:
                continue
            enclosing = node
            while enclosing in parents and not isinstance(parents[enclosing], (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = parents[enclosing]
            scope = parents.get(enclosing)
            function = scope.name if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else "module"
            scope_class = None
            ancestor = scope
            while ancestor in parents:
                ancestor = parents[ancestor]
                if isinstance(ancestor, ast.ClassDef):
                    scope_class = ancestor.name
                    break
            env = _assignments(scope if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
            target_functions = _direct_scope_functions(scope, parents, functions)
            name_node = node.args[0]
            names = _lit(name_node, env)
            handler_expr = node.args[1] if len(node.args) > 1 else None
            registration_rows = []
            if isinstance(names, str):
                registration_rows.append((names, handler_expr, env))
            else:
                name_value = _py_name(name_node)
                loop = next((p for p in parents_path(node, parents) if isinstance(p, ast.For)), None)
                if loop:
                    iter_node = loop.iter
                    if (isinstance(iter_node, ast.Call) and _py_name(iter_node.func) == "items"
                            and isinstance(iter_node.func, ast.Attribute)):
                        mapping = _lit(iter_node.func.value, env)
                        if isinstance(mapping, dict):
                            for key, value in mapping.items():
                                local = dict(env)
                                if isinstance(loop.target, ast.Tuple) and len(loop.target.elts) == 2:
                                    for target, v in zip(loop.target.elts, (key, value)):
                                        if isinstance(target, ast.Name):
                                            local[target.id] = v
                                registration_rows.append((key, handler_expr, local))
                    else:
                        values = _lit(iter_node, env)
                        if isinstance(values, tuple):
                            targets = loop.target.elts if isinstance(loop.target, ast.Tuple) else [loop.target]
                            for value in values:
                                local = dict(env)
                                if isinstance(loop.target, ast.Tuple) and isinstance(value, tuple) and len(value) == len(targets):
                                    for target, item in zip(targets, value):
                                        if isinstance(target, ast.Name):
                                            local[target.id] = item
                                elif len(targets) == 1 and isinstance(targets[0], ast.Name):
                                    local[targets[0].id] = value
                                name = _lit(name_node, local)
                                if isinstance(name, str):
                                    registration_rows.append((name, handler_expr, local))
                if not registration_rows:
                    dynamic.append({"kind": "bridge", "name": ast.unparse(name_node),
                        "source": _endpoint(rel, name_node.lineno, function),
                        "reason": f"Python bridge.register 名称表达式 {ast.unparse(name_node)} 由运行时提供"})
            for name, handler, local in registration_rows:
                if handler is None:
                    continue
                target, rule, extra_via = _resolve_target(handler, local, target_functions, rel, modules, scope_class)
                registration = _endpoint(rel, node.lineno, function)
                row = dict(kind="bridge", name=name, source=registration, target=target,
                           via=[registration, *extra_via], rule=f"bridge.register 字面量注册；{rule}")
                if target is None:
                    unresolved.append({"kind": "bridge", "name": name, "source": registration,
                                       "reason": row["rule"]})
                result[name].append(row)
    return result, unresolved, dynamic, modules


def parents_path(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    current = node
    while current in parents:
        current = parents[current]
        yield current


def _ts_entry(root: Path, path: Path, function_name: str):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        lines = list(stream)
    match = re.compile(rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\b")
    for line_no, line in enumerate(lines, 1):
        if match.search(line):
            return _endpoint(path.relative_to(root).as_posix(), line_no, function_name)
    return None


def _ts_assignment_entry(root: Path, path: Path, identifier: str, marker: str):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if identifier in line and marker in line:
                return _endpoint(path.relative_to(root).as_posix(), line_no, identifier)
    return None


def _ts_callable_entry(root: Path, path: Path, identifier: str):
    if not path.is_file():
        return None
    pattern = re.compile(rf"^\s*(?:(?:export\s+)?(?:async\s+)?function\s+{re.escape(identifier)}\b|(?:const|let)\s+{re.escape(identifier)}\s*=)")
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if pattern.search(line):
                return _endpoint(path.relative_to(root).as_posix(), line_no, identifier)
    return None


def _py_entry(modules, file: str, function_name: str):
    item = modules.get(file)
    if not item:
        return None
    class_name, _, method_name = function_name.partition(".")
    for node in ast.walk(item[2]):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return _endpoint(file, child.lineno, function_name)
    return None


def _py_line(modules, file: str, function_name: str):
    item = modules.get(file)
    if not item:
        return None
    for node in ast.walk(item[2]):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return _endpoint(file, node.lineno, function_name)
    return None


def _frontend_calls(root: Path):
    calls: dict[str, list[dict[str, object]]] = defaultdict(list)
    event_listeners: dict[str, list[dict[str, object]]] = defaultdict(list)
    dynamic = []
    file = root / "refactor/learning/web"
    for path in sorted(file.rglob("*.ts")) + sorted(file.rglob("*.tsx")):
        rel = path.relative_to(root).as_posix()
        with path.open(encoding="utf-8") as stream:
            lines = list(stream)
        full_text = "".join(lines)
        bridge_call = bool(re.search(r"import\s*\{[^}]*\bcall\b[^}]*\}\s*from\s*['\"][^'\"]*bridge", full_text, re.S))
        bridge_act = bool(re.search(r"\bconst\s+act\s*=\s*React\.useCallback", full_text)
                          or re.search(r"(?:async\s+)?function\s+act\s*\([^)]*method[^)]*\)\s*\{[^}]*\bcall\s*\(\s*method\b", full_text, re.S))
        functions = _ts_functions(lines)
        for number, line in enumerate(lines, 1):
            if line.lstrip().startswith(("//", "/*", "*")):
                continue
            for match in re.finditer(r"\b(call|request|act)\s*(?:<[\w{}, <>|?:.\[\]]+>)?\s*\(\s*([^,)]*)", line):
                if re.search(r"\bfunction\s*$", line[:match.start()]):
                    continue
                method_kind = match.group(1)
                if method_kind == "call" and not bridge_call:
                    continue
                if method_kind == "act" and not bridge_act:
                    continue
                expression = match.group(2).strip()
                if re.fullmatch(r"(['\"])[^'\"]+\1", expression):
                    continue
                literal_methods = re.findall(r"(['\"])([A-Za-z][\w.-]+)\1", expression)
                if literal_methods:
                    for _quote, literal_name in literal_methods:
                        calls[literal_name].append({"file": rel, "line": number,
                            "function": _ts_function_at(functions, number),
                            "call_kind": f"{method_kind}(literal alternative)"})
                    continue
                if re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", expression):
                    dynamic.append({"kind": "bridge", "name": expression,
                                    "source": _endpoint(rel, number, _ts_function_at(functions, number)),
                                    "reason": f"{method_kind} 的方法实参表达式 {expression} 由上游运行时提供"})
            for match in METHOD_CALL.finditer(line):
                if not bridge_call:
                    continue
                name = match.group(2)
                function = _ts_function_at(functions, number)
                calls[name].append({"file": rel, "line": number, "function": function, "call_kind": match.group(0).split("(", 1)[0].strip()})
            if bridge_act and "act(" in line:
                first_arg = line.split("act(", 1)[1].split(",", 1)[0]
                for name_match in re.finditer(r"(['\"])(classroom\.[A-Za-z0-9_.-]+)\1", first_arg):
                    name = name_match.group(2)
                    calls[name].append({"file": rel, "line": number,
                                        "function": _ts_function_at(functions, number),
                                        "call_kind": "act(literal method argument)"})
            for match in EVENT_ON.finditer(line):
                callback = ""
                suffix = line[match.end():]
                if "=>" in suffix:
                    body = suffix.split("=>", 1)[1]
                    invoked = re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", body)
                    if invoked:
                        callback = f" effect callback → {invoked[-1]}"
                event_listeners[match.group(2)].append(
                    _endpoint(rel, number, _ts_function_at(functions, number) + callback))
    return calls, event_listeners, dynamic


def _push_events(modules):
    result = defaultdict(list)
    for rel, (_text, lines, tree, _functions) in modules.items():
        for line_no, line in enumerate(lines, 1):
            for match in EVENT_PUSH.finditer(line):
                function = _function_at(tree, line_no)
                result[match.group(2)].append(_endpoint(rel, line_no, function))
    return result


def extract(root: Path) -> dict[str, list[dict[str, object]]]:
    """Return proved literal bridge requests/events plus unresolved dispatch sites."""
    root = Path(root)
    registered, unresolved, dynamic, modules = _registered(root)
    frontend, event_listeners, frontend_dynamic = _frontend_calls(root)
    dynamic.extend(frontend_dynamic)
    ts_call = _ts_entry(root, root / "refactor/learning/web/src/bridge.ts", "call")
    py_call = _py_entry(modules, "refactor/learning/app/bridge.py", "Bridge.call")
    py_run = _py_entry(modules, "refactor/learning/app/bridge.py", "Bridge._run")
    connections = []
    for name, callers in frontend.items():
        handlers = registered.get(name, [])
        for caller in callers:
            source = _endpoint(caller["file"], caller["line"], caller["function"])
            if not handlers:
                unresolved.append({"kind": "bridge", "name": name, "source": source,
                    "reason": "网页调用了字面量方法名，但快照内未找到对应Python bridge.register绑定"})
                continue
            for registration in handlers:
                if registration["target"] is None:
                    unresolved.append({"kind": "bridge", "name": name, "source": registration["source"],
                                       "reason": registration["rule"]})
                    continue
                connections.append(dict(kind="bridge", name=name, source=source,
                    target=registration["target"],
                    via=[source, *([ts_call] if ts_call else []),
                         *([_ts_callable_entry(root, root / caller["file"], "act")] if caller["call_kind"].startswith("act") and _ts_callable_entry(root, root / caller["file"], "act") else []),
                         *([py_call] if py_call else []), *([py_run] if py_run else []), *registration["via"]],
                    rule=f"前端 {caller['call_kind']} 字符串与 Python bridge.register 方法字面量完全相等；注册目标为 {registration['rule']}"))
    # Keep statically registered methods that have no literal frontend call as
    # one-sided evidence. A runtime method variable does not prove which of
    # these registrations it will select.
    for name, registrations in registered.items():
        if name in frontend:
            continue
        for registration in registrations:
            unresolved.append({"kind": "bridge", "name": name, "source": registration["source"],
                               "reason": "只找到Python bridge.register字面量注册；网页侧没有对应字面量调用"})
    pushed = _push_events(modules)
    for name, sources in pushed.items():
        listeners = event_listeners.get(name, [])
        push_call = sources[0] if sources else None
        qt_prefix = []
        if name == "plans_changed":
            sync = modules.get("refactor/learning/app/sync.py")
            if sync:
                emit_line = next((i for i, line in enumerate(sync[1], 1) if "_remote_plans.emit()" in line), None)
                connect_line = next((i for i, line in enumerate(sync[1], 1)
                                     if "_remote_plans.connect(self._plans_changed" in line), None)
                slot_line = next((i for i, line in enumerate(sync[1], 1)
                                  if line.lstrip().startswith("def _plans_changed(")), None)
                if emit_line and connect_line and slot_line:
                    sources = [_endpoint("refactor/learning/app/sync.py", emit_line,
                                         _function_at(sync[2], emit_line))]
                    qt_prefix = [_endpoint("refactor/learning/app/sync.py", connect_line,
                                           "Qt signal _remote_plans.connect(_plans_changed)"),
                                 _endpoint("refactor/learning/app/sync.py", slot_line, "WindowSync._plans_changed")]
        for source in sources:
            if listeners:
                for target in listeners:
                    push_endpoint = _py_line(modules, "refactor/learning/app/bridge.py", "push")
                    connections.append(dict(kind="bridge", name=name, source=source, target=target,
                        via=[source, *qt_prefix, *([push_call] if push_call else []),
                             *([push_endpoint] if push_endpoint else []), target],
                        rule="Python bridge.push 字面量事件名与前端 on 字面量监听名相等；事件/监听回调连接，不是函数直接调用"))
            else:
                unresolved.append({"kind": "bridge", "name": name, "source": source,
                    "reason": "找到Python bridge.push字面量事件，但快照网页中无同名字面量 on 监听"})
    # RecallQuestion's request prop receives the parent's function that forwards
    # the method string unchanged to call(); this is a statically explicit prop.
    recall = root / "refactor/learning/web/src/classroom/RecallQuestion.tsx"
    classroom_page = root / "refactor/learning/web/src/pages/Classroom.tsx"
    if recall.is_file() and classroom_page.is_file():
        with recall.open(encoding="utf-8") as stream:
            recall_lines = list(stream)
        with classroom_page.open(encoding="utf-8") as stream:
            classroom_lines = list(stream)
        component_calls = []
        for line_no, line in enumerate(recall_lines, 1):
            for match in re.finditer(r"request(?:<[^>]+>)?\s*\(\s*(['\"])(class\.recall_[\w]+)\1", line):
                component_calls.append((match.group(2), line_no, _ts_function_at(_ts_functions(recall_lines), line_no)))
        prop = next((i for i, line in enumerate(classroom_lines, 1) if "request={recallRequest}" in line), None)
        if prop:
            caller_funcs = _ts_functions(classroom_lines)
            forward_line = next((i for i, line in enumerate(classroom_lines, 1)
                                 if "const recallRequest" in line), None)
            for name, line_no, fn in component_calls:
                for registration in registered.get(name, []):
                    target = registration["target"]
                    if target:
                        source = _endpoint("refactor/learning/web/src/classroom/RecallQuestion.tsx", line_no, fn)
                        via = [source,
                               _endpoint("refactor/learning/web/src/pages/Classroom.tsx", prop, _ts_function_at(caller_funcs, prop))]
                        if forward_line:
                            via.append(_endpoint("refactor/learning/web/src/pages/Classroom.tsx", forward_line, "recallRequest"))
                        via.extend(registration["via"])
                        connections.append(dict(kind="bridge", name=name, source=source,
                            target=target, via=via,
                            rule="RecallQuestion.request prop 由 Classroom 的 recallRequest 提供；该函数把method原样传给call，Python methods映射注册此键"))
                    else:
                        unresolved.append({"kind": "bridge", "name": name, "source": registration["source"], "reason": registration["rule"]})
    key = lambda row: (row["name"], row["source"]["file"], row["source"]["line"],
                       (row.get("target") or {}).get("file", ""), (row.get("target") or {}).get("line", 0))
    unique = {key(row): row for row in connections}
    connections = sorted(unique.values(), key=key)
    unresolved = sorted({(row["kind"], row["name"], row["source"]["file"], row["source"]["line"], row["reason"]): row
                         for row in unresolved}.values(), key=key)
    dynamic = sorted({(row["kind"], row["name"], row["source"]["file"], row["source"]["line"], row["reason"]): row
                      for row in dynamic}.values(), key=key)
    return {"connections": connections, "unresolved": unresolved, "dynamic": dynamic}
