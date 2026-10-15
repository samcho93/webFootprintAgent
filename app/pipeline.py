"""에이전트 파이프라인: 확보 → 추출 → 생성 → 규칙검증 → KiCad 검증 → AI 검토(반복) → 저장."""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import traceback
import uuid
from dataclasses import dataclass, field

from .agents.extractor import extract_spec, review_spec
from .agents.finder import find_datasheet
from .agents.llm import LLMError
from .datasheet import DatasheetError, download, select_pages
from .kicad.library import generate, save
from .kicad.preview import footprint_svg, symbol_svg
from .kicad.verify import checks_to_dict, kicad_render, pad_table, verify
from .models import PartSpec, ReviewResult

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.environ.get("FP_AGENT_WORK", os.path.join(ROOT, "work"))
LIB_DIR = os.environ.get("FP_AGENT_LIB", os.path.join(ROOT, "library"))
MAX_REVIEW_ROUNDS = int(os.environ.get("FP_AGENT_REVIEW_ROUNDS", "2"))


@dataclass
class JobInput:
    part_number: str = ""
    manufacturer: str = ""
    datasheet_url: str = ""
    datasheet_path: str = ""      # 업로드된 파일
    package_hint: str = ""
    lib_name: str = "AutoParts"
    auto_save: bool = True
    ai_review: bool = True
    spec: PartSpec | None = None  # 수동/재생성 모드: 추출 생략


@dataclass
class Job:
    id: str
    inp: JobInput
    status: str = "queued"        # queued / running / passed / needs_review / failed / saved
    stage: str = ""
    log: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str = ""
    created: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def say(self, msg: str, level: str = "info"):
        self.log.append({"t": dt.datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg})

    def to_dict(self):
        return {"id": self.id, "status": self.status, "stage": self.stage, "log": self.log,
                "result": self.result, "error": self.error, "created": self.created,
                "input": {k: v for k, v in self.inp.__dict__.items() if k != "spec"}}


JOBS: dict[str, Job] = {}


def start_job(inp: JobInput) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], inp=inp)
    JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "part")[:80]


def _acquire(job: Job) -> tuple[str | None, str]:
    """데이터시트 PDF 경로와 URL 확보. 없으면 웹 검색."""
    inp = job.inp
    jdir = os.path.join(WORK_DIR, job.id)
    if inp.datasheet_path:
        job.say(f"업로드된 데이터시트 사용: {os.path.basename(inp.datasheet_path)}")
        return inp.datasheet_path, inp.datasheet_url
    if inp.datasheet_url:
        job.say(f"링크에서 데이터시트 다운로드: {inp.datasheet_url}")
        return download(inp.datasheet_url, os.path.join(jdir, "datasheet.pdf")), inp.datasheet_url
    if not inp.part_number:
        raise ValueError("부품 번호, 데이터시트 링크, 파일 중 하나는 필요합니다")
    job.stage = "search"
    found = find_datasheet(inp.part_number, inp.manufacturer, log=job.say)
    if found.notes:
        job.say(f"검색 메모: {found.notes}")
    for c in found.candidates:
        job.say(f"후보: {c.url} ({c.source})")
        try:
            return download(c.url, os.path.join(jdir, "datasheet.pdf")), c.url
        except Exception as e:  # 다음 후보로
            job.say(f"  다운로드 실패: {e}", "warning")
    raise DatasheetError("검색된 후보에서 데이터시트를 내려받지 못했습니다. 링크나 파일을 직접 지정해 주세요.")


def _summary(gen) -> dict:
    fp = gen.footprint
    return {
        "footprint_name": fp.name,
        "body_mm": [round(fp.body[2] - fp.body[0], 3), round(fp.body[3] - fp.body[1], 3)],
        "courtyard_mm": [round(fp.courtyard[2] - fp.courtyard[0], 3), round(fp.courtyard[3] - fp.courtyard[1], 3)],
        "land_pattern_notes": fp.notes,
        "pads": [{k: v for k, v in p.items() if k in ("number", "x", "y", "w", "h", "rot", "type", "drill")}
                 for p in pad_table(fp) if p["number"] != "(paste)"],
    }


def evaluate(spec: PartSpec, lib_name: str, job: Job | None = None) -> tuple[object, dict]:
    say = job.say if job else (lambda *a, **k: None)
    gen = generate(spec, lib_name)
    checks = checks_to_dict(verify(spec, gen.footprint))
    n_err = sum(c["level"] == "error" for c in checks)
    n_warn = sum(c["level"] == "warning" for c in checks)
    say(f"규칙 검증: 오류 {n_err}, 경고 {n_warn}", "error" if n_err else ("warning" if n_warn else "info"))
    if job:
        job.stage = "verify"
    say("KiCad CLI 로 파싱/렌더링 검증 중…")
    render = kicad_render(gen.symbol_lib_text, gen.symbol_name, gen.footprint)
    if render["cli"]:
        ok = render["symbol_ok"] and render["footprint_ok"]
        say(f"KiCad 검증: 심볼 {'OK' if render['symbol_ok'] else '실패'}, 풋프린트 {'OK' if render['footprint_ok'] else '실패'}",
            "info" if ok else "error")
    else:
        say(render["log"], "warning")
    return gen, {
        "spec": spec.model_dump(),
        "symbol_name": gen.symbol_name,
        "footprint_name": gen.footprint.name,
        "checks": checks,
        "kicad": {k: render[k] for k in ("cli", "symbol_ok", "footprint_ok", "log")},
        "symbol_svg": symbol_svg(spec),
        "footprint_svg": footprint_svg(gen.footprint),
        "kicad_symbol_svg": render["symbol_svg"],
        "kicad_footprint_svg": render["footprint_svg"],
        "pads": pad_table(gen.footprint),
        "land_notes": gen.footprint.notes,
        "files": {"symbol": gen.symbol_lib_text, "footprint": gen.footprint.text},
    }


def _passed(res: dict, review: ReviewResult | None) -> bool:
    if any(c["level"] == "error" for c in res["checks"]):
        return False
    k = res["kicad"]
    if k["cli"] and not (k["symbol_ok"] and k["footprint_ok"]):
        return False
    return review is None or review.verdict == "pass"


def _run(job: Job):
    inp = job.inp
    job.status = "running"
    try:
        pdf_path, url = (inp.datasheet_path or None, inp.datasheet_url)
        pdf_bytes, pages, total = None, [], 0
        if inp.spec is None or inp.ai_review:
            job.stage = "acquire"
            try:
                pdf_path, url = _acquire(job)
            except Exception as e:
                if inp.spec is None:
                    raise
                job.say(f"데이터시트 없이 진행: {e}", "warning")
        if pdf_path:
            pdf_bytes, pages, total = select_pages(pdf_path, part_hint=inp.part_number)
            job.say(f"데이터시트 {total}쪽 중 관련 {len(pages)}쪽 선택: {pages}")

        if inp.spec is not None:
            spec = inp.spec
            job.say("입력된 사양(JSON)으로 생성")
        else:
            job.stage = "extract"
            spec = extract_spec(pdf_bytes, pages, total, inp.part_number, inp.package_hint, log=job.say)
            job.say(f"추출 완료: {spec.part_number} / {spec.package.name} / 핀 {len(spec.pins)}개")
        if url and not spec.datasheet_url:
            spec.datasheet_url = url
        if inp.part_number and not spec.part_number:
            spec.part_number = inp.part_number

        review: ReviewResult | None = None
        history = []
        for rnd in range(1, MAX_REVIEW_ROUNDS + 2):
            job.stage = f"generate#{rnd}"
            job.say(f"[{rnd}회차] 심볼/풋프린트 생성")
            try:
                gen, res = evaluate(spec, inp.lib_name, job)
            except ValueError as e:  # 사양 자체가 생성 불가 (치수 누락 등)
                job.say(f"생성 실패: {e}", "error")
                if not inp.ai_review or rnd > MAX_REVIEW_ROUNDS:
                    raise
                gen, res = None, {"spec": spec.model_dump(), "checks": [{"level": "error", "check": "generate", "message": str(e)}],
                                  "kicad": {"cli": None}}
            if not inp.ai_review or rnd > MAX_REVIEW_ROUNDS:
                review = None if not inp.ai_review else review
                break
            job.stage = f"review#{rnd}"
            summary = _summary(gen) if gen else {"error": "generation failed"}
            review = review_spec(pdf_bytes, pages, total, spec, summary, res["checks"], log=job.say)
            history.append(review.model_dump(exclude={"corrected_spec"}))
            errs = [i for i in review.issues if i.severity == "error"]
            job.say(f"검토 결과: {review.verdict} — {review.summary}", "info" if review.verdict == "pass" else "warning")
            for i in review.issues:
                job.say(f"  [{i.severity}] {i.area}: {i.description} {('(' + i.evidence + ')') if i.evidence else ''}",
                        "error" if i.severity == "error" else ("warning" if i.severity == "warning" else "info"))
            if review.verdict == "pass" or not errs:
                if gen is None:
                    raise ValueError("생성 실패를 검토 단계에서 해결하지 못함")
                break
            if review.corrected_spec is None:
                job.say("검토자가 수정안을 내지 못함 → 사람 검토 필요", "warning")
                break
            job.say("검토자 수정안 반영 후 재생성")
            spec = review.corrected_spec
            if url and not spec.datasheet_url:
                spec.datasheet_url = url

        if gen is None:
            raise ValueError("생성 실패")
        res["review"] = review.model_dump(exclude={"corrected_spec"}) if review else None
        res["review_history"] = history
        res["datasheet"] = {"url": spec.datasheet_url, "pages": pages, "total": total,
                            "path": pdf_path}
        job.result = res
        passed = _passed(res, review)
        job.status = "passed" if passed else "needs_review"
        job.stage = "done"
        if passed and inp.auto_save:
            do_save(job)
        elif not passed:
            job.say("자동 검증을 통과하지 못했습니다. 결과를 확인/수정한 뒤 저장하세요.", "warning")
    except (LLMError, DatasheetError, ValueError) as e:
        job.status, job.error = "failed", str(e)
        job.say(str(e), "error")
    except Exception as e:  # 예상치 못한 오류도 화면에 표시
        job.status, job.error = "failed", f"{type(e).__name__}: {e}"
        job.say(job.error, "error")
        job.say(traceback.format_exc(), "debug")


def do_save(job: Job, force: bool = False) -> dict:
    res = job.result
    if not res:
        raise ValueError("저장할 결과가 없습니다")
    spec = PartSpec.model_validate(res["spec"])
    gen = generate(spec, job.inp.lib_name)
    extra = {"status": job.status if not force else f"{job.status} (수동 승인)",
             "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
             "checks": res.get("checks"), "review": res.get("review"),
             "review_history": res.get("review_history"), "datasheet": res.get("datasheet")}
    paths = save(gen, LIB_DIR, job.inp.lib_name, extra, (res.get("datasheet") or {}).get("path"))
    job.status = "saved"
    job.result["saved"] = paths
    job.say(f"라이브러리에 저장: {paths['symbol_lib']} / {paths['footprint']}")
    return paths
