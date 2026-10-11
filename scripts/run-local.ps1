$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path '.venv\Scripts\python.exe')) { py -m venv .venv }
# Core runtime has no third-party dependencies. Invoke Python directly; never call pip.exe.
& .\.venv\Scripts\python.exe run_web_ui.py
