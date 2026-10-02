"""Small, isolated Python example runner used by fix-error review.

The child process receives only the submitted source over stdin.  Its AST
allowlist rejects imports, loops, comprehensions, arbitrary calls, file and
network access before execution; the child also has empty builtins and an
audit hook.  This is deliberately a teaching subset, not a general sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import json
import re
import subprocess
import sys

from .planner_agents import AgentFormatError


class CodeRunError(AgentFormatError):
    """The submitted example cannot be safely executed or timed out."""


@dataclass(frozen=True)
class CodeResult:
    prints: tuple[str, ...]
    values: dict[str, str]

    @property
    def output(self) -> str:
        if self.prints:
            return "\n".join(self.prints)
        return "末态 " + json.dumps(self.values, ensure_ascii=False, sort_keys=True)


_SAFE = (ast.Module, ast.Assign, ast.Expr, ast.Name, ast.Store, ast.Load, ast.Constant,
         ast.Tuple, ast.List, ast.Dict, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub,
         ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Call, ast.Attribute,
         ast.Try, ast.ExceptHandler, ast.Pass, ast.Subscript, ast.Slice, ast.Compare,
         ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.BoolOp, ast.And,
         ast.Or, ast.If, ast.IfExp)
_LIST_METHODS = frozenset(('append', 'extend', 'insert', 'remove', 'pop', 'sort',
                           'reverse', 'clear', 'copy', 'count', 'index'))


def _safe_script(tree: ast.AST) -> bool:
    nodes = list(ast.walk(tree))
    if len(nodes) > 180 or any(not isinstance(node, _SAFE) for node in nodes):
        return False
    for node in nodes:
        if isinstance(node, ast.Constant) and (
                isinstance(node.value, (int, float)) and abs(node.value) > 1_000_000
                or isinstance(node.value, str) and len(node.value) > 1000):
            return False
        if isinstance(node, ast.Attribute) and (
                not isinstance(node.value, ast.Name) or node.attr not in _LIST_METHODS):
            return False
        if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id in ('print', 'len', 'str', 'sorted', 'type')
                or isinstance(node.func, ast.Attribute) and node.func.attr in _LIST_METHODS):
            return False
        if isinstance(node, ast.ExceptHandler) and not (
                node.type is None or isinstance(node.type, ast.Name)
                and node.type.id in ('ValueError', 'IndexError', 'TypeError')):
            return False
    return True


_RUNNER = r'''
import sys,json
packet=json.loads(sys.stdin.read())
prints=[]
def capture(*args):
    prints.append(' '.join(map(str,args)))
def deny(event,args):
    if event == 'open' or event.startswith(('socket.','os.','subprocess.')):
        raise RuntimeError('lesson I/O prohibited')
sys.addaudithook(deny)
namespace={'__builtins__':{},'print':capture,'len':len,'str':str,'sorted':sorted,'type':type,
           'ValueError':ValueError,'IndexError':IndexError,'TypeError':TypeError}
try:
    exec(compile(packet['code'],'<fix-error-example>','exec'),namespace,namespace)
    values={k:repr(v) for k,v in namespace.items() if k!='__builtins__' and not callable(v)}
    print(json.dumps({'prints':prints,'values':values},ensure_ascii=False))
except BaseException as e:
    print(json.dumps({'error':type(e).__name__},ensure_ascii=False))
'''


def run_python(code: str, *, timeout: float = 2.0) -> CodeResult:
    if not isinstance(code, str) or not code.strip():
        raise CodeRunError('代码为空，无法验算')
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        raise CodeRunError('代码语法无法解析') from None
    if not _safe_script(tree):
        raise CodeRunError('代码超出可隔离运行的教学子集')
    try:
        child = subprocess.run(
            [sys.executable, '-I', '-B', '-X', 'utf8', '-c', _RUNNER],
            input=json.dumps({'code': code}, ensure_ascii=False),
            capture_output=True, text=True, encoding='utf-8', timeout=timeout, check=False)
        data = json.loads(child.stdout)
    except subprocess.TimeoutExpired:
        raise CodeRunError('代码运行超时') from None
    except (ValueError, OSError):
        raise CodeRunError('代码运行失败') from None
    if child.returncode or 'error' in data:
        raise CodeRunError('代码运行失败：' + str(data.get('error', '子进程失败')))
    prints = data.get('prints')
    values = data.get('values')
    if not isinstance(prints, list) or not isinstance(values, dict):
        raise CodeRunError('代码运行结果格式无效')
    return CodeResult(tuple(str(v) for v in prints), {str(k): str(v) for k, v in values.items()})


def extract_code(text: str) -> str | None:
    """Return the first fenced Python block, or a marked raw code string."""
    if not isinstance(text, str):
        return None
    match = re.search(r"```(?:python|py)\s*\n(.*?)```", text, re.S | re.I)
    return match.group(1).strip() if match else None


def extract_work_code(lines) -> str | None:
    """Collect short raw code steps when a model did not use code fences."""
    if isinstance(lines, str):
        lines = [lines]
    snippets = []
    for line in lines or ():
        fenced = extract_code(str(line))
        if fenced:
            snippets.append(fenced)
            continue
        text = str(line)
        match = re.search(r'(?:^|[：:]\s*)([A-Za-z_]\w*\s*=|print\()', text)
        if not match:
            match = re.search(r'([A-Za-z_]\w*\s*=|print\()', text)
        if not match:
            continue
        value = text[match.start(1):]
        value = re.split(r'[，。；;]|(?<=\))\s*[\u3400-\u9fff]', value)[0].strip()
        if value:
            snippets.append(value)
    return '\n'.join(snippets) if snippets else None


def expected_output(value: str) -> str:
    value = str(value).strip()
    value = re.sub(r'^\s*(?:输出|结果)(?:为|是|：|:)\s*', '', value)
    if value.startswith('```') and value.endswith('```'):
        value = value[3:-3].strip()
    return value


def verify_fix_error_code(wrong_code: str, wrong_result: str,
                          correct_code: str, correct_result: str,
                          *, timeout: float = 2.0) -> tuple[bool, str]:
    """Run both versions and require exact declared outputs."""
    try:
        wrong = run_python(wrong_code, timeout=timeout)
        fixed = run_python(correct_code, timeout=timeout)
    except CodeRunError as exc:
        return False, str(exc)
    expected_wrong = expected_output(wrong_result)
    expected_fixed = expected_output(correct_result)
    if wrong.output.strip() != expected_wrong:
        return False, '错误代码实测输出与wrong_result不一致'
    if fixed.output.strip() != expected_fixed:
        return False, '改正代码实测输出与correct_result不一致'
    return True, '两段代码输出均与声明一致'


__all__ = ['CodeResult', 'CodeRunError', 'run_python', 'extract_code', 'extract_work_code',
           'verify_fix_error_code']
