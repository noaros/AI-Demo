#!/bin/sh
# Runs a script from this folder in the project's virtual environment: ./scripts/run.sh ingest
# azd hooks use it; azd passes its environment values (endpoints etc.) as env vars.
set -e
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt
.venv/bin/python -m "scripts.$1"
