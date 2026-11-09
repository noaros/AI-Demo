#!/bin/sh
# azd postprovision hook: azd passes its environment values (endpoints etc.) as env vars.
set -e
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet -r requirements.txt
.venv/bin/python -m scripts.ingest
