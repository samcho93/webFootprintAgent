"""데이터시트 PDF → PartSpec 추출 에이전트, 그리고 생성 결과 검토(Review) 에이전트."""
from __future__ import annotations

import base64
import json

from ..models import PartSpec, ReviewResult
from .llm import call_json

SPEC_RULES = """
## Output conventions (follow exactly)
Units: millimetres everywhere. Convert inches/mils (1 mil = 0.0254 mm). If only a nominal value is
given, put it in both *_min and *_max.

### pins
- One entry per physical terminal of the selected package, numbered exactly as the datasheet numbers them
  (e.g. "1".."16", or BGA "A1"). Include NC pins (electrical_type no_connect, name "NC") and the exposed /
  thermal pad if it is a solderable terminal (number it as the datasheet does; if the datasheet gives it no
  number use str(pin_count+1) and use the same string for package.exposed_pad.number).
- If several physical pins share one name (e.g. two GND pins), list each pin separately.
- Names: datasheet names verbatim; active-low signals written as ~{NAME} (e.g. ~{RESET}).
- electrical_type: power supply inputs → power_in; regulator outputs → power_out; ground → power_in;
  digital/analog inputs → input; push-pull outputs → output; open-drain → open_collector; I/O → bidirectional;
  tri-state outputs → tri_state; passive components and transistor terminals → passive; NC → no_connect.
- side: use "auto" unless the datasheet's functional block diagram suggests a better arrangement.
  group: functional grouping such as "Power", "SPI", "GPIO Port A".

### package (the variant matching the requested part number / ordering code)
Choose family:
- gullwing_dual: SOIC/SOP/SSOP/TSSOP/MSOP/SOT-23-5/6/8 with equal pins on two opposite sides.
- gullwing_quad: QFP/LQFP/TQFP. nolead_dual: DFN/SON. nolead_quad: QFN.
- chip_2terminal: 0201..2512 chip parts, SOD-123/323, SMA/SMB/SMC, chip LEDs.
- dip: through-hole DIP. pin_header: simple grid THT connector/header. bga: BGA/WLCSP.
- explicit: anything irregular (SOT-23-3, SOT-89, SOT-223, DPAK/TO-252, D2PAK, TO-220, connectors with
  mounting pegs, modules, unequal pin counts per side, etc.).
Geometry conventions (KiCad footprint orientation: pin 1 at top-left, rows of a dual package run vertically):
- body_width = E1 (body size across the rows, X); body_length = D (body length along the rows, Y).
- lead_span_min/max = E (tip-to-tip overall width across the rows). For no-lead parts = body size across rows.
- lead_length = L (foot/terminal contact length). lead_width = b.
- For quad packages: pins_y = pins on the left side, pins_x = pins on the bottom side.
- chip_2terminal: lead_span = overall length L, lead_length = terminal band length, lead_width = body width W,
  body_length = L, body_width = W.
- dip/pin_header: row_spacing (centre-to-centre), lead_diameter (max lead width/diagonal), rows for headers.
- bga: rows, cols, pitch, ball_diameter, missing_balls (unpopulated positions).
- exposed_pad: the datasheet's exposed-pad dimensions (width X, length Y).
Land pattern:
- If the datasheet provides a recommended land pattern / PCB layout with enough dimensions, convert it into
  explicit_pads (centre origin, X right, Y DOWN, pin 1 top-left) and set land_pattern_source =
  "datasheet_recommended". Also do this for every "explicit" family package (from the land pattern if
  available, otherwise derive pads from the mechanical drawing with IPC-7351 nominal toe/heel/side fillets
  of roughly 0.35/0.35/0.05 mm for gull-wing and 0.3/0/0 mm for flat no-lead terminals, and say so in notes).
  Mechanical/mounting pads use number "" (or the datasheet's number if it is electrically connected).
- Otherwise leave explicit_pads empty; the land pattern will be computed from the package dimensions.

### evidence
List where each fact came from, citing ORIGINAL datasheet page numbers, e.g. "p.4 Table 5-1 Pin Functions",
"p.38 D0008A package outline". Put uncertainties/assumptions in extraction_notes and package.notes.
"""


def _doc_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf",
                   "data": base64.standard_b64encode(pdf_bytes).decode("ascii")},
        "title": "datasheet",
        # 추출과 검토 단계에서 같은 PDF 를 다시 보내므로 캐시
        "cache_control": {"type": "ephemeral"},
    }


def _page_note(pages: list[int], total: int) -> str:
    if len(pages) == total:
        return f"The attached PDF is the complete {total}-page datasheet."
    return (f"The attached PDF contains a subset of a {total}-page datasheet; attached page k corresponds to "
            f"original page {pages}[k-1]. Cite ORIGINAL page numbers.")


def extract_spec(pdf_bytes: bytes, pages: list[int], total: int, part_number: str,
                 package_hint: str = "", log=print, filename_hint: str = "") -> PartSpec:
    file_note = (f'The datasheet file is named "{filename_hint}". The name may contain the exact ordering code — '
                 "use it to choose the part/package only if the datasheet confirms it; it may also be a literature "
                 "number or an arbitrary name.\n") if filename_hint else ""
    task = f"""Task: extract everything needed to build a KiCad symbol and footprint for part "{part_number or '(determine from the datasheet)'}".
{file_note}{_page_note(pages, total)}
{"Requested package variant: " + package_hint if package_hint else "If the datasheet covers several packages, pick the one matching the part number's ordering code; if the part number does not identify one, pick the most common surface-mount variant and explain in extraction_notes."}

Work carefully: first locate the pin-function table and the package drawing for the chosen variant, then fill
every field. Double-check that the number of pins equals the package terminal count.
{SPEC_RULES}"""
    log("데이터시트에서 핀/패키지 정보 추출 중…")
    return call_json([_doc_block(pdf_bytes), {"type": "text", "text": task}], PartSpec, effort="high", log=log)


def review_spec(pdf_bytes: bytes | None, pages: list[int], total: int, spec: PartSpec,
                generated_summary: dict, rule_checks: list[dict], log=print) -> ReviewResult:
    task = f"""Task: independently REVIEW a KiCad symbol + footprint that another engineer generated. Assume it
contains mistakes until you have verified each item against the datasheet.
{_page_note(pages, total) if pdf_bytes else "No datasheet PDF is available; review for internal consistency and against your knowledge of standard packages, and flag anything you cannot verify as a warning."}

Check, item by item:
1. Package variant is the one matching part number "{spec.part_number}".
2. Every pin: number ↔ name mapping, nothing missing/extra, exposed pad handling, electrical_type sensible.
3. Package dimensions (pitch, E, E1, D, L, b, EP, height) match the drawing's min/max table; units correct.
4. Generated pad table (centre origin, Y down, mm): pad count, pin-1 location, numbering direction
   (counter-clockwise from pin 1 when viewed from top), pad positions/sizes plausible against the lead
   geometry and against any recommended land pattern in the datasheet.
5. Rule-check results below: explain or fix every error.

Severity: "error" = would produce a wrong/unusable part (wrong pin mapping, wrong pitch, wrong pad count,
mirrored numbering…). "warning" = questionable but usable. "info" = remarks.
verdict = "pass" only if there are no errors. If there are errors you can fix, return corrected_spec as the
complete corrected PartSpec (same conventions as below); otherwise corrected_spec = null.
Write summary and issue descriptions in Korean; keep evidence as datasheet page references.

## Spec under review
```json
{json.dumps(spec.model_dump(), ensure_ascii=False, indent=1)}
```

## Generated result
```json
{json.dumps(generated_summary, ensure_ascii=False, indent=1)}
```

## Rule checks
```json
{json.dumps(rule_checks, ensure_ascii=False, indent=1)}
```
{SPEC_RULES}"""
    content = ([_doc_block(pdf_bytes)] if pdf_bytes else []) + [{"type": "text", "text": task}]
    log("검토 에이전트가 데이터시트와 대조 검토 중…")
    return call_json(content, ReviewResult, effort="high", log=log)
