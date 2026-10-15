"""다른 PC 에서 실행할 때의 점검: 버전 일치, 오래된 KiCad 처리."""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import __version__  # noqa: E402
from app.kicad import verify  # noqa: E402
from app.kicad.library import generate  # noqa: E402
from app.models import PartSpec  # noqa: E402


def test_versions_match():
    """브리지(__version__) · 웹 화면(PAGE_VERSION) · CHANGELOG 최신 버전이 같아야 오래된 브리지 경고가 맞게 동작한다."""
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    page = re.search(r'const PAGE_VERSION = "([\d.]+)"', js).group(1)
    log = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    latest = re.search(r"^\| (\d+\.\d+\.\d+) \|", log, re.M).group(1)
    assert __version__ == page == latest, (__version__, page, latest)


@pytest.mark.skipif(os.name != "nt", reason="가짜 kicad-cli 를 .bat 으로 만든다")
def test_old_kicad_is_skipped_not_failed(tmp_path, monkeypatch):
    fake = tmp_path / "kicad-cli.bat"
    fake.write_text("@echo 8.0.4\r\n", encoding="ascii")
    monkeypatch.setattr(verify, "_kicad_candidates", lambda: [str(fake)])
    info = verify.kicad_info(refresh=True)
    try:
        assert info["major"] == 8 and info["ok"] is False
        assert "KiCad 9" in info["message"]
        assert verify.find_kicad_cli() is None
        spec = PartSpec.model_validate(json.load(open(os.path.join(ROOT, "examples", "R0603.json"), encoding="utf-8")))
        gen = generate(spec, "T")
        res = verify.kicad_render(gen.symbol_lib_text, gen.symbol_name, gen.footprint)
        assert res["skipped"] and res["symbol_ok"] is None and "KiCad 9" in res["log"]
    finally:
        verify._KICAD_CACHE.update(at=0.0, info=None)  # 다른 테스트가 실제 KiCad 를 다시 찾게
