"""로컬 브리지 실행: python run.py [--port 8766] [--no-open]"""
import argparse
import os
import threading
import webbrowser

import uvicorn

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
