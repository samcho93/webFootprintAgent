"""KiCad S-expression 최소 직렬화/파싱 도우미."""
from __future__ import annotations

import re
import uuid


class Sym(str):
    """따옴표 없이 출력되는 심볼 토큰 (예: yes, smd, input)."""


def q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def num(v: float) -> str:
    v = round(float(v), 4)
    if v == 0:
        v = 0.0  # -0 제거
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def uid() -> list:
    return ["uuid", str(uuid.uuid4())]


def dumps(node, indent: int = 0) -> str:
    """중첩 리스트 → S-expression 문자열.

    리스트 첫 요소는 키워드. 문자열은 따옴표 처리, Sym 은 그대로, 숫자는 num().
    자식 리스트가 있으면 여러 줄로, 없으면 한 줄로 출력한다.
    """
    if not isinstance(node, list):
        if isinstance(node, Sym):
            return str(node)
        if isinstance(node, bool):
            return "yes" if node else "no"
        if isinstance(node, (int, float)):
            return num(node)
        return q(node)
    head, *rest = node
    tab = "\t" * indent
    simple = [r for r in rest if not isinstance(r, list)]
    lists = [r for r in rest if isinstance(r, list)]
    if not lists:
        return f"{tab}({head}" + "".join(" " + dumps(r) for r in rest) + ")"
    # 짧은 리스트만 가진 노드는 한 줄로 (at/size/font 등)
    inline_ok = all(not any(isinstance(x, list) for x in l) for l in lists) and len(lists) <= 2 and head in (
        "effects", "stroke", "start", "end", "pts", "font",
    )
    if inline_ok:
        return f"{tab}({head}" + "".join(" " + (dumps(r) if not isinstance(r, list) else dumps(r).strip()) for r in rest) + ")"
    out = f"{tab}({head}" + "".join(" " + dumps(r) for r in simple)
    for r in lists:
        out += "\n" + dumps(r, indent + 1)
    return out + "\n" + tab + ")"


_TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+)')


def loads(text: str):
    """간단한 S-expression 파서 (라이브러리 병합용). 문자열은 str, 나머지 토큰은 Sym."""
    pos, stack, cur = 0, [], []
    root = cur
    while True:
        m = _TOKEN.match(text, pos)
        if not m:
            break
        tok = m.group(1)
        pos = m.end()
        if tok == "(":
            stack.append(cur)
            new: list = []
            cur.append(new)
            cur = new
        elif tok == ")":
            cur = stack.pop()
        elif tok.startswith('"'):
            cur.append(tok[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\"))
        else:
            cur.append(Sym(tok))
    return root[0] if root else None
