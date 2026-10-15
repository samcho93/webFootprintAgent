"""PartSpec → KiCad 9 회로 기호 (.kicad_sym) 생성기.

좌표계: 심볼 라이브러리는 Y 위쪽이 + (KiCad 규칙). 모든 핀 끝점은 2.54 mm(100 mil) 격자에 놓인다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..models import PartSpec, Pin
from .sexpr import Sym, dumps

GRID = 2.54
PIN_LEN = 2.54
FONT = 1.27
CHAR_W = 0.95  # 1.27mm 폰트 문자당 대략 폭 (보수적으로)
NAME_OFFSET = 0.508

_NEG_SUPPLY = re.compile(r"^(A|D|P|S)?(GND|VSS|VEE|V-|VNEG|EP|PAD|THERMAL)", re.I)


def sanitize_name(s: str) -> str:
    s = re.sub(r"[:/\\\s\"']+", "_", s.strip())
    return s or "PART"


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def text_len(s: str) -> float:
    # ~{X} (오버바) 표기는 길이 계산에서 제외
    return len(re.sub(r"[~{}]", "", s)) * CHAR_W


def snap_up(v: float, g: float = GRID) -> float:
    return math.ceil(round(v / g, 6)) * g


@dataclass
class PlacedPin:
    pin: Pin
    x: float
    y: float
    angle: int


def assign_sides(pins: list[Pin]) -> dict[str, list[Pin]]:
    sides: dict[str, list[Pin]] = {"left": [], "right": [], "top": [], "bottom": []}
    flexible: list[Pin] = []
    for p in pins:
        if p.side != "auto":
            sides[p.side].append(p)
            continue
        t = p.electrical_type
        if t == "power_in":
            sides["bottom" if _NEG_SUPPLY.match(p.name) else "top"].append(p)
        elif t == "input":
            sides["left"].append(p)
        elif t in ("output", "power_out", "open_collector", "open_emitter", "tri_state"):
            sides["right"].append(p)
        else:
            flexible.append(p)
    for p in flexible:  # 좌우 균형
        (sides["left"] if len(sides["left"]) <= len(sides["right"]) else sides["right"]).append(p)

    # 상/하에 핀이 너무 많으면 좌우로 넘김 (가독성)
    for tb, lr in (("top", "left"), ("bottom", "right")):
        while len(sides[tb]) > 8:
            sides[lr].append(sides[tb].pop())

    order = {p.number: i for i, p in enumerate(pins)}
    for k in sides:
        # 같은 그룹끼리 모으고, 그룹 안에서는 데이터시트 순서 유지
        group_first: dict[str, int] = {}
        for p in sides[k]:
            group_first.setdefault(p.group, order[p.number])
        sides[k].sort(key=lambda p: (group_first[p.group], order[p.number]))
    return sides


def layout(spec: PartSpec):
    sides = assign_sides(spec.pins)
    L, R, T, B = sides["left"], sides["right"], sides["top"], sides["bottom"]

    rows = max(len(L), len(R), 1)
    max_l = max((text_len(p.name) for p in L), default=0)
    max_r = max((text_len(p.name) for p in R), default=0)
    max_t = max((text_len(p.name) for p in T), default=0)
    max_b = max((text_len(p.name) for p in B), default=0)
    n_tb = max(len(T), len(B))

    width_need = max(max_l + max_r + 2 * NAME_OFFSET + 2 * GRID, (n_tb + 1) * GRID, 3 * GRID)
    hw = snap_up(width_need / 2)

    y_top = GRID * math.ceil((rows - 1) / 2)
    y_bot = y_top - (rows - 1) * GRID
    box_top = y_top + GRID + (snap_up(max_t + NAME_OFFSET) if T else 0)
    box_bot = y_bot - GRID - (snap_up(max_b + NAME_OFFSET) if B else 0)

    placed: list[PlacedPin] = []
    for i, p in enumerate(L):
        placed.append(PlacedPin(p, -hw - PIN_LEN, y_top - i * GRID, 0))
    for i, p in enumerate(R):
        placed.append(PlacedPin(p, hw + PIN_LEN, y_top - i * GRID, 180))
    for side, y, ang in ((T, box_top + PIN_LEN, 270), (B, box_bot - PIN_LEN, 90)):
        x0 = -GRID * math.floor((len(side) - 1) / 2)
        for i, p in enumerate(side):
            placed.append(PlacedPin(p, x0 + i * GRID, y, ang))
    return placed, (-hw, box_top, hw, box_bot), sides


def _effects(hide: bool = False, justify: str | None = None) -> list:
    e: list = ["effects", ["font", ["size", FONT, FONT]]]
    if justify:
        e.append(["justify", Sym(justify)])
    if hide:
        e.append(["hide", Sym("yes")])
    return e


def build_symbol(spec: PartSpec, footprint_ref: str) -> list:
    name = sanitize_name(spec.part_number)
    placed, (x1, y1, x2, y2), sides = layout(spec)
    top_extra = PIN_LEN if sides["top"] else 0
    bot_extra = PIN_LEN if sides["bottom"] else 0

    def prop(key, val, x, y, hide=False, justify=None):
        return ["property", key, val, ["at", x, y, 0], _effects(hide, justify)]

    sym = [
        "symbol", name,
        ["pin_names", ["offset", NAME_OFFSET]],
        ["exclude_from_sim", Sym("no")],
        ["in_bom", Sym("yes")],
        ["on_board", Sym("yes")],
        prop("Reference", spec.reference_prefix or "U", x1, y1 + top_extra + 1.27, justify="left bottom"),
        prop("Value", name, x1, y2 - bot_extra - 1.27, justify="left top"),
        prop("Footprint", footprint_ref, 0, y2 - bot_extra - 3.81, hide=True),
        prop("Datasheet", spec.datasheet_url or "~", 0, y2 - bot_extra - 6.35, hide=True),
        prop("Description", spec.description, 0, y2 - bot_extra - 8.89, hide=True),
    ]
    if spec.keywords:
        sym.append(prop("ki_keywords", spec.keywords, 0, 0, hide=True))
    sym.append([
        "symbol", f"{name}_0_1",
        ["rectangle", ["start", x1, y1], ["end", x2, y2],
         ["stroke", ["width", 0.254], ["type", Sym("default")]],
         ["fill", ["type", Sym("background")]]],
    ])
    unit = ["symbol", f"{name}_1_1"]
    for pp in placed:
        p = pp.pin
        unit.append([
            "pin", Sym(p.electrical_type), Sym("line"),
            ["at", pp.x, pp.y, pp.angle],
            ["length", PIN_LEN],
            ["name", p.name, _effects()],
            ["number", p.number, _effects()],
        ])
    sym.append(unit)
    sym.append(["embedded_fonts", Sym("no")])
    return sym


def symbol_library(symbols: list[list]) -> str:
    lib = [
        "kicad_symbol_lib",
        ["version", Sym("20241209")],
        ["generator", "webFootprintAgent"],
        ["generator_version", "0.1"],
        *symbols,
    ]
    return dumps(lib) + "\n"


def placed_pins(spec: PartSpec):
    """검증/미리보기용 배치 결과."""
    placed, box, _ = layout(spec)
    return placed, box
