"""Conservative exact-answer comparisons, using only the standard library.

None means outside the supported grammar, never an inferred wrong answer.
Polynomial identities are proved with rational coefficients, not sampling.
No student code is executed. Rational functions and transcendental functions
are left to the judge. Process questions are left to the process-aware judge.
"""
from __future__ import annotations

import ast
from fractions import Fraction
import re
import unicodedata


def normalize(value):
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


def _poly(text):
    text = normalize(text).replace('−', '-').replace('×', '*').replace('÷', '/').replace('^', '**')
    text = re.sub(r'(?<=\d)(?=[a-z(])|(?<=\))(?=[a-z0-9(])', '*', text)
    tree = ast.parse(text, mode='eval')
    if len(list(ast.walk(tree))) > 100: raise ValueError()

    def add(a, b):
        out = dict(a)
        for key, value in b.items(): out[key] = out.get(key, Fraction(0)) + value
        return {k: v for k, v in out.items() if v}

    def mul(a, b):
        out = {}
        for ka, va in a.items():
            for kb, vb in b.items():
                key = tuple(sorted(ka + kb))
                if len(key) > 6: raise ValueError()
                out[key] = out.get(key, Fraction(0)) + va * vb
        if len(out) > 100: raise ValueError()
        return {k: v for k, v in out.items() if v}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return {(): Fraction(ast.get_source_segment(text, node))}
        if isinstance(node, ast.Name) and len(node.id) == 1 and node.id.isascii():
            return {(node.id,): Fraction(1)}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return {k: v * (-1 if isinstance(node.op, ast.USub) else 1) for k, v in visit(node.operand).items()}
        if not isinstance(node, ast.BinOp): raise ValueError()
        a, b = visit(node.left), visit(node.right)
        if isinstance(node.op, ast.Add): return add(a, b)
        if isinstance(node.op, ast.Sub): return add(a, {k: -v for k, v in b.items()})
        if isinstance(node.op, ast.Mult): return mul(a, b)
        if isinstance(node.op, ast.Div) and set(b) == {()} and b[()]:
            return {k: v / b[()] for k, v in a.items()}
        if isinstance(node.op, ast.Pow) and not (set(b) - {()}):
            exponent = b.get((), Fraction(0))
            if exponent.denominator != 1 or not 0 <= exponent <= 6: raise ValueError()
            out = {(): Fraction(1)}
            for _ in range(int(exponent)): out = mul(out, a)
            return out
        raise ValueError()
    return {k: v for k, v in visit(tree.body).items() if v}


def _expression(text):
    if '=' not in text: return False, _poly(text)
    if text.count('=') != 1: raise ValueError()
    left, right = text.split('=')
    a, b = _poly(left), _poly(right)
    out = {key: a.get(key, Fraction(0)) - b.get(key, Fraction(0)) for key in a.keys() | b.keys()}
    out = {k: v for k, v in out.items() if v}
    # Equations differing by a nonzero constant factor have the same roots.
    if out:
        scale = out[sorted(out)[0]]
        out = {k: v / scale for k, v in out.items()}
    return True, out


def compare_exact(question, reply):
    """Return True/False when decidable, otherwise None for the model judge."""
    if question.get('type') != 'exact': return None
    # Algebraic equivalence alone cannot prove the requested intermediate step
    # was supplied (x=4 is not evidence of the requested 3x=12 step).
    if re.search(r'过程|步骤|那一步|移项|推导|怎么得到', question['stem']): return None
    accepted = question.get('accepted') or [question['answer']]
    kind = question.get('answer_kind')
    if any(normalize(reply) == normalize(a) for a in accepted): return True
    try:
        if kind == 'number':
            value = Fraction(normalize(reply))
            tolerance = Fraction(str(question.get('tolerance', 0)))
            return any(abs(value - Fraction(normalize(a))) <= tolerance for a in accepted)
        if kind == 'expression':
            value = _expression(reply)
            return any(value == _expression(a) for a in accepted)
        # Term accepted lists enumerate names. Unlisted names may be genuine
        # synonyms, and free text requires semantic judgement, not edit distance.
        return None
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return None
