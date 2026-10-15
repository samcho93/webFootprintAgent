"""로컬 브리지 실행: python run.py [--port 8766] [--no-open]"""
import argparse
import importlib.util
import os
import sys
import threading
import webbrowser

# 다른 PC 에서 흔한 문제: 오래된 Python, 다른 Python 에 설치된 패키지
if sys.version_info < (3, 10):
    sys.exit(f"Python 3.10 이상이 필요합니다 (지금: {sys.version.split()[0]}, {sys.executable}).\n"
             "  python.org 에서 최신 Python 을 설치하거나, Windows 에서는 `py -3 run.py` 로 실행하세요.")
_missing = [m for m in ("fastapi", "uvicorn", "anthropic", "fitz", "pydantic", "multipart")
            if importlib.util.find_spec(m) is None]
if _missing:
    sys.exit(f"필요한 패키지가 없습니다: {', '.join(_missing)}\n"
             f"  \"{sys.executable}\" -m pip install -r requirements.txt   (이 Python 에 설치해야 합니다)")

import uvicorn  # noqa: E402

p = argparse.ArgumentParser(description="KiCad Footprint Agent 로컬 브리지")
p.add_argument("--port", type=int, default=int(os.environ.get("FP_AGENT_PORT", "8766")))
p.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 끔")
a = p.parse_args()

url = f"http://127.0.0.1:{a.port}/"
print(f"KiCad Footprint Agent 브리지: {url}")
print("  웹 화면: 위 주소 또는 https://irodmas.com/webfootprintagent/")
if not a.no_open:
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
uvicorn.run("app.main:app", host="127.0.0.1", port=a.port, log_level="info")
