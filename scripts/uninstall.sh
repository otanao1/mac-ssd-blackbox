#!/bin/sh
set -eu
if [ -n "${BLACKBOX_CLI-}" ]; then
    exec "$BLACKBOX_CLI" uninstall "$@"
fi
: "${BLACKBOX_PYTHON:?Set BLACKBOX_PYTHON to an existing Python 3.11+ executable}"
cd "$(dirname "$0")/.."
exec "$BLACKBOX_PYTHON" -m blackbox uninstall "$@"
