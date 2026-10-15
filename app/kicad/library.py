"""생성 + 라이브러리 저장."""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass

from ..models import PartSpec
from .footprint import Footprint, build_footprint
from .sexpr import dumps, loads
from .symbol import build_symbol, sanitize_name, symbol_library


@dataclass
class Generated:
    spec: PartSpec
    symbol_name: str
    symbol_node: list
    symbol_lib_text: str      # 단일 심볼 라이브러리 (미리보기/다운로드용)
    footprint: Footprint


def generate(spec: PartSpec, lib_name: str) -> Generated:
    fp = build_footprint(spec)
    node = build_symbol(spec, f"{lib_name}:{fp.name}")
    return Generated(spec, sanitize_name(spec.part_number), node, symbol_library([node]), fp)


def _lib_table(kind: str, lib_name: str, uri: str) -> str:
    uri = uri.replace("\\", "/")
    return (f"({kind}_lib_table\n\t(version 7)\n"
            f'\t(lib (name "{lib_name}")(type "KiCad")(uri "{uri}")(options "")(descr "webFootprintAgent 자동 생성"))\n)\n')


def save(gen: Generated, lib_dir: str, lib_name: str, extra: dict, datasheet_path: str | None = None) -> dict:
    os.makedirs(lib_dir, exist_ok=True)
    sym_path = os.path.join(lib_dir, f"{lib_name}.kicad_sym")
    pretty = os.path.join(lib_dir, f"{lib_name}.pretty")
    os.makedirs(pretty, exist_ok=True)

    # 심볼 라이브러리 병합 (같은 이름은 교체)
    if os.path.exists(sym_path):
        with open(sym_path, encoding="utf-8") as f:
            tree = loads(f.read())
        symbols = [n for n in tree[1:] if isinstance(n, list) and str(n[0]) == "symbol" and n[1] != gen.symbol_name]
    else:
        symbols = []
    symbols.append(gen.symbol_node)
    symbols.sort(key=lambda n: str(n[1]).lower())
    with open(sym_path, "w", encoding="utf-8") as f:
        f.write(symbol_library(symbols))

    fp_path = os.path.join(pretty, gen.footprint.name + ".kicad_mod")
    with open(fp_path, "w", encoding="utf-8") as f:
        f.write(gen.footprint.text)

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", gen.spec.part_number)
    os.makedirs(os.path.join(lib_dir, "specs"), exist_ok=True)
    spec_path = os.path.join(lib_dir, "specs", f"{safe}.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump({"spec": gen.spec.model_dump(), "footprint": gen.footprint.name,
                   "symbol": gen.symbol_name, **extra}, f, ensure_ascii=False, indent=2)
    ds_saved = None
    if datasheet_path and os.path.exists(datasheet_path):
        os.makedirs(os.path.join(lib_dir, "datasheets"), exist_ok=True)
        ds_saved = os.path.join(lib_dir, "datasheets", f"{safe}.pdf")
        if os.path.abspath(ds_saved) != os.path.abspath(datasheet_path):
            shutil.copyfile(datasheet_path, ds_saved)

    absdir = os.path.abspath(lib_dir)
    with open(os.path.join(lib_dir, "sym-lib-table"), "w", encoding="utf-8") as f:
        f.write(_lib_table("sym", lib_name, os.path.join(absdir, f"{lib_name}.kicad_sym")))
    with open(os.path.join(lib_dir, "fp-lib-table"), "w", encoding="utf-8") as f:
        f.write(_lib_table("fp", lib_name, os.path.join(absdir, f"{lib_name}.pretty")))
    return {"symbol_lib": sym_path, "footprint": fp_path, "spec": spec_path, "datasheet": ds_saved}


def list_library(lib_dir: str) -> list[dict]:
    out = []
    d = os.path.join(lib_dir, "specs")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    j = json.load(f)
                out.append({"part_number": j["spec"]["part_number"], "manufacturer": j["spec"].get("manufacturer", ""),
                            "package": j["spec"]["package"]["name"], "footprint": j.get("footprint"),
                            "status": j.get("status", ""), "saved_at": j.get("saved_at", ""), "file": fn})
            except Exception:  # 손상된 파일은 목록에서 제외
                continue
    return out


__all__ = ["generate", "save", "list_library", "Generated", "dumps"]
