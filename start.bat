@echo off
rem KiCad Footprint Agent local bridge: find Python 3.10+, install packages, run.
rem Keep this file ASCII-only (cmd misreads UTF-8 lines); Korean messages come from run.py.
cd /d "%~dp0"
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and run again.
  pause
  exit /b 1
)
echo Python: %PY%
%PY% -m pip install -q -r requirements.txt
%PY% run.py %*
pause
