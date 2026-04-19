#!/usr/bin/env sh
set -eu

if command -v python3 >/dev/null 2>&1; then
  exec python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py install
fi

if command -v python >/dev/null 2>&1; then
  exec python plugins/eqemu-oracle/scripts/eqemu_oracle.py install
fi

echo "Python 3 was not found. Install Python 3 and rerun ./install.sh." >&2
exit 1
