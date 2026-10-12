#!/usr/bin/env bash
# One-time bootstrap for Cursor Remote SSH dev on Ubuntu.
# Run from repo root: bash scripts/remote-dev-setup.sh
#
# Default is free-threaded 3.14t — with FastUploadWriter + 4 MiB HTTP chunks it
# often beats GIL 3.14 on LAN stream uploads. Override with e.g.:
#   PYTHON_VERSION=3.13 bash scripts/remote-dev-setup.sh
set -euo pipefail

PY="${PYTHON_VERSION:-3.14t}"

command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }

echo "==> uv python install $PY"
uv python install "$PY"

want_major_minor="$(echo "$PY" | sed -E 's/^([0-9]+\.[0-9]+).*/\1/')"
if [ -d .venv ]; then
  if ! .venv/bin/python -c "import sys; assert f'{sys.version_info[0]}.{sys.version_info[1]}' == '$want_major_minor'" 2>/dev/null; then
    echo "==> removing .venv (not Python $want_major_minor)"
    rm -rf .venv
  fi
fi

if [ ! -x .venv/bin/python ]; then
  echo "==> uv venv --python $PY"
  uv venv --python "$PY"
fi

echo "==> uv pip install -e ."
uv pip install -e .

echo "==> verify"
.venv/bin/python -c 'import aird,sys,os; p=os.path.dirname(aird.__file__); assert os.path.isfile(os.path.join(p,"static/img/logo-icon.png")); gil=getattr(sys,"_is_gil_enabled",lambda:True)(); print("ok:", p, "python", sys.version.split()[0], "gil", gil)'

echo ""
echo "Done. In Cursor: select interpreter -> $(pwd)/.venv/bin/python"
echo "Run app: .venv/bin/aird   (or restart systemd for production)"
echo "Tip: measure upload MB/s on this host; 3.14t is often fastest for WireGuard stream."
