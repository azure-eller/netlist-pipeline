"""Minimal s-expression reader for KiCad files (netlist, .kicad_pcb, .kicad_pro is JSON)."""

from __future__ import annotations

SExpr = str | list["SExpr"]


def parse(text: str) -> SExpr:
    tokens = _tokenize(text)
    pos = 0

    def read() -> SExpr:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            out: list[SExpr] = []
            while tokens[pos] != ")":
                out.append(read())
            pos += 1
            return out
        if tok == ")":
            raise ValueError("unexpected ')'")
        return tok

    return read()


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            tokens.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf: list[str] = []
            while text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            tokens.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def children(node: SExpr, tag: str) -> list[list[SExpr]]:
    """All child lists of `node` whose head is `tag`."""
    if isinstance(node, str):
        return []
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def child(node: SExpr, tag: str) -> list[SExpr] | None:
    found = children(node, tag)
    return found[0] if found else None


def value(node: SExpr, tag: str, default: str | None = None) -> str | None:
    """First scalar after `tag` in a child like (tag "x")."""
    c = child(node, tag)
    if c is None or len(c) < 2 or not isinstance(c[1], str):
        return default
    return c[1]
