"""오프라인 생성기 테스트: examples/*.json → 심볼/풋프린트 생성 → 규칙 검증 → KiCad CLI 파싱.

실행: python -m pytest tests  (또는 python tests/test_generators.py)
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.kicad.library import generate  # noqa: E402
from app.kicad.verify import find_kicad_cli, kicad_render, verify  # noqa: E402
from app.models import PartSpec  # noqa: E402

EXAMPLES = sorted(glob.glob(os.path.join(ROOT, "examples", "*.json")))


def run_one(path):
    spec = PartSpec.model_validate(json.load(open(path, encoding="utf-8")))
    gen = generate(spec, "TestLib")
    checks = verify(spec, gen.footprint)
    errors = [c for c in checks if c.level == "error"]
    render = kicad_render(gen.symbol_lib_text, gen.symbol_name, gen.footprint)
    return gen, checks, errors, render


def test_examples():
    assert EXAMPLES
    for path in EXAMPLES:
        gen, checks, errors, render = run_one(path)
        assert not errors, f"{os.path.basename(path)}: {errors}"
        if find_kicad_cli():
            assert render["symbol_ok"], f"{path} symbol: {render['log']}"
            assert render["footprint_ok"], f"{path} footprint: {render['log']}"


if __name__ == "__main__":
    for path in EXAMPLES:
        gen, checks, errors, render = run_one(path)
        print(f"== {os.path.basename(path)} -> {gen.footprint.name}")
        for c in checks:
            print(f"   [{c.level:7}] {c.check}: {c.message}")
        for n in gen.footprint.notes:
            print(f"   note: {n}")
        print(f"   kicad-cli symbol={render['symbol_ok']} footprint={render['footprint_ok']}")
        if render["log"] and not (render["symbol_ok"] and render["footprint_ok"]):
            print("   log:", render["log"][:800])
