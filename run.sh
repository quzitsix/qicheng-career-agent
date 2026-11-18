#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print '请先按 README 安装 Python 环境与依赖。'
  exit 1
fi
exec .venv/bin/python -m uvicorn app.main:create_app --factory --host "${SRT_HOST:-0.0.0.0}" --port "${SRT_PORT:-8000}" --no-access-log
