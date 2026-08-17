"""Nyrqis NUI expression language (NUI-SCHEMA §7.2).

A small deterministic expression language designed specifically for NUI
(NFS-001 §7's ``$expr:`` values): ``state.name`` references, comparisons,
``&&``/``||``/``!`` boolean logic, and the function set
``if``/``min``/``max``/``contains``/``format`` — nothing more.

Why deterministic: the same expression must parse, validate, and evaluate
identically in NyForge (design time), this floor (the reference
implementation), and the Rust ``nyui`` crate (the shipped hot path,
ADR-0025). Both parsers are recursive descent with a shared token grammar
and byte-identical error messages (differential-tested).

Error contract: every error is a plain string starting with ``expr:``.
Syntax errors carry a byte offset (the character at which the parser
noticed the problem) so messages are position-stable across the two
implementations. Validation-time errors (unknown state/function/arity)
are reported by the caller, which knows the document's states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExprError(ValueError):
    """A deterministic expression error; ``str(exc)`` starts with ``expr:``."""


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||[(){}[\],.!<>=+\-*/%])
""", re.VERBOSE)

_STR_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "0": "\0",
}


@dataclass(frozen=True)
class Token:
    kind: str  # 'num' | 'str' | 'ident' | 'op' | 'eof'
    text: str
    pos: int  # byte offset into the source


def _tokenize(text: str) -> List[Token]:
    tokens: List[Token] = []
    for match in _TOKEN_RE.finditer(text):
        kind = match.lastgroup
        if kind == "ws":
            continue
        tokens.append(Token(kind=kind, text=match.group(), pos=match.start()))
    tokens.append(Token(kind="eof", text="", pos=len(text)))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    pass


@dataclass(frozen=True)
class Num(Node):
    value: float


@dataclass(frozen=True)
class Str(Node):
    value: str


@dataclass(frozen=True)
class Bool(Node):
    value: bool


@dataclass(frozen=True)
class StateRef(Node):
    name: str  # the dotted path after ``state.`` ('' means bare ``state``)


@dataclass(frozen=True)
class Func(Node):
    name: str
    args: Tuple[Node, ...]


@dataclass(frozen=True)
class Not(Node):
    operand: Node


@dataclass(frozen=True)
class Neg(Node):
    operand: Node


@dataclass(frozen=True)
class Bin(Node):
    op: str  # '==' '!=' '<' '<=' '>' '>=' '&&' '||' '+' '-' '*' '/' '%'
    left: Node
    right: Node


# ---------------------------------------------------------------------------
# Parser (recursive descent, single-token lookahead)
# ---------------------------------------------------------------------------

_COMPARE_OPS = ("==", "!=", "<", "<=", ">", ">=")
_ADD_OPS = ("+", "-")
_MUL_OPS = ("*", "/", "%")


def parse(text: str) -> Node:
    """Parse an expression. Raises ``ExprError`` on syntax errors."""
    tokens = _tokenize(text)
    parser = _Parser(tokens, text)
    node = parser.parse_or()
    if parser.peek().kind != "eof":
        tok = parser.peek()
        raise ExprError(
            f"expr: syntax error at {tok.pos}: unexpected token '{tok.text}'")
    return node


class _Parser:
    def __init__(self, tokens: List[Token], source: str) -> None:
        self.tokens = tokens
        self.source = source
        self.index = 0

    def peek(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        tok = self.tokens[self.index]
        self.index += 1
        return tok

    def _unexpected(self, tok: Token) -> ExprError:
        return ExprError(
            f"expr: syntax error at {tok.pos}: unexpected token '{tok.text}'")

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.peek().text == "||":
            self.advance()
            left = Bin("||", left, self.parse_and())
        return left

    def parse_and(self) -> Node:
        left = self.parse_compare()
        while self.peek().text == "&&":
            self.advance()
            left = Bin("&&", left, self.parse_compare())
        return left

    def parse_compare(self) -> Node:
        left = self.parse_add()
        while self.peek().text in _COMPARE_OPS:
            op = self.advance().text
            left = Bin(op, left, self.parse_add())
        return left

    def parse_add(self) -> Node:
        left = self.parse_mul()
        while self.peek().text in _ADD_OPS:
            op = self.advance().text
            left = Bin(op, left, self.parse_mul())
        return left

    def parse_mul(self) -> Node:
        left = self.parse_unary()
        while self.peek().text in _MUL_OPS:
            op = self.advance().text
            left = Bin(op, left, self.parse_unary())
        return left

    def parse_unary(self) -> Node:
        tok = self.peek()
        if tok.text == "!":
            self.advance()
            return Not(self.parse_unary())
        if tok.text == "-":
            self.advance()
            return Neg(self.parse_unary())
        if tok.text == "+":
            self.advance()
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.advance()
        if tok.kind == "num":
            return Num(float(tok.text))
        if tok.kind == "str":
            return Str(_unescape(tok.text))
        if tok.kind == "ident":
            if tok.text == "state":
                # ``state`` or ``state.a.b`` — a dotted continuation is
                # consumed into the StateRef name.
                nxt = self.peek()
                if nxt.text == ".":
                    self.advance()
                    name_tok = self.advance()
                    if name_tok.kind != "ident" or name_tok.text == "state":
                        raise self._unexpected(name_tok)
                    name = name_tok.text
                    while self.peek().text == ".":
                        self.advance()
                        seg = self.advance()
                        if seg.kind != "ident":
                            raise self._unexpected(seg)
                        name += "." + seg.text
                    return StateRef(name)
                if nxt.text == "(":
                    raise ExprError(
                        f"expr: syntax error at {nxt.pos}: 'state' is not a function")
                return StateRef("")
            if tok.text == "true":
                return Bool(True)
            if tok.text == "false":
                return Bool(False)
            if tok.text in ("if", "min", "max", "contains", "format"):
                return self._parse_call(tok)
            raise ExprError(f"expr: unknown function '{tok.text}'")
        if tok.text == "(":
            node = self.parse_or()
            closing = self.advance()
            if closing.text != ")":
                raise self._unexpected(closing)
            return node
        raise self._unexpected(tok)

    def _parse_call(self, name_tok: Token) -> Node:
        opening = self.advance()
        if opening.text != "(":
            raise self._unexpected(opening)
        args: List[Node] = []
        if self.peek().text == ")":
            self.advance()
            return Func(name_tok.text, tuple(args))
        while True:
            args.append(self.parse_or())
            sep = self.advance()
            if sep.text == ")":
                break
            if sep.text != ",":
                raise self._unexpected(sep)
        return Func(name_tok.text, tuple(args))


def _unescape(raw: str) -> str:
    """Decode a quoted string token (including the surrounding quotes)."""
    body = raw[1:-1]
    out: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            i += 1
            if i >= len(body):
                break
            esc = body[i]
            out.append(_STR_ESCAPES.get(esc, esc))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

#: name -> (min_args, max_args) — enforced identically by both parsers.
FUNCTIONS = {
    "if": (3, 3),
    "min": (2, None),
    "max": (2, None),
    "contains": (2, 2),
    "format": (2, None),
}


def state_refs(node: Node) -> List[str]:
    """Every ``state.<path>`` reference in the expression, in order."""
    out: List[str] = []

    def walk(n: Node) -> None:
        if isinstance(n, StateRef):
            out.append(n.name)
        elif isinstance(n, Func):
            for arg in n.args:
                walk(arg)
        elif isinstance(n, Not):
            walk(n.operand)
        elif isinstance(n, Neg):
            walk(n.operand)
        elif isinstance(n, Bin):
            walk(n.left)
            walk(n.right)

    walk(node)
    return out


def validate(node: Node, known_states: Optional[set] = None) -> Optional[str]:
    """Structural validation beyond syntax. Returns the first error
    message, or ``None`` if the expression is valid. ``known_states``
    is the set of document state names; ``None`` skips the state check.

    ``known_states`` may also be ``False`` to skip the check explicitly."""

    def err(msg: str) -> str:
        return f"expr: {msg}"

    for name in state_refs(node):
        if known_states is not None and name not in known_states:
            return err(f"unknown state 'state.{name}'")

    def walk(n: Node) -> Optional[str]:
        if isinstance(n, Func):
            signature = FUNCTIONS.get(n.name)
            if signature is None:
                return err(f"unknown function '{n.name}'")
            min_args, max_args = signature
            count = len(n.args)
            if count < min_args or (max_args is not None and count > max_args):
                if max_args is None:
                    expected = f"at least {min_args}"
                elif min_args == max_args:
                    expected = str(min_args)
                else:
                    expected = f"{min_args}-{max_args}"
                return err(
                    f"function '{n.name}' expects {expected} argument(s), "
                    f"got {count}")
            for arg in n.args:
                found = walk(arg)
                if found is not None:
                    return found
        elif isinstance(n, Not):
            return walk(n.operand)
        elif isinstance(n, Neg):
            return walk(n.operand)
        elif isinstance(n, Bin):
            found = walk(n.left)
            if found is not None:
                return found
            return walk(n.right)
        return None

    return walk(node)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return bool(value)


def _as_number(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise ExprError(f"expr: {what} must be a number, got boolean")
    if isinstance(value, (int, float)):
        return float(value)
    raise ExprError(f"expr: {what} must be a number, got '{value}'")


def _format_number(value: float, spec: str) -> str:
    if spec == "":
        if value == int(value):
            return str(int(value))
        return repr(value)
    try:
        return format(value, spec)
    except ValueError:
        raise ExprError(f"expr: format: invalid numeric spec '{{{spec}}}'")


def _format_value(value: Any, spec: str) -> str:
    if spec == "":
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    if isinstance(value, (int, float)):
        return _format_number(float(value), spec)
    raise ExprError(f"expr: format: non-numeric value with numeric spec '{{{spec}}}'")


def eval_expr(node: Node, states: dict) -> Any:
    """Evaluate a parsed expression against ``states`` (state name ->
    value). Deterministic and side-effect free."""

    def walk(n: Node) -> Any:
        if isinstance(n, Num):
            return n.value
        if isinstance(n, Str):
            return n.value
        if isinstance(n, Bool):
            return n.value
        if isinstance(n, StateRef):
            return states.get(n.name, "")
        if isinstance(n, Not):
            return not _truthy(walk(n.operand))
        if isinstance(n, Neg):
            return -_as_number(walk(n.operand), "operand of '-'")
        if isinstance(n, Bin):
            return _bin(n, walk)
        if isinstance(n, Func):
            return _call(n, walk)
        raise ExprError(f"expr: internal: unknown node {type(n).__name__}")

    return walk(node)


def _bin(n: Bin, walk) -> Any:
    op = n.op
    if op == "&&":
        return _truthy(walk(n.left)) and _truthy(walk(n.right))
    if op == "||":
        return _truthy(walk(n.left)) or _truthy(walk(n.right))
    if op in ("==", "!="):
        left = walk(n.left)
        right = walk(n.right)
        equal = _equals(left, right)
        return equal if op == "==" else not equal
    if op in ("<", "<=", ">", ">="):
        left = walk(n.left)
        right = walk(n.right)
        a = _compare_number(left, op, n)
        b = _compare_number(right, op, n)
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        return a >= b
    if op == "+":
        left = walk(n.left)
        right = walk(n.right)
        if isinstance(left, str) or isinstance(right, str):
            return str(left) + str(right)
        return _as_number(left, "operand of '+'") + _as_number(right, "operand of '+'")
    if op == "-":
        return _as_number(walk(n.left), "operand of '-'") - _as_number(
            walk(n.right), "operand of '-'")
    if op == "*":
        return _as_number(walk(n.left), "operand of '*'") * _as_number(
            walk(n.right), "operand of '*'")
    if op == "/":
        right = _as_number(walk(n.right), "operand of '/'")
        if right == 0:
            raise ExprError("expr: division by zero")
        return _as_number(walk(n.left), "operand of '/'") / right
    if op == "%":
        right = _as_number(walk(n.right), "operand of '%'")
        if right == 0:
            raise ExprError("expr: modulo by zero")
        return _as_number(walk(n.left), "operand of '%'") % right
    raise ExprError(f"expr: internal: unknown operator '{op}'")


def _compare_number(value: Any, op: str, n: Bin) -> float:
    if isinstance(value, bool):
        raise ExprError(f"expr: cannot compare boolean with '{op}'")
    if isinstance(value, (int, float)):
        return float(value)
    raise ExprError(f"expr: cannot compare '{value}' with '{op}'")


def _equals(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    return left == right


def _call(n: Func, walk) -> Any:
    name = n.name
    args = [walk(arg) for arg in n.args]
    if name == "if":
        condition, yes, no = args
        return yes if _truthy(condition) else no
    if name == "min":
        nums = [_as_number(v, f"argument of '{name}'") for v in args]
        return min(nums)
    if name == "max":
        nums = [_as_number(v, f"argument of '{name}'") for v in args]
        return max(nums)
    if name == "contains":
        haystack, needle = args
        if isinstance(haystack, str) and isinstance(needle, str):
            return needle in haystack
        if isinstance(haystack, (list, tuple)):
            return needle in haystack
        raise ExprError(
            "expr: contains: first argument must be a string or list")
    if name == "format":
        value = args[0]
        if not isinstance(args[1], str):
            raise ExprError("expr: format: format string must be a string")
        spec = _parse_format_spec(args[1])
        if len(args) == 2:
            return _format_value(value, spec)
        parts = [args[1]]
        for extra in args[2:]:
            parts.append(str(extra))
        return "".join(parts)
    raise ExprError(f"expr: unknown function '{name}'")


def _parse_format_spec(fmt: str) -> str:
    """Accept ``"{0}"`` or ``"{0:.2f}"`` and return the spec after the
    colon ('' for a plain index). Anything else is rejected — the
    language stays deterministic and small."""
    body = fmt.strip()
    if not (body.startswith("{") and body.endswith("}") and len(body) >= 3):
        raise ExprError("expr: format: format string must be like '{0}' or '{0:.2f}'")
    inner = body[1:-1]
    if inner == "" or not inner[0].isdigit():
        raise ExprError("expr: format: format string must be like '{0}' or '{0:.2f}'")
    if ":" in inner:
        index, spec = inner.split(":", 1)
        if not index.isdigit():
            raise ExprError("expr: format: format string must be like '{0}' or '{0:.2f}'")
        return spec
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ExprError", "FUNCTIONS", "Node", "Num", "Str", "Bool", "StateRef",
    "Func", "Not", "Neg", "Bin",
    "parse", "validate", "state_refs", "eval_expr",
]
