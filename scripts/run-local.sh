#!/usr/bin/env sh
set -eu
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
exec python run_web_ui.py
