"""Link statically named HTTP calls to in-repository HTTP route handlers.

This is a deliberately small source reader: Python AST handles literals and
f-strings; bounded annotation parsing handles Spring and FastAPI routes. It
does not infer a server from a host name, and unresolved paths remain visible.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
import re
from typing import Any


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
METHOD_CALLS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}


def _text(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        return "".join(stream)


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        return [line.rstrip("\r\n") for line in stream]


def _norm(path: str) -> str:
    path = re.sub(r"\{[^{}]*\}", "{}", path)
    path = re.sub(r"//+", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


class _Static:
    def __init__(self, tree: ast.Module):
        self.values: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                resolved = self.value(value) if value is not None else None
                if resolved is not None:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.values[target.id] = resolved

    def value(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        if isinstance(node, ast.Attribute):
            # For Java-like module constants surfaced through generated code,
            # only accept the already-known final identifier.
            return self.values.get(node.attr)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.value(node.left), self.value(node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr):
            parts = []
            for item in node.values:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    parts.append(item.value)
                elif isinstance(item, ast.FormattedValue):
                    static = self.value(item.value)
                    parts.append(static if static is not None else "{}")
                else:
                    return None
            return "".join(parts)
        return None


def _java_prefixes(java_files: list[Path]) -> dict[str, str]:
    constants: dict[str, str] = {}
    declarations = re.compile(
        r"(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?String\s+([A-Za-z_]\w*)\s*=\s*\"([^\"]*)\""
    )
    for path in java_files:
        text = _text(path)
        class_match = re.search(r"\bclass\s+(\w+)", text)
        class_name = class_match.group(1) if class_match else ""
        for line in text.splitlines():
            for match in declarations.finditer(line):
                name, value = match.group(1), match.group(2)
                if class_name:
                    constants[f"{class_name}.{name}"] = value
                if name not in constants:
                    constants[name] = value
                elif constants[name] != value:
                    constants.pop(name, None)
    return constants


def _java_value(expression: str, constants: dict[str, str]) -> str | None:
    match = re.fullmatch(r"\s*\"([^\"]*)\"\s*", expression)
    if match:
        return match.group(1)
    match = re.fullmatch(r"\s*(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*", expression)
    if match:
        exact = re.sub(r"\s+", "", expression)
        return constants.get(exact, constants.get(match.group(1)))
    return None


def _mapping_values(args: str, constants: dict[str, str]) -> list[str]:
    simple = re.fullmatch(r'\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*', args)
    if simple:
        return [simple.group(1).replace('\\"', '"')]
    property_match = re.search(r'(?:path|value)\s*=\s*(\{[^}]*\}|"[^\"]*")', args)
    if property_match:
        args = property_match.group(1)
    elif "=" in args:
        return []
    strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', args)
    if strings:
        return [value.replace('\\"', '"') for value in strings]
    value = _java_value(args, constants)
    return [value] if value is not None else []


def _java_routes(root: Path) -> list[dict[str, Any]]:
    java_files = sorted((root / "refactor/server-java").rglob("*.java"))
    if not java_files:
        return []
    constants = _java_prefixes(java_files)
    routes: list[dict[str, Any]] = []
    class_pattern = re.compile(r"\bclass\s+(\w+)")
    class_map_pattern = re.compile(r"@RequestMapping\s*\(([^)]*)\)")
    method_map_pattern = re.compile(r"@(Get|Post|Put|Patch|Delete)Mapping\s*(?:\(([^)]*)\))?")
    generic_pattern = re.compile(r"@RequestMapping\s*\(([^)]*)\)")
    method_decl_pattern = re.compile(r"\b(?:public|protected)\s+[\w<>?,.\[\] ]+?\s+(\w+)\s*\(")
    generic_http = re.compile(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)")

    for file in java_files:
        lines = _read_lines(file)
        rel = file.relative_to(root).as_posix()
        class_prefix = ""
        class_found = False
        for index, line in enumerate(lines):
            class_hit = class_pattern.search(line)
            if class_hit:
                class_found = True
                # Find the nearest class annotation before the class header.
                for before in range(max(0, index - 8), index):
                    hit = class_map_pattern.search(lines[before])
                    if hit:
                        value = _java_value(hit.group(1), constants)
                        if value is not None:
                            class_prefix = value
                continue
            if not class_found:
                continue

            mappings: list[tuple[str, list[str]]] = []
            for hit in method_map_pattern.finditer(line):
                method = hit.group(1).upper()
                values = _mapping_values(hit.group(2) or "", constants)
                mappings.append((method, values or [""]))
            for hit in generic_pattern.finditer(line):
                args = hit.group(1)
                request_methods = sorted(set(generic_http.findall(args)))
                # A generic RequestMapping with no method is an unrestricted
                # routing filter/alias, not one exact HTTP endpoint.
                if not request_methods:
                    continue
                vals = _mapping_values(args, constants) or [""]
                mappings.extend((method, vals) for method in request_methods)
            if not mappings:
                continue
            method_name = None
            method_line = None
            for next_index in range(index, min(len(lines), index + 12)):
                declaration = method_decl_pattern.search(lines[next_index])
                if declaration:
                    method_name = declaration.group(1)
                    method_line = next_index + 1
                    break
                if next_index > index and ("@GetMapping" in lines[next_index] or "@PostMapping" in lines[next_index]
                    or "@PutMapping" in lines[next_index] or "@PatchMapping" in lines[next_index]
                    or "@DeleteMapping" in lines[next_index] or "@RequestMapping" in lines[next_index]):
                    break
            if method_name is None:
                continue
            for verb, values in mappings:
                for value in values:
                    full = _norm(class_prefix.rstrip("/") + "/" + value.lstrip("/"))
                    routes.append(dict(method=verb, path=full,
                        endpoint=dict(file=rel, line=method_line, function=method_name),
                        declaration=dict(file=rel, line=index + 1, function=method_name)))
    return routes


def _python_routes(root: Path) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for file in sorted((root / "refactor/server").rglob("*.py")):
        try:
            tree = ast.parse(_text(file), filename=str(file))
        except SyntaxError:
            continue
        static = _Static(tree)
        routers: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            constructor = value.func.id if isinstance(value.func, ast.Name) else ""
            if constructor not in {"APIRouter", "FastAPI"}:
                continue
            prefix = ""
            for kw in value.keywords:
                if kw.arg == "prefix":
                    prefix = static.value(kw.value) or ""
            for target in targets:
                if isinstance(target, ast.Name):
                    routers[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                router_name = decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
                method = decorator.func.attr.upper()
                if router_name not in routers or method not in HTTP_METHODS:
                    continue
                value = static.value(decorator.args[0]) if decorator.args else ""
                if value is None:
                    continue
                path = _norm(routers[router_name].rstrip("/") + "/" + value.lstrip("/"))
                routes.append(dict(method=method, path=path,
                    endpoint=dict(file=file.relative_to(root).as_posix(), line=node.lineno, function=node.name),
                    declaration=dict(file=file.relative_to(root).as_posix(), line=decorator.lineno, function=node.name)))
    return routes


def _call_name(node: ast.Call) -> tuple[str, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        receiver = ast.unparse(func.value) if hasattr(ast, "unparse") else ""
        return func.attr, receiver
    if isinstance(func, ast.Name):
        return func.id, ""
    return "", ""


def _python_candidates(root: Path, route_lookup: dict[tuple[str, str], list[dict[str, Any]]]):
    candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    files = sorted((root / "refactor").rglob("*.py"))
    parsed: dict[str, tuple[ast.Module, _Static, list[str]]] = {}
    function_nodes: dict[tuple[str, str], ast.AST] = {}
    for file in files:
        rel = file.relative_to(root).as_posix()
        try:
            text = _text(file)
            tree = ast.parse(text, filename=str(file))
        except (SyntaxError, UnicodeError):
            continue
        static = _Static(tree)
        parsed[rel] = (tree, static, text.splitlines())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_nodes[(rel, node.name)] = node

    # Index calls to helper wrappers so dynamic parameter values can be linked
    # one callsite outward when the caller supplies a literal route.
    callers: dict[tuple[str, str], list[tuple[str, ast.Call, ast.AST]]] = defaultdict(list)
    for rel, (tree, _static, _lines) in parsed.items():
        current_functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                current_functions.append(node.name)
                self.generic_visit(node)
                current_functions.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                name, _receiver = _call_name(node)
                if name:
                    callers[(rel, name)].append((current_functions[-1] if current_functions else "<module>", node, node.func))
                self.generic_visit(node)

        Visitor().visit(tree)

    for rel, (tree, static, _lines) in parsed.items():
        current_functions: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                current_functions.append(node.name)
                self.generic_visit(node)
                current_functions.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                name, receiver = _call_name(node)
                method_node = None
                path_node = None
                if name == "request_json":
                    if len(node.args) >= 2:
                        method_node, path_node = node.args[:2]
                    else:
                        method_node = next((kw.value for kw in node.keywords if kw.arg == "method"), None)
                        path_node = next((kw.value for kw in node.keywords if kw.arg in {"path", "url"}), None)
                elif name in {"stream", "request"} and len(node.args) >= 2:
                    method_node, path_node = node.args[:2]
                    if name == "stream" and "_client" not in receiver and "client" not in receiver.lower():
                        method_node = path_node = None
                elif name in METHOD_CALLS and node.args and (
                    "http" in receiver.lower() or "client" in receiver.lower() or name == "request"
                ):
                    method_node = ast.Constant(METHOD_CALLS[name])
                    path_node = node.args[0]
                if method_node is None or path_node is None:
                    self.generic_visit(node)
                    return
                method = static.value(method_node)
                path = static.value(path_node)
                source_variants = [(node, current_functions[-1] if current_functions else "<module>", method, path, [])]
                if current_functions and (method is None or path is None):
                    # Follow a common request wrapper's first positional route
                    # argument back to a literal caller, preserving both source
                    # locations in the emitted chain.
                    wrapper = current_functions[-1]
                    definitions = [n for (file, nname), n in function_nodes.items()
                                   if file == rel and nname == wrapper]
                    if definitions:
                        params = definitions[0].args.args
                        def parameter_index(expression: ast.AST | None) -> int | None:
                            names = {child.id for child in ast.walk(expression) if isinstance(child, ast.Name)} if expression else set()
                            return next((i for i in range(len(params) - 1, -1, -1) if params[i].arg in names), None)

                        path_index = parameter_index(path_node)
                        method_index = parameter_index(method_node)
                        if path_index is not None:
                            resolved = []
                            for parent_name, call, _ in callers.get((rel, wrapper), []):
                                # Bound method calls do not pass ``self`` as a
                                # positional argument; free-function wrappers
                                # do not have that offset.
                                offset = 1 if params and params[0].arg in {"self", "cls"} else 0
                                caller_path_index = path_index - offset
                                caller_method_index = method_index - offset if method_index is not None else None
                                parent_path_node = call.args[caller_path_index] if caller_path_index >= 0 and len(call.args) > caller_path_index else None
                                parent_method_node = call.args[caller_method_index] if caller_method_index is not None and caller_method_index >= 0 and len(call.args) > caller_method_index else method_node
                                parent_path = static.value(parent_path_node)
                                parent_method = static.value(parent_method_node)
                                if parent_path is None or parent_method is None:
                                    continue
                                resolved.append((call, parent_name, parent_method, parent_path,
                                    [dict(file=rel, line=node.lineno, function=wrapper)]))
                            if resolved:
                                source_variants = resolved
                emitted_dynamic = False
                for source_node, source_function, method, path, via in source_variants:
                    if method is None or not isinstance(method, str) or method.upper() not in HTTP_METHODS:
                    # A generic outbound wrapper usually receives a variable
                    # method. Keep it as a dynamic point only for app paths.
                        if path and path.startswith("/api/"):
                            dynamic.append(dict(kind="http", name=path, source=dict(file=rel, line=source_node.lineno,
                                function=source_function), reason="HTTP 方法不是静态字符串，无法确定接收路由"))
                        continue
                    if path is None:
                        dynamic.append(dict(kind="http", name="(动态路径)", source=dict(file=rel, line=source_node.lineno,
                            function=source_function), reason="调用参数的 HTTP 路径不是静态路径"))
                        continue
                    path = path.split("?", 1)[0]
                    normalized = _norm(path)
                    if not normalized.startswith("/api/") and not normalized.startswith("/health/"):
                        continue
                    verb = method.upper()
                    endpoints = route_lookup.get((verb, normalized), [])
                    source = dict(file=rel, line=source_node.lineno, function=source_function)
                    name_text = f"{verb} {normalized}"
                    if not endpoints:
                        unresolved.append(dict(kind="http", name=name_text, source=source,
                            reason="源码中没有找到方法和规范化路径均相同的 Java/FastAPI 路由"))
                    else:
                        for route in endpoints:
                            candidates.append(dict(kind="http", name=name_text, source=source,
                                target=route["endpoint"], via=via,
                                rule="调用点的静态 HTTP 方法/路径与 Spring/FastAPI 注解路径相同；{} 路径参数统一配对".format(
                                    "动态值" if "{}" in normalized else "无"), route_declaration=route["declaration"]))
                self.generic_visit(node)

        Visitor().visit(tree)
    return candidates, unresolved, dynamic


def extract(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Return statically verifiable Python HTTP-to-controller connections."""
    root = Path(root)
    routes = _java_routes(root) + _python_routes(root)
    route_lookup: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for route in routes:
        route_lookup[(route["method"], _norm(route["path"]))].append(route)
    connections, unresolved, dynamic = _python_candidates(root, route_lookup)
    connections.sort(key=lambda row: (row["target"]["file"], row["source"]["file"], row["source"]["line"], row["name"]))
    unresolved.sort(key=lambda row: (row["source"]["file"], row["source"]["line"], row["name"]))
    dynamic.sort(key=lambda row: (row["source"]["file"], row["source"]["line"], row["name"]))
    return {"connections": connections, "unresolved": unresolved, "dynamic": dynamic}
