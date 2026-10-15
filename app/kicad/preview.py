"""검토용 SVG 미리보기 (핀/패드 번호가 보이도록 자체 렌더링)."""
from __future__ import annotations

import math
from html import escape

from ..models import PartSpec
from .footprint import Footprint
from .symbol import FONT, PIN_LEN, layout

COLORS = {
    "body": "#840000", "fill": "#ffffc2", "pin": "#840000", "name": "#006464", "num": "#a90000", "field": "#006464",
}
TYPE_MARK = {"power_in": "⏚", "power_out": "⏻", "no_connect": "×"}


def _t(x, y, s, size, color, anchor="start", rot=0, baseline="middle", weight="normal"):
    tr = f' transform="rotate({rot} {x:.3f} {y:.3f})"' if rot else ""
    return (f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size:.3f}" fill="{color}" text-anchor="{anchor}" '
            f'dominant-baseline="{baseline}" font-family="Consolas, monospace" font-weight="{weight}"{tr}>{escape(s)}</text>')


def _name(s: str) -> str:
    return s.replace("~{", "").replace("}", "")  # 오버바 표기 단순화 (미리보기 전용)


def symbol_svg(spec: PartSpec) -> str:
    placed, (x1, y1, x2, y2), sides = layout(spec)
    # 심볼 좌표(Y 위) → SVG(Y 아래)
    pts = [(p.x, -p.y) for p in placed] + [(x1, -y1), (x2, -y2)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = 4
    vx, vy = min(xs) - pad - 2, min(ys) - pad - 2
    vw, vh = max(xs) - min(xs) + 2 * pad + 4, max(ys) - min(ys) + 2 * pad + 4
    f = FONT
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}">',
           f'<rect x="{x1}" y="{-y1}" width="{x2 - x1}" height="{y1 - y2}" fill="{COLORS["fill"]}" '
           f'stroke="{COLORS["body"]}" stroke-width="0.254"/>']
    for pp in placed:
        x, y = pp.x, -pp.y
        dx, dy = {0: (1, 0), 180: (-1, 0), 270: (0, 1), 90: (0, -1)}[pp.angle]
        ex, ey = x + dx * PIN_LEN, y + dy * PIN_LEN
        out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{ex:.3f}" y2="{ey:.3f}" stroke="{COLORS["pin"]}" stroke-width="0.152"/>')
        if pp.pin.electrical_type == "no_connect":
            out.append(_t(x, y, "×", f * 1.2, COLORS["pin"], "middle"))
        else:
            out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="0.25" fill="none" stroke="{COLORS["pin"]}" stroke-width="0.1"/>')
        nm = _name(pp.pin.name)
        if dx:  # 좌/우
            out.append(_t(ex + dx * 0.5, ey, nm, f, COLORS["name"], "start" if dx > 0 else "end"))
            out.append(_t((x + ex) / 2, y - 0.35, pp.pin.number, f * 0.85, COLORS["num"], "middle", baseline="auto"))
        else:   # 상/하 (세로 텍스트)
            # 아래(-90°)로 읽히는 KiCad 방식: 위쪽 핀은 끝 정렬, 아래쪽 핀은 시작 정렬
            out.append(_t(ex, ey + dy * 0.5, nm, f, COLORS["name"], "end" if dy > 0 else "start", rot=-90))
            out.append(_t(x - 0.35, (y + ey) / 2, pp.pin.number, f * 0.85, COLORS["num"], "middle", rot=-90, baseline="auto"))
    top_extra = PIN_LEN if sides["top"] else 0
    bot_extra = PIN_LEN if sides["bottom"] else 0
    out.append(_t(x1, -(y1 + top_extra) - 1.0, spec.reference_prefix + "?", f, COLORS["field"], "start", baseline="auto"))
    out.append(_t(x1, -(y2 - bot_extra) + 1.8, spec.part_number, f, COLORS["field"], "start", baseline="auto"))
    out.append("</svg>")
    return "".join(out)


def footprint_svg(fp: Footprint) -> str:
    c = fp.courtyard
    m = 1.2
    vx, vy, vw, vh = c[0] - m, c[1] - m, c[2] - c[0] + 2 * m, c[3] - c[1] + 2 * m
    s = max(vw, vh) / 60  # 기준 선폭
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.3f} {vy:.3f} {vw:.3f} {vh:.3f}">',
           f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#001023"/>']
    # 1 mm 격자
    for gx in range(math.floor(vx), math.ceil(vx + vw) + 1):
        out.append(f'<line x1="{gx}" y1="{vy}" x2="{gx}" y2="{vy + vh}" stroke="#16304d" stroke-width="{s * 0.15:.4f}"/>')
    for gy in range(math.floor(vy), math.ceil(vy + vh) + 1):
        out.append(f'<line x1="{vx}" y1="{gy}" x2="{vx + vw}" y2="{gy}" stroke="#16304d" stroke-width="{s * 0.15:.4f}"/>')
    b = fp.body
    out.append(f'<rect x="{b[0]}" y="{b[1]}" width="{b[2] - b[0]}" height="{b[3] - b[1]}" fill="none" stroke="#a0a0a0" '
               f'stroke-width="0.1" stroke-dasharray="0.3 0.2"/>')
    out.append(f'<rect x="{c[0]}" y="{c[1]}" width="{c[2] - c[0]}" height="{c[3] - c[1]}" fill="none" stroke="#ff26e2" stroke-width="0.05"/>')
    for p in fp.pads:
        rot = f' transform="rotate({p.rot} {p.x} {p.y})"' if p.rot else ""
        x, y = p.x - p.w / 2, p.y - p.h / 2
        copper = p.is_copper
        color = "#c83434" if p.kind == "smd" else "#c2a032"
        if not copper:  # 페이스트 전용 창
            out.append(f'<rect x="{x:.4f}" y="{y:.4f}" width="{p.w}" height="{p.h}" fill="none" stroke="#e8d45a" '
                       f'stroke-width="0.04"{rot}/>')
            continue
        if p.shape in ("circle",):
            out.append(f'<circle cx="{p.x}" cy="{p.y}" r="{p.w / 2}" fill="{color}" opacity="0.9"/>')
        else:
            rx = min(p.w, p.h) * (0.25 if p.shape == "roundrect" else 0.5 if p.shape == "oval" else 0)
            out.append(f'<rect x="{x:.4f}" y="{y:.4f}" width="{p.w}" height="{p.h}" rx="{rx:.4f}" fill="{color}" opacity="0.9"{rot}/>')
        if p.drill:
            out.append(f'<circle cx="{p.x}" cy="{p.y}" r="{p.drill / 2}" fill="#001023"/>')
        if p.number:
            bb = p.bbox
            short, long_ = sorted((bb[2] - bb[0], bb[3] - bb[1]))
            fs = min(short * 0.85, long_ * 1.2 / max(len(p.number), 1), 1.2)
            vertical = (bb[3] - bb[1]) > (bb[2] - bb[0]) * 1.6
            out.append(_t(p.x, p.y, p.number, fs, "#ffffff", "middle", rot=-90 if vertical else 0,
                          baseline="central", weight="bold"))
    for kind, g in fp.silk:
        if kind == "line":
            (a, bb) = g
            out.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{bb[0]}" y2="{bb[1]}" stroke="#f2eda1" stroke-width="0.12" stroke-linecap="round"/>')
        else:
            out.append('<polygon points="' + " ".join(f"{x:.3f},{y:.3f}" for x, y in g) + '" fill="#f2eda1"/>')
    # 원점 표시 + 치수
    out.append(f'<path d="M -0.4 0 H 0.4 M 0 -0.4 V 0.4" stroke="#ffffff" stroke-width="0.04"/>')
    out.append(_t(c[0], c[1] - 0.3, f"{c[2] - c[0]:.2f} × {c[3] - c[1]:.2f} mm (courtyard)", min(0.9, vw / 30), "#ff26e2",
                  baseline="auto"))
    out.append("</svg>")
    return "".join(out)
