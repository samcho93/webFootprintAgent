"""생성 결과에 대한 결정론적(규칙 기반) 검증 + KiCad CLI 파싱/렌더링 검증."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass

from ..models import PartSpec
from .footprint import Footprint, Pad
from .symbol import placed_pins


@dataclass
class Check:
    level: str     # ok / warning / error
    check: str
    message: str


MIN_KICAD = 9  # 생성 파일 형식 (심볼 20241209 / 풋프린트 20241229) 은 KiCad 9 부터 열린다


def _ver_key(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _kicad_candidates() -> list[str]:
    """kicad-cli 후보 (높은 버전 우선). PCB Agent(server/kicad.js) 와 같은 위치를 찾는다."""
    out: list[str] = []
    env = os.environ.get("KICAD_CLI")
    if env:
        out.append(env)

    def versions(root: str) -> list[str]:
        try:
            vs = [v for v in os.listdir(root) if re.fullmatch(r"\d+(\.\d+)?", v)]
        except OSError:
            return []
        return [os.path.join(root, v, "bin", "kicad-cli.exe") for v in sorted(vs, key=_ver_key, reverse=True)]

    if os.name == "nt":
        roots = {os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")} - {None}
        roots |= {f"{d}:\\Program Files" for d in "CDEFGH"}
        found = []
        for r in roots:
            found += versions(os.path.join(r, "KiCad"))
        if os.environ.get("LOCALAPPDATA"):  # 사용자별 설치
            found += versions(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "KiCad"))
        out += sorted(found, key=lambda p: _ver_key(os.path.basename(os.path.dirname(os.path.dirname(p)))), reverse=True)
    elif sys.platform == "darwin":
        out.append("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
    else:
        out += ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"]
    w = shutil.which("kicad-cli")
    if w:
        out.append(w)
    seen, uniq = set(), []
    for c in out:
        k = os.path.normcase(os.path.abspath(c))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


_KICAD_CACHE: dict = {"at": 0.0, "info": None}


def kicad_info(refresh: bool = False) -> dict:
    """{cli, version, major, ok, message, searched} — `kicad-cli version` 은 수 초 걸리므로 5분 캐시."""
    if not refresh and _KICAD_CACHE["info"] and time.time() - _KICAD_CACHE["at"] < 300:
        return _KICAD_CACHE["info"]
    cands = _kicad_candidates()
    info = {"cli": None, "version": "", "major": None, "ok": False, "searched": cands,
            "message": "kicad-cli 를 찾지 못했습니다 — KiCad 9 를 설치하거나 KICAD_CLI 로 경로를 지정하세요. KiCad 검증은 생략됩니다."}
    for c in cands:
        if not os.path.exists(c):
            continue
        try:
            out = subprocess.run([c, "version"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"(\d+)\.(\d+)(\.\d+)?", out)
        ver = m.group(0) if m else ""
        major = int(m.group(1)) if m else None
        info.update(cli=c, version=ver, major=major)
        if major is not None and major < MIN_KICAD:
            info["message"] = (f"KiCad {ver} 은 지원하지 않습니다 — KiCad {MIN_KICAD} 이상이 필요합니다 "
                               f"(생성 파일은 KiCad {MIN_KICAD} 형식이라 KiCad {major} 에서는 열리지 않음). KiCad 검증은 생략됩니다.")
            continue  # 더 새 버전이 다른 곳에 있으면 그것을 쓴다
        info.update(ok=True, message=f"KiCad {ver or '?'}")
        break
    _KICAD_CACHE.update(at=time.time(), info=info)
    return info


def find_kicad_cli() -> str | None:
    """검증에 쓸 수 있는(KiCad 9 이상) kicad-cli 경로."""
    info = kicad_info()
    return info["cli"] if info["ok"] else None


def rect_gap(a, b) -> float:
    """두 사각형 간 최소 거리 (겹치면 음수)."""
    dx = max(a[0] - b[2], b[0] - a[2])
    dy = max(a[1] - b[3], b[1] - a[3])
    if dx < 0 and dy < 0:
        return max(dx, dy)
    return (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5


def verify(spec: PartSpec, fp: Footprint) -> list[Check]:
    out: list[Check] = []
    add = lambda lvl, c, m: out.append(Check(lvl, c, m))
    pkg = spec.package

    # --- 심볼 핀
    nums = [p.number.strip() for p in spec.pins]
    dup = [n for n, c in Counter(nums).items() if c > 1]
    if not spec.pins:
        add("error", "symbol.pins", "핀이 하나도 없음")
    if dup:
        add("error", "symbol.unique", f"중복된 핀 번호: {', '.join(dup)}")
    else:
        add("ok", "symbol.unique", f"핀 번호 {len(nums)}개 모두 고유")
    empty = [p.number for p in spec.pins if not p.name.strip()]
    if empty:
        add("warning", "symbol.names", f"이름 없는 핀: {', '.join(empty)}")

    placed, _ = placed_pins(spec)
    off = [pp.pin.number for pp in placed if abs(pp.x / 1.27 - round(pp.x / 1.27)) > 1e-6 or abs(pp.y / 1.27 - round(pp.y / 1.27)) > 1e-6]
    if off:
        add("error", "symbol.grid", f"50 mil 격자 밖 핀: {', '.join(off)}")
    else:
        add("ok", "symbol.grid", "모든 핀 끝점이 50 mil 격자 위")
    pos = Counter((round(pp.x, 3), round(pp.y, 3)) for pp in placed)
    if any(c > 1 for c in pos.values()):
        add("error", "symbol.overlap", "같은 위치에 겹친 핀 존재")

    # --- 심볼 ↔ 풋프린트 대응
    copper = [p for p in fp.pads if p.is_copper]
    pad_nums = {p.number for p in copper if p.number}
    sym_nums = set(nums)
    miss_fp = sorted(sym_nums - pad_nums)
    miss_sym = sorted(pad_nums - sym_nums)
    if miss_fp:
        add("error", "map.symbol_to_pad", f"풋프린트에 없는 심볼 핀: {', '.join(miss_fp)}")
    if miss_sym:
        add("error", "map.pad_to_symbol", f"심볼에 없는 패드: {', '.join(miss_sym)}")
    if not miss_fp and not miss_sym:
        add("ok", "map", f"심볼 핀 {len(sym_nums)}개 ↔ 패드 {len(pad_nums)}개 1:1 대응")

    expected = pkg.pin_count + (1 if pkg.exposed_pad else 0)
    if pkg.family == "bga":
        expected = len(pad_nums)
    if len(pad_nums) != expected and not pkg.explicit_pads:
        add("warning", "package.count", f"패키지 pin_count({expected}) 와 패드 수({len(pad_nums)}) 불일치")

    # --- 치수 상식 검사
    if pkg.family == "gullwing_dual" or pkg.family == "gullwing_quad":
        if pkg.lead_span_max and pkg.body_width and pkg.lead_span_max <= pkg.body_width:
            add("error", "package.span", f"리드 스팬 E({pkg.lead_span_max}) ≤ 몸체 폭({pkg.body_width}) — 치수 오인 가능성")
    if pkg.family in ("gullwing_dual", "nolead_dual", "dip") and pkg.pitch and pkg.body_length:
        row = (pkg.pin_count // 2 - 1) * pkg.pitch
        if row >= pkg.body_length:
            add("error", "package.row", f"핀 열 길이 {row:.2f} ≥ 몸체 길이 {pkg.body_length} — pitch/핀수/D 확인")
    if pkg.exposed_pad and pkg.body_width and pkg.exposed_pad.width >= pkg.body_width:
        add("error", "package.ep", "EP 가 몸체보다 큼")
    if pkg.height_max is None:
        add("warning", "package.height", "최대 높이(A) 미기재")

    # --- 패드 간 간격
    min_gap, worst = 1e9, None
    for i, a in enumerate(copper):
        for b in copper[i + 1:]:
            if a.number and a.number == b.number:
                continue
            g = rect_gap(a.bbox, b.bbox)
            if g < min_gap:
                min_gap, worst = g, (a.number, b.number)
    if worst:
        if min_gap < 0:
            add("error", "pads.overlap", f"패드 {worst[0]}–{worst[1]} 겹침 ({min_gap:.3f} mm)")
        elif min_gap < 0.15:
            add("warning", "pads.clearance", f"최소 패드 간격 {min_gap:.3f} mm (패드 {worst[0]}–{worst[1]}) < 0.15 mm")
        else:
            add("ok", "pads.clearance", f"최소 패드 간격 {min_gap:.3f} mm")

    # --- 피치 일관성
    if pkg.pitch and pkg.family not in ("explicit", "chip_2terminal") and len(copper) > 2:
        sig = [p for p in copper if p.number and not (pkg.exposed_pad and p.number == (pkg.exposed_pad.number or str(pkg.pin_count + 1)))]
        nn = []
        for a in sig:
            d = min(((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5 for b in sig if b is not a)
            nn.append(d)
        # 가장 가까운 패드 간격이 피치와 같아야 한다 (SOT-23-5 처럼 빈 자리가 있어 더 먼 것은 정상)
        if abs(min(nn) - pkg.pitch) > 0.01:
            add("warning", "pads.pitch", f"가장 가까운 패드 간격 {min(nn):.3f} mm 가 피치 {pkg.pitch} mm 와 다름")
        else:
            add("ok", "pads.pitch", f"가장 가까운 패드 간격 = 피치 {pkg.pitch} mm")

    # --- 코트야드 / 드릴
    c = fp.courtyard
    outside = [p.number or "(paste)" for p in fp.pads if p.bbox[0] < c[0] or p.bbox[1] < c[1] or p.bbox[2] > c[2] or p.bbox[3] > c[3]]
    if outside:
        add("error", "courtyard", f"코트야드 밖 패드: {', '.join(outside)}")
    else:
        add("ok", "courtyard", f"코트야드 {c[2]-c[0]:.2f} x {c[3]-c[1]:.2f} mm 가 모든 패드 포함")
    for p in fp.pads:
        if p.kind == "thru_hole" and p.drill and min(p.w, p.h) - p.drill < 0.3:
            add("error", "pads.annular", f"패드 {p.number} 애뉼러 링 < 0.15 mm")
            break
    for p in fp.pads:
        if p.w <= 0 or p.h <= 0:
            add("error", "pads.size", f"패드 {p.number} 크기 0 이하")
            break
    return out


def kicad_render(sym_lib_text: str, sym_name: str, fp: Footprint) -> dict:
    """KiCad CLI 로 실제 파싱되는지 확인하고 SVG 미리보기를 만든다."""
    cli = find_kicad_cli()
    res = {"cli": cli, "symbol_ok": None, "footprint_ok": None, "symbol_svg": "", "footprint_svg": "", "log": "",
           "skipped": not cli}
    if not cli:  # 없거나 KiCad 9 미만 — 부품 오류가 아니라 검증을 못 한 것
        res["log"] = kicad_info()["message"]
        return res
    with tempfile.TemporaryDirectory() as td:
        sym_path = os.path.join(td, "check.kicad_sym")
        with open(sym_path, "w", encoding="utf-8") as f:
            f.write(sym_lib_text)
        pretty = os.path.join(td, "check.pretty")
        os.makedirs(pretty)
        with open(os.path.join(pretty, fp.name + ".kicad_mod"), "w", encoding="utf-8") as f:
            f.write(fp.text)
        so, fo = os.path.join(td, "sym_svg"), os.path.join(td, "fp_svg")
        r1 = subprocess.run([cli, "sym", "export", "svg", "-o", so, "-s", sym_name, sym_path],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        r2 = subprocess.run([cli, "fp", "export", "svg", "-o", fo, "--fp", fp.name,
                             "-l", "F.Cu,F.Paste,F.SilkS,F.Fab,F.CrtYd,F.Mask", pretty],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        res["log"] = "".join(x or "" for x in (r1.stdout, r1.stderr, r2.stdout, r2.stderr)).strip()
        svgs = glob.glob(os.path.join(so, "*.svg"))
        res["symbol_ok"] = r1.returncode == 0 and bool(svgs)
        if svgs:
            res["symbol_svg"] = open(svgs[0], encoding="utf-8").read()
        svgs = glob.glob(os.path.join(fo, "*.svg"))
        res["footprint_ok"] = r2.returncode == 0 and bool(svgs)
        if svgs:
            res["footprint_svg"] = open(svgs[0], encoding="utf-8").read()
    return res


def checks_to_dict(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


def pad_table(fp: Footprint) -> list[dict]:
    return [{"number": p.number or "(paste)", "x": p.x, "y": p.y, "w": p.w, "h": p.h, "shape": p.shape,
             "type": p.kind, "drill": p.drill, "rot": p.rot, "layers": " ".join(p.layers)} for p in fp.pads]
