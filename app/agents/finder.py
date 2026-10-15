"""데이터시트가 주어지지 않았을 때 웹 검색으로 공식 데이터시트 PDF 를 찾는 에이전트."""
from __future__ import annotations

from ..models import DatasheetSearchResult
from .llm import call_json

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
]


def find_datasheet(part_number: str, manufacturer: str = "", log=print) -> DatasheetSearchResult:
    hint = f" made by {manufacturer}" if manufacturer else ""
    prompt = f"""Find the official datasheet PDF for the electronic component "{part_number}"{hint}.

Requirements:
- Prefer the manufacturer's own site (e.g. ti.com, analog.com, st.com, onsemi.com, nxp.com, microchip.com,
  infineon.com, renesas.com, diodes.com, vishay.com). Distributor mirrors (Digi-Key, Mouser, LCSC, alldatasheet)
  are acceptable only as later fallbacks.
- Each candidate URL must point directly at a PDF file (or a page whose only purpose is serving that PDF).
  Use web_fetch to confirm the URL works when you are unsure.
- Make sure the datasheet actually covers this exact part number / ordering code (including package suffix).
- Return up to 4 candidates, best first. If the part number looks ambiguous, explain in notes."""
    log(f"웹 검색으로 '{part_number}' 데이터시트 탐색 중…")
    return call_json([{"type": "text", "text": prompt}], DatasheetSearchResult, effort="medium", tools=TOOLS,
                     max_tokens=32000, log=log)
