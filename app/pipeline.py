"""에이전트 파이프라인: 확보 → 추출 → 생성 → 규칙검증 → KiCad 검증 → AI 검토(반복) → 저장."""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import queue
import re
import string
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
    filename_hint: str = ""       # 폴더 일괄: 데이터시트 파일 이름 (부품 번호 단서)
    batch_id: str = ""


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
        job.say(f"데이터시트 파일 사용: {inp.filename_hint or os.path.basename(inp.datasheet_path)}")
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
            spec = extract_spec(pdf_bytes, pages, total, inp.part_number, inp.package_hint, log=job.say,
                                filename_hint=inp.filename_hint)
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


SAVE_LOCK = threading.Lock()  # 일괄 처리 중 여러 작업이 같은 심볼 라이브러리를 병합하므로 직렬화


def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def do_save(job: Job, force: bool = False) -> dict:
    res = job.result
    if not res:
        raise ValueError("저장할 결과가 없습니다")
    spec = PartSpec.model_validate(res["spec"])
    gen = generate(spec, job.inp.lib_name)
    ds_path = (res.get("datasheet") or {}).get("path")
    extra = {"status": job.status if not force else f"{job.status} (수동 승인)",
             "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
             "checks": res.get("checks"), "review": res.get("review"),
             "review_history": res.get("review_history"), "datasheet": res.get("datasheet"),
             "datasheet_sha1": file_sha1(ds_path) if ds_path and os.path.exists(ds_path) else ""}
    with SAVE_LOCK:
        paths = save(gen, LIB_DIR, job.inp.lib_name, extra, ds_path)
    job.status = "saved"
    job.result["saved"] = paths
    job.say(f"라이브러리에 저장: {paths['symbol_lib']} / {paths['footprint']}")
    return paths


# ======================================================================= 폴더 일괄 생성
def library_index() -> dict[str, str]:
    """이미 라이브러리에 있는 데이터시트 SHA-1 → 부품 번호."""
    idx: dict[str, str] = {}
    for fn in glob.glob(os.path.join(LIB_DIR, "specs", "*.json")):
        try:
            with open(fn, encoding="utf-8") as f:
                j = json.load(f)
            if j.get("datasheet_sha1"):
                idx[j["datasheet_sha1"]] = j["spec"]["part_number"]
        except Exception:
            continue
    for fn in glob.glob(os.path.join(LIB_DIR, "datasheets", "*.pdf")):  # sha1 기록 전 저장분
        try:
            idx.setdefault(file_sha1(fn), os.path.splitext(os.path.basename(fn))[0])
        except OSError:
            continue
    return idx


def list_dirs(path: str) -> dict:
    """폴더 선택용: 하위 폴더 목록과 PDF 개수. path 가 비면 드라이브 · 홈 목록."""
    if not path:
        if os.name == "nt":
            import ctypes  # 드라이브 접근 없이 비트마스크로 목록 (네트워크 드라이브 지연 방지)
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            roots = [f"{d}:\\" for i, d in enumerate(string.ascii_uppercase) if mask >> i & 1]
        else:
            roots = ["/"]
        return {"path": "", "parent": None, "dirs": roots + [os.path.expanduser("~")], "pdfs": 0}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError(f"폴더가 없습니다: {path}")
    dirs, pdfs = [], 0
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            try:
                if e.is_dir() and not e.name.startswith((".", "$")):
                    dirs.append(e.path)
                elif e.is_file() and e.name.lower().endswith(".pdf"):
                    pdfs += 1
            except OSError:
                continue
    except PermissionError:
        pass
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else "", "dirs": dirs, "pdfs": pdfs}


def _pdfs(folder: str, recursive: bool) -> list[str]:
    out = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$"))]
            out += [os.path.join(root, f) for f in files if f.lower().endswith(".pdf")]
    else:
        out = [e.path for e in os.scandir(folder) if e.is_file() and e.name.lower().endswith(".pdf")]
    return sorted(out, key=str.lower)


def scan_folder(folder: str, recursive: bool = False) -> list[dict]:
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError(f"폴더가 없습니다: {folder}")
    idx = library_index()
    out = []
    for p in _pdfs(folder, recursive):
        try:
            sha = file_sha1(p)
        except OSError:
            continue
        out.append({"path": p, "name": os.path.relpath(p, folder), "size": os.path.getsize(p),
                    "sha1": sha, "in_library": sha in idx, "part": idx.get(sha, "")})
    return out


@dataclass
class BatchItem:
    path: str
    name: str
    sha1: str = ""
    job_id: str = ""
    status: str = "pending"   # pending / skipped / stopped / (작업 상태: running, saved, passed, needs_review, failed)
    note: str = ""


@dataclass
class Batch:
    id: str
    folder: str
    items: list[BatchItem]
    lib_name: str = "AutoParts"
    ai_review: bool = True
    auto_save: bool = True
    parallel: int = 1
    skip_existing: bool = True
    status: str = "running"   # running / stopping / stopped / done
    created: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    finished: str = ""

    def to_dict(self):
        items = []
        for it in self.items:
            j = JOBS.get(it.job_id)
            spec = (j.result.get("spec") if j else None) or {}
            items.append({"name": it.name, "path": it.path, "job_id": it.job_id,
                          "status": j.status if j else it.status, "stage": j.stage if j else "",
                          "note": it.note or (j.error if j else ""), "part": spec.get("part_number", ""),
                          "package": (spec.get("package") or {}).get("name", "")})
        counts: dict[str, int] = {}
        for i in items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        return {"id": self.id, "folder": self.folder, "status": self.status, "created": self.created,
                "finished": self.finished, "lib_name": self.lib_name, "parallel": self.parallel,
                "ai_review": self.ai_review, "auto_save": self.auto_save, "counts": counts, "items": items}


BATCHES: dict[str, Batch] = {}


def start_batch(folder: str, files: list[str], *, lib_name: str, ai_review: bool, auto_save: bool,
                parallel: int = 1, skip_existing: bool = True) -> Batch:
    folder = os.path.abspath(folder)
    items = []
    for p in files:
        p = os.path.abspath(p)
        if not p.lower().endswith(".pdf") or not os.path.isfile(p):
            raise ValueError(f"PDF 파일이 아닙니다: {p}")
        if os.path.commonpath([folder, p]) != folder:
            raise ValueError(f"지정한 폴더 밖의 파일입니다: {p}")
        items.append(BatchItem(path=p, name=os.path.relpath(p, folder)))
    if not items:
        raise ValueError("처리할 PDF 가 없습니다")
    b = Batch(id=uuid.uuid4().hex[:10], folder=folder, items=items, lib_name=lib_name, ai_review=ai_review,
              auto_save=auto_save, parallel=max(1, min(int(parallel), 4)), skip_existing=skip_existing)
    BATCHES[b.id] = b
    threading.Thread(target=_run_batch, args=(b,), daemon=True).start()
    return b


def _run_batch(b: Batch):
    q: queue.Queue = queue.Queue()
    idx = library_index() if b.skip_existing else {}
    for it in b.items:
        q.put(it)

    def worker():
        while True:
            try:
                it = q.get_nowait()
            except queue.Empty:
                return
            if b.status != "running":
                it.status = "stopped"
                continue
            try:
                it.sha1 = file_sha1(it.path)
            except OSError as e:
                it.status, it.note = "failed", str(e)
                continue
            if b.skip_existing and it.sha1 in idx:
                it.status, it.note = "skipped", f"이미 라이브러리에 있음 ({idx[it.sha1]})"
                continue
            job = Job(id=uuid.uuid4().hex[:12], inp=JobInput(
                datasheet_path=it.path, filename_hint=os.path.basename(it.path), lib_name=b.lib_name,
                ai_review=b.ai_review, auto_save=b.auto_save, batch_id=b.id))
            job.say(f"[폴더 일괄 {b.id}] {it.name}")
            JOBS[job.id] = job
            it.job_id = job.id
            it.status = "running"
            _run(job)
            it.status = job.status
            if job.status == "saved":
                idx[it.sha1] = (job.result.get("spec") or {}).get("part_number", it.name)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(b.parallel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    b.status = "stopped" if b.status == "stopping" else "done"
    b.finished = dt.datetime.now().isoformat(timespec="seconds")


def stop_batch(batch_id: str) -> Batch:
    b = BATCHES[batch_id]
    if b.status == "running":
        b.status = "stopping"  # 진행 중인 파일은 끝까지 처리하고, 남은 파일은 건너뜀
    return b
