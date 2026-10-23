#!/bin/sh
set -eu
: "${BLACKBOX_PYTHON:?Set BLACKBOX_PYTHON to an existing Python 3.11+ executable}"
cd "$(dirname "$0")/.."
exec "$BLACKBOX_PYTHON" -m blackbox uninstall "$@"
