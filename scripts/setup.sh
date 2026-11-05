#!/bin/sh
set -eu
# Bundle mode does not require a shared Python, even for first installation.
if [ "${1-}" = "--app-bundle" ]; then
    [ "$#" -ge 2 ] || exit 64
    app="$2"
    shift 2
    /usr/bin/codesign --verify --deep --strict "$app"
    exec "$app/Contents/Helpers/Blackbox Monitor.app/Contents/MacOS/BlackboxMonitor" install --app-bundle "$app" "$@"
fi
# Choose an existing Python >=3.11 explicitly; do not install/change global Python.
: "${BLACKBOX_PYTHON:?Set BLACKBOX_PYTHON to an existing Python 3.11+ executable}"
cd "$(dirname "$0")/.."
exec "$BLACKBOX_PYTHON" -m blackbox install "$@"
