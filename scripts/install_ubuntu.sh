#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is missing."
  echo "Run: sudo apt update && sudo apt install -y python3 python3-venv python3-pip git ca-certificates tzdata"
  exit 1
fi

python3 - <<'PY'
import sys

if sys.version_info < (3, 9):
    version = ".".join(map(str, sys.version_info[:3]))
    raise SystemExit(f"Python >= 3.9 is required, found {version}.")
PY

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is missing."
  echo "Run: sudo apt update && sudo apt install -y python3-venv"
  exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python -m pip install "qrcode>=7.4" || echo "Optional qrcode install failed; login will print a QR URL instead."

mkdir -p data
if [ ! -f .env ]; then
  cp .env.example .env
fi

cat <<'EOF'

Install complete.

Next steps:
1. Edit .env and set OPENAI_API_KEY / OPENAI_BASE_URL.
2. Run: source .venv/bin/activate
3. Run: python -m wechat_remind doctor --skip-api
4. Run: python -m wechat_remind login
5. Run: python -m wechat_remind run

EOF
