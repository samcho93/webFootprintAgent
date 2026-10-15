"""Claude Code CLI 백엔드: API 키 없이 로그인된 `claude` CLI 를 헤드리스(-p)로 실행한다.

PDF 문서는 임시 폴더에 파일로 저장해 CLI 의 Read 도구로 읽게 하고, 웹 검색은 WebSearch/WebFetch 도구를 쓴다.
결과는 --json-schema 구조화 출력(structured_output)으로 받는다.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable

TIMEOUT_S = int(os.environ.get("FP_AGENT_CLI_TIMEOUT", "1500"))


def find_claude() -> list[str] | None:
    """실행할 명령 [exe, (스크립트)] — .cmd shim 은 실제 실행 파일로 풀어 인자 따옴표 문제를 피한다."""
    env = os.environ.get("CLAUDE_CLI")
    if env and os.path.exists(env):
        return [env]
    # 셸과 같은 PATH 우선순위를 따른다 (~/.local/bin 의 오래된 설치본은 마지막)
    cands = [shutil.which("claude")]
    if os.name == "nt":
        cands += glob.glob(os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"))
    home = os.path.expanduser("~")
    cands += [os.path.join(home, ".local", "bin", "claude.exe"), os.path.join(home, ".local", "bin", "claude")]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        if c.lower().endswith(".cmd"):
            m = re.search(r'"%dp0%\\([^"]+\.(?:exe|js))"', open(c, encoding="utf-8", errors="ignore").read(), re.I)
            if m:
                target = os.path.join(os.path.dirname(c), m.group(1))
                if target.lower().endswith(".js") and shutil.which("node"):
                    return [shutil.which("node"), target]
                if os.path.exists(target):
                    return [target]
            continue
        return [c]
    return None


_INFO_CACHE: dict = {"at": 0.0, "info": None}


def cli_info(refresh: bool = False) -> dict:
    """{path, version, json_schema, logged_in, ok, message} — 다른 PC 에서 작업 전에 문제를 알리기 위한 점검 (5분 캐시)."""
    if not refresh and _INFO_CACHE["info"] and time.time() - _INFO_CACHE["at"] < 300:
        return _INFO_CACHE["info"]
    cmd = find_claude()
    info = {"path": cmd[-1] if cmd else None, "version": "", "json_schema": False, "logged_in": None, "ok": False,
            "message": "claude CLI 를 찾지 못했습니다 — `npm i -g @anthropic-ai/claude-code` 설치 후 터미널에서 `claude` 로 로그인하세요"}
    if cmd:
        def run(args, timeout=30):
            return subprocess.run(cmd + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            info["version"] = run(["--version"]).stdout.strip()
            info["json_schema"] = "--json-schema" in run(["--help"]).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            info["message"] = f"claude CLI 실행 실패: {e}"
        else:
            try:  # 오래된 CLI 에는 auth status 가 없을 수 있음 → 알 수 없음(None)
                r = run(["auth", "status", "--json"])
                if r.returncode == 0 and r.stdout.strip().startswith("{"):
                    info["logged_in"] = bool(json.loads(r.stdout).get("loggedIn"))
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                pass
            if not info["json_schema"]:
                info["message"] = (f"claude CLI {info['version']} 가 구조화 출력(--json-schema)을 지원하지 않습니다 — "
                                   "`npm i -g @anthropic-ai/claude-code` 로 업데이트하세요 (PATH 에서 먼저 찾는 claude 가 쓰입니다)")
            elif info["logged_in"] is False:
                info["message"] = "claude CLI 가 로그인되어 있지 않습니다 — 터미널에서 `claude` 를 실행해 로그인하세요"
            else:
                info.update(ok=True, message=f"Claude CLI {info['version'].split(' ')[0]}")
    _INFO_CACHE.update(at=time.time(), info=info)
    return info


def cli_version() -> str:
    cmd = find_claude()
    if not cmd:
        return ""
    try:
        return subprocess.run(cmd + ["--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30).stdout.strip()
    except Exception:
        return ""


def _summ(name: str, inp: dict) -> str:
    if name == "Read":
        p = os.path.basename(inp.get("file_path", ""))
        return f"{p} {('pages ' + str(inp['pages'])) if inp.get('pages') else ''}".strip()
    if name == "WebSearch":
        return inp.get("query", "")
    if name == "WebFetch":
        return inp.get("url", "")
    return json.dumps(inp, ensure_ascii=False)[:200]


class CLIError(Exception):
    pass


def run_structured(content: list[dict], schema: dict, *, system: str, model: str, effort: str,
                   web: bool, log: Callable[[str], None]) -> dict:
    info = cli_info()
    if not info["ok"]:  # 설치 · 버전 · 로그인 문제는 작업을 시작하기 전에 알린다
        raise CLIError(info["message"])
    cmd = find_claude()
    work = tempfile.mkdtemp(prefix="fpagent_")
    try:
        parts, n_doc = [], 0
        for block in content:
            if block.get("type") == "document":
                n_doc += 1
                path = os.path.join(work, f"datasheet{n_doc if n_doc > 1 else ''}.pdf")
                with open(path, "wb") as f:
                    f.write(base64.standard_b64decode(block["source"]["data"]))
                parts.append(f"The datasheet PDF is the file `{path}`. Read ALL of its pages with the Read tool "
                             "(use the pages parameter in chunks of at most 20 pages) and inspect the tables and "
                             "drawings carefully before answering.")
            elif block.get("type") == "text":
                parts.append(block["text"])
        prompt = "\n\n".join(parts) + "\n\nWhen you are done, return the final answer as the structured output."

        tools = (["Read"] if n_doc else []) + (["WebSearch", "WebFetch"] if web else [])
        args = cmd + ["-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
                      "--model", model, "--effort", effort, "--system-prompt", system,
                      "--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":"))]
        args += ["--tools", *tools] if tools else ["--tools", ""]
        if tools:
            args += ["--allowedTools", *tools]
        args += ["--add-dir", work]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}
        env.update({"NO_COLOR": "1", "FORCE_COLOR": "0"})

        log(f"  · Claude CLI 실행 (도구: {', '.join(tools) or '없음'})")
        p = subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        timer = threading.Timer(TIMEOUT_S, p.kill)
        timer.start()
        err_chunks: list[bytes] = []
        threading.Thread(target=lambda: err_chunks.append(p.stderr.read()), daemon=True).start()
        p.stdin.write(prompt.encode("utf-8"))
        p.stdin.close()
        result = None
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use" and c.get("name") != "StructuredOutput":
                        log(f"  · {c.get('name')}: {_summ(c.get('name', ''), c.get('input') or {})}")
            elif ev.get("type") == "result":
                result = ev
        p.wait()
        timer.cancel()
        if result is None:
            err = b"".join(err_chunks).decode("utf-8", "replace").strip()
            raise CLIError(f"Claude CLI 가 결과 없이 종료됨 (code {p.returncode}) {err[:600]}")
        log(f"  · CLI 완료: {result.get('num_turns')}턴, {round((result.get('duration_ms') or 0) / 1000)}초")
        if result.get("is_error") or result.get("structured_output") is None:
            msg = result.get("result") or result.get("subtype") or "알 수 없는 오류"
            if "login" in str(msg).lower() or "auth" in str(msg).lower():
                msg = f"{msg} — 터미널에서 `claude` 를 실행해 로그인하세요"
            raise CLIError(f"Claude CLI 오류: {str(msg)[:600]}")
        return result["structured_output"]
    finally:
        shutil.rmtree(work, ignore_errors=True)
