"""CHANGELOG.md → static/index.html 「사용법」 페이지의 개발 · 수정 이력 블록.

irodmas.com 은 static/ 폴더만 복사해 서비스하므로, 화면에서 보이도록 HTML 로 바꿔 index.html 안에 넣는다.
CHANGELOG.md 를 고친 뒤 실행:  python tools/sync_changelog.py
(--check: 동기화 여부만 확인, 다르면 종료 코드 1)
"""
from __future__ import annotations

import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "CHANGELOG.md")
PAGE = os.path.join(ROOT, "static", "index.html")
START, END = "<!--CHANGELOG:START-->", "<!--CHANGELOG:END-->"


def inline(s: str) -> str:
    """굵게 · 코드 · 링크 (코드 안은 변환하지 않음)."""
    parts = re.split(r"(`[^`]+`)", s)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{html.escape(p[1:-1])}</code>")
            continue
        p = html.escape(p, quote=False)
        p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        p = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                   lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>'
                   if m.group(2).startswith("http") else m.group(1), p)
        p = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1" target="_blank" rel="noopener">\1</a>', p)
        out.append(p)
    return "".join(out)


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i, in_details, first_section = 0, False, True
    para: list[str] = []

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            flush_para(); i += 1; continue
        if line.startswith("# "):            # 문서 제목은 페이지 h2 로 대신함
            flush_para(); i += 1; continue
        if line.strip() == "---":
            flush_para(); i += 1; continue
        if line.startswith("## "):           # 버전 단위 → 접기 (최신만 펼침)
            flush_para()
            if in_details:
                out.append("</details>")
            out.append(f'<details class="ver"{" open" if first_section else ""}><summary>{inline(line[3:])}</summary>')
            in_details, first_section = True, False
            i += 1; continue
        if line.startswith("### "):
            flush_para(); out.append(f"<h4>{inline(line[4:])}</h4>"); i += 1; continue
        if line.startswith("|"):             # 표
            flush_para()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append('<table class="plain"><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</table>")
            continue
        if re.match(r"^\s*- ", line):         # 목록 (들여쓴 줄은 하위 목록)
            flush_para()
            out.append("<ul>")
            depth = 0
            while i < len(lines) and re.match(r"^\s*- ", lines[i]):
                ind = len(lines[i]) - len(lines[i].lstrip())
                d = 1 if ind >= 2 else 0
                while depth < d:
                    out.append("<ul>"); depth += 1
                while depth > d:
                    out.append("</ul>"); depth -= 1
                out.append(f"<li>{inline(lines[i].strip()[2:])}</li>")
                i += 1
            while depth > 0:
                out.append("</ul>"); depth -= 1
            out.append("</ul>")
            continue
        para.append(line.strip())
        i += 1
    flush_para()
    if in_details:
        out.append("</details>")
    return "\n".join(out)


def block() -> str:
    with open(MD, encoding="utf-8") as f:
        body = convert(f.read())
    return f"{START}\n{body}\n{END}"


def main() -> int:
    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    if START not in page or END not in page:
        print("index.html 에 CHANGELOG 표시(START/END)가 없습니다", file=sys.stderr)
        return 2
    new = page[:page.index(START)] + block() + page[page.index(END) + len(END):]
    if "--check" in sys.argv:
        if new != page:
            print("CHANGELOG.md 와 사용법 페이지가 다릅니다 — python tools/sync_changelog.py 를 실행하세요")
            return 1
        print("동기화됨")
        return 0
    with open(PAGE, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print("사용법 페이지에 개발 · 수정 이력을 반영했습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
