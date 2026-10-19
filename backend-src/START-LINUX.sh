#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r requirements-web.txt
exec .venv/bin/python free_start.py
