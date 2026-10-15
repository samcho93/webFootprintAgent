"""FastAPI 로컬 브리지 서버.

웹 화면은 이 서버(http://127.0.0.1:8766)나 irodmas.com/webfootprintagent/ 에서 열 수 있다.
irodmas.com 화면은 이 PC 의 브리지로 요청하므로, 허용된 Origin 만 받는다 (KiCad · Claude CLI 를 이 PC 에서 실행하기 때문).
실행: python run.py   (또는 start.bat / run.ps1)
"""
from __future__ import annotations

import os
import re
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .env import load_env

load_env()  # .agents.llm 이 모델/키를 읽기 전에 .env 반영

from .agents.claude_cli import find_claude  # noqa: E402
from .agents.llm import MODEL, backend  # noqa: E402
from .kicad.library import list_library
from .kicad.verify import find_kicad_cli
from .models import PartSpec
from .pipeline import JOBS, LIB_DIR, ROOT, WORK_DIR, JobInput, do_save, evaluate, start_job

app = FastAPI(title="KiCad Footprint Agent")
STATIC = os.path.join(ROOT, "static")

# 이 브리지를 사용할 수 있는 웹 화면 주소
ORIGINS = {"https://irodmas.com", "https://www.irodmas.com", "https://samcho93.github.io",
           *[o.strip() for o in os.environ.get("FP_AGENT_ORIGINS", "").split(",") if o.strip()]}
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


@app.middleware("http")
async def bridge_guard(request: Request, call_next):
    # DNS 리바인딩 방지: 로컬 주소로 들어온 요청만 처리
    if not LOCAL_HOST.match(request.headers.get("host", "")):
        return JSONResponse({"detail": "로컬 주소(127.0.0.1)로만 접속할 수 있습니다"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in ORIGINS and not LOCAL_ORIGIN.match(origin):
        return JSONResponse({"detail": f"허용되지 않은 웹 주소: {origin}"}, status_code=403)
    resp = Response(status_code=204) if request.method == "OPTIONS" and origin else await call_next(request)
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"  # 공개 사이트 → 로컬 기기 접근(Chrome PNA)
    return resp


def _bool(v: str | None) -> bool:
    return str(v).lower() in ("1", "true", "on", "yes")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/status")
def status():
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return {"model": MODEL, "api_key": has_key, "backend": backend(), "claude_cli": bool(find_claude()),
            "kicad_cli": find_kicad_cli(), "library_dir": LIB_DIR}


@app.post("/api/jobs")
async def create_job(
    part_number: str = Form(""),
    manufacturer: str = Form(""),
    datasheet_url: str = Form(""),
    package_hint: str = Form(""),
    lib_name: str = Form("AutoParts"),
    auto_save: str = Form("true"),
    ai_review: str = Form("true"),
    file: UploadFile | None = File(None),
):
    path = ""
    if file is not None and file.filename:
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "PDF 파일만 업로드할 수 있습니다")
        os.makedirs(os.path.join(WORK_DIR, "uploads"), exist_ok=True)
        path = os.path.join(WORK_DIR, "uploads", uuid.uuid4().hex + ".pdf")
        with open(path, "wb") as f:
            f.write(data)
    if not (part_number.strip() or datasheet_url.strip() or path):
        raise HTTPException(400, "부품 번호, 데이터시트 링크, PDF 파일 중 하나를 입력하세요")
    job = start_job(JobInput(part_number=part_number.strip(), manufacturer=manufacturer.strip(),
                             datasheet_url=datasheet_url.strip(), datasheet_path=path,
                             package_hint=package_hint.strip(), lib_name=_lib(lib_name),
                             auto_save=_bool(auto_save), ai_review=_bool(ai_review)))
    return {"id": job.id}


def _lib(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()) or "AutoParts"


class SpecJob(BaseModel):
    spec: dict
    lib_name: str = "AutoParts"
    ai_review: bool = False
    auto_save: bool = False
    from_job: str | None = None


def _parse_spec(d: dict) -> PartSpec:
    try:
        return PartSpec.model_validate(d)
    except ValidationError as e:
        raise HTTPException(422, f"사양 JSON 오류: {e}")


@app.post("/api/jobs/spec")
def create_spec_job(req: SpecJob):
    spec = _parse_spec(req.spec)
    inp = JobInput(part_number=spec.part_number, lib_name=_lib(req.lib_name), ai_review=req.ai_review,
                   auto_save=req.auto_save, spec=spec, datasheet_url=spec.datasheet_url)
    if req.from_job and req.from_job in JOBS:  # 이전 작업의 데이터시트 재사용
        prev = JOBS[req.from_job]
        ds = (prev.result.get("datasheet") or {})
        inp.datasheet_path = ds.get("path") or prev.inp.datasheet_path
        inp.datasheet_url = inp.datasheet_url or ds.get("url") or ""
    return {"id": start_job(inp).id}


@app.post("/api/preview")
def preview(req: SpecJob):
    """AI 없이 즉시 생성 + 규칙/KiCad 검증 (편집기 미리보기용)."""
    spec = _parse_spec(req.spec)
    try:
        _, res = evaluate(spec, _lib(req.lib_name))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return res


@app.get("/api/jobs")
def jobs():
    return [{"id": j.id, "status": j.status, "part": (j.result.get("spec") or {}).get("part_number") or j.inp.part_number,
             "created": j.created} for j in sorted(JOBS.values(), key=lambda j: j.created, reverse=True)]


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "작업 없음")
    return JOBS[job_id].to_dict()


@app.post("/api/jobs/{job_id}/save")
def save_job(job_id: str, force: bool = False):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "작업 없음")
    if j.status not in ("passed", "needs_review", "saved"):
        raise HTTPException(400, f"저장할 수 없는 상태: {j.status}")
    if j.status == "needs_review" and not force:
        raise HTTPException(400, "검증 미통과 — 확인 후 강제 저장(force)하세요")
    try:
        return do_save(j, force=force)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    j = JOBS.get(job_id)
    if not j or not j.result.get("files"):
        raise HTTPException(404, "결과 없음")
    if kind == "symbol":
        return PlainTextResponse(j.result["files"]["symbol"], headers={
            "Content-Disposition": f'attachment; filename="{j.result["symbol_name"]}.kicad_sym"'})
    if kind == "footprint":
        return PlainTextResponse(j.result["files"]["footprint"], headers={
            "Content-Disposition": f'attachment; filename="{j.result["footprint_name"]}.kicad_mod"'})
    raise HTTPException(404)


@app.get("/api/library")
def library():
    return {"dir": LIB_DIR, "parts": list_library(LIB_DIR)}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
