"""데이터시트 다운로드 및 관련 페이지 추출 (PyMuPDF)."""
from __future__ import annotations

import os
import re

import fitz  # PyMuPDF
import httpx2 as httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MAX_BYTES = 60 * 1024 * 1024

# 핀/패키지 정보가 있을 가능성이 높은 페이지를 고르기 위한 키워드 가중치
KEYWORDS = {
    r"pin\s*(configuration|assignment|description|function|out|map)s?": 6,
    r"pinout": 6,
    r"terminal\s+(configuration|functions?)": 6,
    r"ball\s*(map|assignment)": 6,
    r"package\s*(outline|dimension|drawing|information)": 6,
    r"mechanical\s*(data|drawing|dimension)": 6,
    r"(recommended|suggested)\s*(land|pcb|footprint|board|solder|pad)": 8,
    r"land\s*pattern": 8,
    r"footprint": 4,
    r"dimensions?\s*\(?mm\)?": 3,
    r"\bmin\b.*\bnom\b.*\bmax\b": 3,
    r"ordering\s*information": 2,
    r"\bpin\s*(no|number|#)": 3,
    r"\b(I/O|type)\b": 1,
}


class DatasheetError(Exception):
    pass


def download(url: str, dest: str) -> str:
    """URL 에서 PDF 다운로드. HTML 페이지면 그 안의 .pdf 링크를 한 단계 따라간다."""
    with httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": UA, "Accept": "application/pdf,*/*"}) as c:
        r = c.get(url)
        r.raise_for_status()
        data = r.content
        if not data.startswith(b"%PDF"):
            ctype = r.headers.get("content-type", "")
            if "html" in ctype:
                links = re.findall(rb'href="([^"]+\.pdf[^"]*)"', data, re.I)
                for link in links[:3]:
                    nxt = httpx.URL(str(r.url)).join(link.decode("utf-8", "ignore"))
                    r2 = c.get(nxt)
                    if r2.status_code == 200 and r2.content.startswith(b"%PDF"):
                        data = r2.content
                        break
            if not data.startswith(b"%PDF"):
                raise DatasheetError(f"PDF 가 아님 (content-type={ctype})")
        if len(data) > MAX_BYTES:
            raise DatasheetError("PDF 가 너무 큼 (>60MB)")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def page_scores(doc: fitz.Document, part_hint: str = "") -> list[tuple[int, float]]:
    scores = []
    for i, page in enumerate(doc):
        t = page.get_text("text").lower()
        s = 0.0
        for pat, w in KEYWORDS.items():
            s += w * min(len(re.findall(pat, t)), 3)
        if part_hint and part_hint.lower() in t:
            s += 1
        # 그림 위주(치수 도면) 페이지 가산점
        if len(page.get_drawings()) > 150:
            s += 2
        scores.append((i, s))
    return scores


def select_pages(path: str, max_pages: int = 24, part_hint: str = "") -> tuple[bytes, list[int], int]:
    """관련 페이지만 뽑은 PDF 바이트, 원본 페이지 번호(1-base), 전체 페이지 수."""
    doc = fitz.open(path)
    n = doc.page_count
    if n <= max_pages:
        pages = list(range(n))
    else:
        scored = page_scores(doc, part_hint)
        keep = {0, 1}  # 표지/개요 (부품 설명, 패키지 옵션)
        for i, s in sorted(scored, key=lambda x: -x[1]):
            if len(keep) >= max_pages:
                break
            if s > 0:
                keep.add(i)
        # 패키지 도면은 대개 마지막 쪽에 몰려 있으므로 끝부분도 보강
        for i in range(n - 1, max(n - 6, -1), -1):
            if len(keep) >= max_pages:
                break
            keep.add(i)
        pages = sorted(keep)
    sub = fitz.open()
    for i in pages:
        sub.insert_pdf(doc, from_page=i, to_page=i)
    data = sub.tobytes(garbage=3, deflate=True)
    sub.close()
    doc.close()
    return data, [p + 1 for p in pages], n


def page_text(path: str, pages: list[int]) -> str:
    doc = fitz.open(path)
    out = []
    for p in pages:
        out.append(f"--- page {p} ---\n" + doc[p - 1].get_text("text"))
    doc.close()
    return "\n".join(out)
