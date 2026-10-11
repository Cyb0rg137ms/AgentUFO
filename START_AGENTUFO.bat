@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Could not create .venv. Install Python 3.10+ and ensure the py launcher is available.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" run_web_ui.py
if errorlevel 1 (
  echo AgentUFO exited with an error. Keep this window open to read the message.
  pause
)
endlocal
