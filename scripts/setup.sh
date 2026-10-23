#!/bin/sh
set -eu
# Choose an existing Python >=3.11 explicitly; do not install/change global Python.
: "${BLACKBOX_PYTHON:?Set BLACKBOX_PYTHON to an existing Python 3.11+ executable}"
cd "$(dirname "$0")/.."
exec "$BLACKBOX_PYTHON" -m blackbox install "$@"
