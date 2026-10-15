"""CHANGELOG.md 를 고치고 사용법 페이지 반영(python tools/sync_changelog.py)을 잊지 않도록 확인."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_changelog_synced():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_changelog.py"), "--check"],
                       capture_output=True)
    assert r.returncode == 0, "CHANGELOG.md 가 사용법 페이지와 다릅니다 — python tools/sync_changelog.py 실행"
