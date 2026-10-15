"""PackageSpec → KiCad 9 풋프린트 (.kicad_mod) 생성기.

랜드 패턴은 데이터시트 권장 패턴(explicit_pads)이 있으면 그대로 쓰고, 없으면
IPC-7351B Nominal(밀도 B) 근사식으로 계산한다. 좌표계: KiCad 풋프린트(Y 아래쪽 +), 원점 = 패키지 중심.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models import PackageSpec, PartSpec
from .sexpr import Sym, dumps, num, uid


@dataclass
class Pad:
    number: str
    x: float
    y: float
    w: float
    h: float
    shape: str = "roundrect"           # rect / roundrect / circle / oval
    kind: str = "smd"                  # smd / thru_hole / np_thru_hole
    drill: float | None = None
    rot: float = 0.0
    layers: list[str] = field(default_factory=lambda: ["F.Cu", "F.Paste", "F.Mask"])

    @property
    def bbox(self):
        w, h = (self.h, self.w) if round(self.rot) % 180 == 90 else (self.w, self.h)
        return (self.x - w / 2, self.y - h / 2, self.x + w / 2, self.y + h / 2)

    @property
    def is_copper(self) -> bool:
        return any("Cu" in l for l in self.layers)


@dataclass
class Footprint:
    name: str
    pads: list[Pad]
    body: tuple[float, float, float, float]       # x1, y1, x2, y2
    courtyard: tuple[float, float, float, float]
    notes: list[str] = field(default_factory=list)
    text: str = ""
    silk: list = field(default_factory=list)      # 미리보기용 실크 선분/삼각형


def r2(v: float, g: float = 0.01) -> float:
    return round(round(v / g) * g, 4)


def _mm(lo, hi, what: str, notes: list[str], default=None):
    """min/max 쌍 정리. 하나만 있으면 둘 다 그 값으로."""
    if lo is None and hi is None:
        if default is None:
            raise ValueError(f"패키지 치수 누락: {what}")
        notes.append(f"{what} 값이 없어 기본값 {default} mm 사용")
        return default, default
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    return min(lo, hi), max(lo, hi)


# ---------------------------------------------------------------- IPC 계산
def ipc_row(span_min, span_max, l_min, l_max, b_max, pitch, jt, jh, js, min_gap=0.2):
    """한 줄(양쪽 대칭) 랜드 계산 → (패드 중심 거리 C, 패드 길이, 패드 폭)."""
    z = span_max + 2 * jt
    s_min = span_min - 2 * l_max
    g = max(s_min - 2 * jh, 0.0)
    pad_len = (z - g) / 2
    c = (z + g) / 2
    x = b_max + 2 * js
    if pitch:
        x = min(x, pitch - min_gap)
    return r2(c), r2(pad_len), r2(x)


def _gap_for(pitch):
    return 0.2 if (pitch or 1) >= 0.65 else 0.15


def gen_dual(pkg: PackageSpec, notes, nolead: bool):
    n = pkg.pin_count
    if n % 2:
        raise ValueError("dual 패키지는 핀 수가 짝수여야 함 (홀수면 explicit 사용)")
    pitch = pkg.pitch or 1.27
    b_lo, b_hi = _mm(pkg.lead_width_min, pkg.lead_width_max, "리드 폭 b", notes, default=pitch * 0.5)
    l_lo, l_hi = _mm(pkg.lead_length_min, pkg.lead_length_max, "리드 길이 L", notes, default=0.6 if not nolead else 0.4)
    if nolead:
        e_lo, e_hi = _mm(pkg.lead_span_min, pkg.lead_span_max, "몸체 폭 E", notes,
                         default=pkg.body_width)
        c, plen, pw = ipc_row(e_lo, e_hi, l_lo, l_hi, b_hi, pitch, jt=0.30, jh=0.0, js=0.0, min_gap=_gap_for(pitch))
    else:
        e_lo, e_hi = _mm(pkg.lead_span_min, pkg.lead_span_max, "리드 스팬 E", notes)
        js = 0.03 if pitch > 0.625 else -0.02
        c, plen, pw = ipc_row(e_lo, e_hi, l_lo, l_hi, b_hi, pitch, jt=0.35, jh=0.35, js=js, min_gap=_gap_for(pitch))
    half = n // 2
    pads = []
    y0 = -(half - 1) * pitch / 2
    for i in range(half):
        pads.append(Pad(str(i + 1), -c / 2, r2(y0 + i * pitch, 0.0001), plen, pw))
    for i in range(half):
        pads.append(Pad(str(half + i + 1), c / 2, r2(-y0 - i * pitch, 0.0001), plen, pw))
    return pads


def gen_quad(pkg: PackageSpec, notes, nolead: bool):
    n = pkg.pin_count
    px = pkg.pins_x or (n // 4)
    py = pkg.pins_y or (n // 4)
    if 2 * (px + py) != n:
        raise ValueError(f"쿼드 핀 수 불일치: 2*({px}+{py}) != {n}")
    pitch = pkg.pitch or 0.5
    b_lo, b_hi = _mm(pkg.lead_width_min, pkg.lead_width_max, "리드 폭 b", notes, default=pitch * 0.5)
    l_lo, l_hi = _mm(pkg.lead_length_min, pkg.lead_length_max, "리드 길이 L", notes, default=0.4 if nolead else 0.6)
    bw = pkg.body_width or 0
    bl = pkg.body_length or bw
    if nolead:
        ex = _mm(pkg.lead_span_min, pkg.lead_span_max, "몸체 폭 E", notes, default=bw)
        ey = (ex[0] + (bl - bw), ex[1] + (bl - bw))
        jt, jh, js = 0.30, 0.0, 0.0
    else:
        ex = _mm(pkg.lead_span_min, pkg.lead_span_max, "리드 스팬 E", notes)
        ey = (ex[0] + (bl - bw), ex[1] + (bl - bw))
        jt, jh, js = 0.35, 0.35, (0.03 if pitch > 0.625 else -0.02)
    gap = _gap_for(pitch)
    cx, plx, pwx = ipc_row(*ex, l_lo, l_hi, b_hi, pitch, jt, jh, js, gap)   # 좌/우 열
    cy, ply, pwy = ipc_row(*ey, l_lo, l_hi, b_hi, pitch, jt, jh, js, gap)   # 상/하 열
    # 코너 충돌 방지: 측면 패드 안쪽 끝이 직교 열의 마지막 패드와 gap 이상 떨어지도록 안쪽을 자름
    y0 = -(py - 1) * pitch / 2
    x0 = -(px - 1) * pitch / 2

    def trim(c, plen, limit):
        outer = c / 2 + plen / 2
        inner = c / 2 - plen / 2
        if inner < limit - 1e-6:
            plen = r2(outer - limit)
            c = r2(2 * (outer - plen / 2))
            notes.append(f"코너 간섭 방지: 패드 안쪽 끝을 {limit:.3f} mm 로 조정 (길이 {plen} mm)")
        return c, plen

    cx, plx = trim(cx, plx, abs(x0) + pwy / 2 + gap)
    cy, ply = trim(cy, ply, abs(y0) + pwx / 2 + gap)
    pads, k = [], 1
    for i in range(py):            # 왼쪽: 위→아래
        pads.append(Pad(str(k), -cx / 2, r2(y0 + i * pitch, 1e-4), plx, pwx)); k += 1
    for i in range(px):            # 아래: 왼→오
        pads.append(Pad(str(k), r2(x0 + i * pitch, 1e-4), cy / 2, ply, pwy, rot=90)); k += 1
    for i in range(py):            # 오른쪽: 아래→위
        pads.append(Pad(str(k), cx / 2, r2(-y0 - i * pitch, 1e-4), plx, pwx)); k += 1
    for i in range(px):            # 위: 오→왼
        pads.append(Pad(str(k), r2(-x0 - i * pitch, 1e-4), -cy / 2, ply, pwy, rot=90)); k += 1
    return pads


def gen_exposed_pad(pkg: PackageSpec, signal_pads: list[Pad], notes) -> list[Pad]:
    ep = pkg.exposed_pad
    if not ep:
        return []
    w, h = ep.width, ep.length
    # 신호 패드 안쪽 끝과 EP 사이 최소 0.2 mm 확보
    inner = min((min(abs(p.bbox[0]), abs(p.bbox[2])) if abs(p.x) > abs(p.y) else 1e9) for p in signal_pads)
    inner_y = min((min(abs(p.bbox[1]), abs(p.bbox[3])) if abs(p.y) >= abs(p.x) else 1e9) for p in signal_pads)
    if inner < 1e8 and w / 2 > inner - 0.2 + 1e-6:
        notes.append(f"EP 폭 {w}→{r2(2 * (inner - 0.2))} mm 축소 (신호 패드와 0.2 mm 간격 확보)")
        w = r2(2 * (inner - 0.2))
    if inner_y < 1e8 and h / 2 > inner_y - 0.2 + 1e-6:
        notes.append(f"EP 길이 {h}→{r2(2 * (inner_y - 0.2))} mm 축소 (신호 패드와 0.2 mm 간격 확보)")
        h = r2(2 * (inner_y - 0.2))
    number = ep.number or str(pkg.pin_count + 1)
    pads = [Pad(number, 0, 0, w, h, shape="rect", layers=["F.Cu", "F.Mask"])]
    # 솔더 페이스트: 격자 창으로 분할, 커버리지 ~55%
    nx, ny = max(1, math.ceil(w / 1.6)), max(1, math.ceil(h / 1.6))
    cw, ch = w / nx, h / ny
    for ix in range(nx):
        for iy in range(ny):
            pads.append(Pad("", r2(-w / 2 + cw * (ix + 0.5), 1e-4), r2(-h / 2 + ch * (iy + 0.5), 1e-4),
                            r2(cw * 0.74), r2(ch * 0.74), shape="roundrect", layers=["F.Paste"]))
    notes.append(f"EP 페이스트 {nx}x{ny} 분할 (약 55% 커버리지)")
    return pads


def gen_chip(pkg: PackageSpec, notes):
    l_lo, l_hi = _mm(pkg.lead_span_min, pkg.lead_span_max, "전체 길이 L", notes, default=pkg.body_length)
    t_lo, t_hi = _mm(pkg.lead_length_min, pkg.lead_length_max, "단자 길이 T", notes, default=l_hi * 0.25)
    w_lo, w_hi = _mm(pkg.lead_width_min, pkg.lead_width_max, "단자 폭 W", notes, default=pkg.body_width)
    jt = 0.20 if l_hi < 1.3 else 0.35
    c, plen, pw = ipc_row(l_lo, l_hi, t_lo, t_hi, w_hi, None, jt=jt, jh=0.0, js=0.0)
    if pkg.pin_count != 2:
        raise ValueError("chip_2terminal 은 pin_count=2 여야 함")
    return [Pad("1", -c / 2, 0, plen, pw), Pad("2", c / 2, 0, plen, pw)]


def _tht_dims(pkg: PackageSpec, notes):
    lead = pkg.lead_diameter or pkg.lead_width_max
    if lead is None:
        notes.append("리드 지름 정보 없음 → 0.6 mm 가정")
        lead = 0.6
    drill = math.ceil((lead + 0.25) / 0.05) * 0.05
    pad = max(round(drill + 0.7, 2), 1.6 if (pkg.pitch or 2.54) >= 2.54 else drill + 0.5)
    return r2(drill), r2(pad)


THT_LAYERS = ["*.Cu", "*.Mask"]


def gen_dip(pkg: PackageSpec, notes):
    n = pkg.pin_count
    pitch = pkg.pitch or 2.54
    rs = pkg.row_spacing or 7.62
    drill, pad = _tht_dims(pkg, notes)
    half = n // 2
    y0 = -(half - 1) * pitch / 2
    pads = []
    for i in range(half):
        pads.append(Pad(str(i + 1), -rs / 2, r2(y0 + i * pitch, 1e-4), pad, pad, "rect" if i == 0 else "oval",
                        "thru_hole", drill, layers=THT_LAYERS))
    for i in range(half):
        pads.append(Pad(str(half + i + 1), rs / 2, r2(-y0 - i * pitch, 1e-4), pad, pad, "oval",
                        "thru_hole", drill, layers=THT_LAYERS))
    return pads


def gen_header(pkg: PackageSpec, notes):
    rows = pkg.rows or 1
    pitch = pkg.pitch or 2.54
    per = pkg.pin_count // rows
    drill, pad = _tht_dims(pkg, notes)
    pads = []
    for k in range(pkg.pin_count):
        r, i = (k % rows, k // rows) if rows > 1 else (0, k)
        x = (r - (rows - 1) / 2) * pitch
        y = (i - (per - 1) / 2) * pitch
        pads.append(Pad(str(k + 1), r2(x, 1e-4), r2(y, 1e-4), pad, pad, "rect" if k == 0 else "oval",
                        "thru_hole", drill, layers=THT_LAYERS))
    return pads


BGA_ROWS = [c for c in "ABCDEFGHJKLMNPRTUVWY"]


def bga_row_name(i: int) -> str:
    if i < len(BGA_ROWS):
        return BGA_ROWS[i]
    return BGA_ROWS[i // len(BGA_ROWS) - 1] + BGA_ROWS[i % len(BGA_ROWS)]


def gen_bga(pkg: PackageSpec, notes):
    rows, cols, pitch = pkg.rows, pkg.cols, pkg.pitch
    if not (rows and cols and pitch):
        raise ValueError("BGA 는 rows, cols, pitch 필수")
    ball = pkg.ball_diameter or pitch * 0.5
    d = r2(ball * 0.8)  # NSMD: 볼 지름의 약 80%
    notes.append(f"BGA 패드 지름 {d} mm (볼 {ball} mm × 0.8, NSMD)")
    missing = {m.upper() for m in pkg.missing_balls}
    pads = []
    for r in range(rows):
        for c in range(cols):
            name = f"{bga_row_name(r)}{c + 1}"
            if name in missing:
                continue
            pads.append(Pad(name, r2((c - (cols - 1) / 2) * pitch, 1e-4), r2((r - (rows - 1) / 2) * pitch, 1e-4),
                            d, d, "circle"))
    return pads


def gen_explicit(pkg: PackageSpec, notes):
    if not pkg.explicit_pads:
        raise ValueError("explicit 패키지인데 explicit_pads 가 비어 있음")
    pads = []
    for p in pkg.explicit_pads:
        if p.pad_type == "smd":
            layers = ["F.Cu", "F.Paste", "F.Mask"]
        else:
            layers = THT_LAYERS
        pads.append(Pad(p.number, p.x, p.y, p.width, p.height, p.shape, p.pad_type, p.drill, p.rotation, layers))
    return pads


def generate_pads(pkg: PackageSpec, notes: list[str]) -> list[Pad]:
    if pkg.explicit_pads and pkg.family != "bga":
        pads = gen_explicit(pkg, notes)
        notes.append("데이터시트/직접 지정 패드 좌표 사용")
        return pads
    f = pkg.family
    if f == "gullwing_dual":
        pads = gen_dual(pkg, notes, nolead=False)
    elif f == "nolead_dual":
        pads = gen_dual(pkg, notes, nolead=True)
    elif f == "gullwing_quad":
        pads = gen_quad(pkg, notes, nolead=False)
    elif f == "nolead_quad":
        pads = gen_quad(pkg, notes, nolead=True)
    elif f == "chip_2terminal":
        pads = gen_chip(pkg, notes)
    elif f == "dip":
        pads = gen_dip(pkg, notes)
    elif f == "pin_header":
        pads = gen_header(pkg, notes)
    elif f == "bga":
        pads = gen_bga(pkg, notes)
    else:
        pads = gen_explicit(pkg, notes)
    if f in ("nolead_dual", "nolead_quad", "gullwing_quad", "gullwing_dual"):
        pads += gen_exposed_pad(pkg, pads, notes)
    notes.append("IPC-7351B Nominal 근사식으로 랜드 패턴 계산")
    return pads


# ---------------------------------------------------------------- 그래픽
def bbox_union(boxes):
    xs1, ys1, xs2, ys2 = zip(*boxes)
    return min(xs1), min(ys1), max(xs2), max(ys2)


def clip_segment(p1, p2, obstacles, clearance):
    """수평/수직 선분에서 장애물(확장된 패드 bbox)과 겹치는 구간을 제거."""
    (x1, y1), (x2, y2) = p1, p2
    horizontal = abs(y1 - y2) < 1e-9
    a, b = (min(x1, x2), max(x1, x2)) if horizontal else (min(y1, y2), max(y1, y2))
    fixed = y1 if horizontal else x1
    cuts = []
    for (ox1, oy1, ox2, oy2) in obstacles:
        ox1, oy1, ox2, oy2 = ox1 - clearance, oy1 - clearance, ox2 + clearance, oy2 + clearance
        if horizontal and oy1 <= fixed <= oy2:
            cuts.append((ox1, ox2))
        elif not horizontal and ox1 <= fixed <= ox2:
            cuts.append((oy1, oy2))
    segs = [(a, b)]
    for c1, c2 in cuts:
        nxt = []
        for s1, s2 in segs:
            if c2 <= s1 or c1 >= s2:
                nxt.append((s1, s2))
                continue
            if c1 > s1:
                nxt.append((s1, c1))
            if c2 < s2:
                nxt.append((c2, s2))
        segs = nxt
    out = []
    for s1, s2 in segs:
        if s2 - s1 < 0.2:
            continue
        # 양끝이 모두 패드로 잘린 조각(패드 사이 실크)은 버리고, 모서리에 이어진 조각만 남긴다
        if cuts and abs(s1 - a) > 1e-9 and abs(s2 - b) > 1e-9:
            continue
        out.append(((s1, fixed), (s2, fixed)) if horizontal else ((fixed, s1), (fixed, s2)))
    return out


def line(p1, p2, layer, width):
    return ["fp_line", ["start", p1[0], p1[1]], ["end", p2[0], p2[1]],
            ["stroke", ["width", width], ["type", Sym("solid")]], ["layer", layer], uid()]


def font(size=1.0, thick=0.15):
    s = min(size, 1.0)
    return ["effects", ["font", ["size", s, s], ["thickness", round(s * 0.15, 3)]]]


def footprint_name(spec: PartSpec) -> str:
    pkg = spec.package
    base = re.sub(r"[^A-Za-z0-9._+-]+", "_", pkg.name).strip("_") or "PKG"
    parts = [base]
    if pkg.family == "explicit" or (pkg.explicit_pads and pkg.family != "bga"):
        parts.append(re.sub(r"[^A-Za-z0-9._+-]+", "_", spec.part_number))
    elif pkg.body_width and pkg.body_length and pkg.family not in ("dip", "pin_header", "chip_2terminal"):
        parts.append(f"{num(pkg.body_width)}x{num(pkg.body_length)}mm")
    if pkg.exposed_pad:
        parts.append(f"EP{num(pkg.exposed_pad.width)}x{num(pkg.exposed_pad.length)}mm")
    if pkg.pitch:
        parts.append(f"P{num(pkg.pitch)}mm")
    name = "_".join(parts)
    return re.sub(r"_+", "_", name)


def build_footprint(spec: PartSpec) -> Footprint:
    pkg = spec.package
    notes: list[str] = []
    pads = generate_pads(pkg, notes)
    copper = [p for p in pads if p.is_copper]
    pad_box = bbox_union([p.bbox for p in pads])

    if pkg.family == "chip_2terminal":
        bw = pkg.lead_span_max or pkg.lead_span_min or pkg.body_length or (pad_box[2] - pad_box[0])
        bh = pkg.lead_width_max or pkg.body_width or (pad_box[3] - pad_box[1])
    else:
        bw = pkg.body_width or (pad_box[2] - pad_box[0])
        bh = pkg.body_length or (pad_box[3] - pad_box[1])
    if pkg.family == "pin_header" and not pkg.body_width:
        bw = (pkg.rows or 1) * (pkg.pitch or 2.54)
        bh = (pkg.pin_count // (pkg.rows or 1)) * (pkg.pitch or 2.54)
    body = (-bw / 2, -bh / 2, bw / 2, bh / 2)

    margin = 0.5 if pkg.family == "bga" else 0.25
    cb = bbox_union([pad_box, body])
    court = (math.floor((cb[0] - margin) * 100) / 100, math.floor((cb[1] - margin) * 100) / 100,
             math.ceil((cb[2] + margin) * 100) / 100, math.ceil((cb[3] + margin) * 100) / 100)

    name = footprint_name(spec)
    is_tht = any(p.kind == "thru_hole" for p in pads)

    items: list = [
        "footprint", name,
        ["version", Sym("20241229")],
        ["generator", "webFootprintAgent"],
        ["generator_version", "0.1"],
        ["layer", "F.Cu"],
        ["descr", f"{pkg.name} ({spec.part_number}) {spec.datasheet_url}".strip()],
        ["tags", f"{pkg.name} {pkg.family}"],
        ["property", "Reference", "REF**", ["at", 0, court[1] - 0.8, 0], ["layer", "F.SilkS"], uid(), font()],
        ["property", "Value", name, ["at", 0, court[3] + 0.8, 0], ["layer", "F.Fab"], uid(), font()],
        ["property", "Datasheet", spec.datasheet_url or "", ["at", 0, 0, 0], ["layer", "F.Fab"],
         ["hide", Sym("yes")], uid(), font()],
        ["property", "Description", f"{pkg.name}, {spec.part_number}", ["at", 0, 0, 0], ["layer", "F.Fab"],
         ["hide", Sym("yes")], uid(), font()],
        ["attr", Sym("through_hole" if is_tht else "smd")],
    ]

    # pin1 위치 → 가장 가까운 몸체 모서리에 모따기
    p1 = next((p for p in copper if p.number in ("1", "A1")), copper[0] if copper else None)
    corners = [(body[0], body[1]), (body[2], body[1]), (body[2], body[3]), (body[0], body[3])]
    ci = min(range(4), key=lambda i: (corners[i][0] - p1.x) ** 2 + (corners[i][1] - p1.y) ** 2) if p1 else 0
    ch = min(1.0, 0.25 * min(bw, bh))
    pts = []
    for i, (x, y) in enumerate(corners):
        if i == ci:
            prev_c, next_c = corners[i - 1], corners[(i + 1) % 4]
            dx1, dy1 = math.copysign(ch, prev_c[0] - x) if prev_c[0] != x else 0, math.copysign(ch, prev_c[1] - y) if prev_c[1] != y else 0
            dx2, dy2 = math.copysign(ch, next_c[0] - x) if next_c[0] != x else 0, math.copysign(ch, next_c[1] - y) if next_c[1] != y else 0
            pts += [(x + dx1, y + dy1), (x + dx2, y + dy2)]
        else:
            pts.append((x, y))
    items.append(["fp_poly", ["pts", *[["xy", x, y] for x, y in pts]],
                  ["stroke", ["width", 0.1], ["type", Sym("solid")]], ["fill", Sym("no")], ["layer", "F.Fab"], uid()])

    # 실크: 몸체 외곽 + 0.11 mm, 패드와 0.2 mm 이상 이격
    o = 0.11
    sb = (body[0] - o, body[1] - o, body[2] + o, body[3] + o)
    edges = [((sb[0], sb[1]), (sb[2], sb[1])), ((sb[2], sb[1]), (sb[2], sb[3])),
             ((sb[2], sb[3]), (sb[0], sb[3])), ((sb[0], sb[3]), (sb[0], sb[1]))]
    obstacles = [p.bbox for p in copper]
    silk = []
    for a, b in edges:
        for s in clip_segment(a, b, obstacles, 0.2):
            items.append(line(s[0], s[1], "F.SilkS", 0.12))
            silk.append(("line", s))

    # pin1 표시: 패드1 바깥쪽 삼각형
    if p1 and spec.reference_prefix.upper() not in ("R", "C", "L", "FB"):
        x1, y1, x2, y2 = p1.bbox
        s = 0.3
        if pkg.family == "bga" or (abs(p1.x) < 1e-6 and abs(p1.y) < 1e-6):
            # 몸체 좌상단 모서리 바깥
            tri = [(sb[0] - 0.35, sb[1] - 0.35), (sb[0] - 0.35 - s, sb[1] - 0.35), (sb[0] - 0.35, sb[1] - 0.35 - s)]
        elif abs(p1.x) >= abs(p1.y):  # 좌/우 변의 패드
            sign = -1 if p1.x < 0 else 1
            tx = (x1 if sign < 0 else x2) + sign * 0.35
            tri = [(tx, p1.y), (tx + sign * s * 1.4, p1.y - s), (tx + sign * s * 1.4, p1.y + s)]
        else:                          # 상/하 변의 패드
            sign = -1 if p1.y < 0 else 1
            ty = (y1 if sign < 0 else y2) + sign * 0.35
            tri = [(p1.x, ty), (p1.x - s, ty + sign * s * 1.4), (p1.x + s, ty + sign * s * 1.4)]
        items.append(["fp_poly", ["pts", *[["xy", x, y] for x, y in tri]],
                      ["stroke", ["width", 0.12], ["type", Sym("solid")]], ["fill", Sym("yes")], ["layer", "F.SilkS"], uid()])
        silk.append(("poly", tri))

    items.append(["fp_rect", ["start", court[0], court[1]], ["end", court[2], court[3]],
                  ["stroke", ["width", 0.05], ["type", Sym("solid")]], ["fill", Sym("no")], ["layer", "F.CrtYd"], uid()])
    fsize = max(0.4, min(1.0, min(bw, bh) * 0.25))
    items.append(["fp_text", Sym("user"), "${REFERENCE}", ["at", 0, 0, 0], ["layer", "F.Fab"], uid(), font(fsize)])

    for p in pads:
        at = ["at", p.x, p.y] + ([p.rot] if p.rot else [])
        node = ["pad", p.number, Sym(p.kind), Sym(p.shape), at, ["size", p.w, p.h]]
        if p.drill and p.kind != "smd":
            node.append(["drill", p.drill])
        node.append(["layers", *p.layers])
        if p.shape == "roundrect":
            node.append(["roundrect_rratio", 0.25])
        node.append(uid())
        items.append(node)
    items.append(["embedded_fonts", Sym("no")])

    fp = Footprint(name=name, pads=pads, body=body, courtyard=court, notes=notes, silk=silk)
    fp.text = dumps(items) + "\n"
    return fp
