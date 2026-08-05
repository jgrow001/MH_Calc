"""
Parser for MistfallDB's embedded data format.

The site server-renders a big flat object table directly into the page as a
sequence of assignments like:

    $R[127]={slug:"ace-assassin-mask",slot:"Head",rarity:"Legendary"},
    $R[128]={slug:"ace-assassin-garb",slot:"Chest",...},
    $R[131]=[$R[127],$R[128],...]

Keys are unquoted JS identifiers, values are JS literals, and values can
reference earlier entries by index ($R[n]). This is not valid JSON (unquoted
keys, back-references), so we parse it with a small hand-rolled tokenizer +
recursive-descent parser instead of eval'ing it.

Usage:
    registry = parse_flight_registry(html_text)   # dict[int, Any], refs resolved
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<ref>\$R\[\d+\])
      | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<number>-?\d+\.\d+(?:[eE][+-]?\d+)?|-?\d+(?:[eE][+-]?\d+)?)
      | (?P<true>true\b)
      | (?P<false>false\b)
      | (?P<null>null\b|undefined\b)
      | (?P<ident>[A-Za-z_$][A-Za-z0-9_$]*)
      | (?P<punct>[{}\[\]:,])
    )
    """,
    re.VERBOSE,
)

_ASSIGN_RE = re.compile(r"\$R\[(\d+)\]=")


@dataclass(frozen=True)
class Ref:
    idx: int


class _ParseError(Exception):
    pass


def _tokenize_from(text: str, pos: int):
    """Yield (kind, value, start, end) tokens starting at pos, stopping at
    the first position that doesn't match (caller decides what that means)."""
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            return
        kind = m.lastgroup
        yield kind, m.group(kind), m.start(kind), m.end()
        pos = m.end()


class _Parser:
    """Parses one JS value at a time from a token stream, tracking position
    in the underlying string so the caller can resume scanning right after
    the value (needed because values are comma-separated at the top level,
    same as `a=1,b=2` in JS)."""

    def __init__(self, text: str, pos: int):
        self.text = text
        self.pos = pos

    def _peek(self):
        m = _TOKEN_RE.match(self.text, self.pos)
        if not m or m.end() == self.pos:
            return None
        return m.lastgroup, m.group(m.lastgroup), m.start(m.lastgroup), m.end()

    def _next(self):
        tok = self._peek()
        if tok is None:
            raise _ParseError(f"unexpected end of input at {self.pos}")
        self.pos = tok[3]
        return tok

    def parse_value(self) -> Any:
        kind, val, start, end = self._next()
        if kind == "punct" and val == "{":
            return self._parse_object()
        if kind == "punct" and val == "[":
            return self._parse_array()
        if kind == "string":
            return _unescape_js_string(val)
        if kind == "number":
            return float(val) if ("." in val or "e" in val or "E" in val) else int(val)
        if kind == "true":
            return True
        if kind == "false":
            return False
        if kind == "null":
            return None
        if kind == "ref":
            return Ref(int(val[3:-1]))
        if kind == "ident":
            # Bare identifier used as a value (rare) - keep as string.
            return val
        raise _ParseError(f"unexpected token {kind!r}={val!r} at {start}")

    def _parse_object(self) -> dict:
        obj: dict[str, Any] = {}
        tok = self._peek()
        if tok and tok[0] == "punct" and tok[1] == "}":
            self._next()
            return obj
        while True:
            kind, val, _, _ = self._next()
            if kind == "string":
                key = _unescape_js_string(val)
            elif kind == "ident":
                key = val
            elif kind == "number":
                key = val
            else:
                raise _ParseError(f"expected object key, got {kind!r}={val!r}")
            kind, val, _, _ = self._next()
            if not (kind == "punct" and val == ":"):
                raise _ParseError(f"expected ':' after key {key!r}")
            obj[key] = self.parse_value()
            kind, val, _, _ = self._next()
            if kind == "punct" and val == "}":
                return obj
            if not (kind == "punct" and val == ","):
                raise _ParseError(f"expected ',' or '}}' in object, got {val!r}")
            tok = self._peek()
            if tok and tok[0] == "punct" and tok[1] == "}":
                self._next()
                return obj

    def _parse_array(self) -> list:
        arr: list[Any] = []
        tok = self._peek()
        if tok and tok[0] == "punct" and tok[1] == "]":
            self._next()
            return arr
        while True:
            arr.append(self.parse_value())
            kind, val, _, _ = self._next()
            if kind == "punct" and val == "]":
                return arr
            if not (kind == "punct" and val == ","):
                raise _ParseError(f"expected ',' or ']' in array, got {val!r}")
            tok = self._peek()
            if tok and tok[0] == "punct" and tok[1] == "]":
                self._next()
                return arr


def _unescape_js_string(raw: str) -> str:
    body = raw[1:-1]
    return (
        body.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\\"", "\"")
        .replace("\\'", "'")
        .replace("\\\\", "\\")
    )


def _resolve(value: Any, registry: dict[int, Any], seen: set[int] | None = None) -> Any:
    if isinstance(value, Ref):
        if seen is None:
            seen = set()
        if value.idx in seen:
            return None  # cycle guard, shouldn't happen in practice
        if value.idx not in registry:
            return None
        return _resolve(registry[value.idx], registry, seen | {value.idx})
    if isinstance(value, dict):
        return {k: _resolve(v, registry, seen) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, registry, seen) for v in value]
    return value


def parse_flight_registry(html_text: str) -> dict[int, Any]:
    """Extract every `$R[n]=<value>` assignment from a MistfallDB page and
    return {n: fully-resolved value} with all $R[m] back-references inlined.
    """
    raw: dict[int, Any] = {}
    for m in _ASSIGN_RE.finditer(html_text):
        idx = int(m.group(1))
        parser = _Parser(html_text, m.end())
        try:
            raw[idx] = parser.parse_value()
        except _ParseError:
            continue
    return {idx: _resolve(val, raw) for idx, val in raw.items()}


def iter_dicts(value: Any):
    """Recursively yield every dict found anywhere in a resolved structure."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from iter_dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from iter_dicts(v)
