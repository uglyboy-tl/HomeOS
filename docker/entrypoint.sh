#!/usr/bin/env bash
# 容器前台入口：摆好环境 → 准备配置 → 起 web。agent（pi）由 web 按请求拉起子进程。
# 本机开发用 scripts/dev.sh，和这里无关。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR=/data

cd "$ROOT"
mkdir -p "$DATA_DIR/pi" "$DATA_DIR/sessions"
if [ ! -w "$ROOT/config" ]; then
  echo "$ROOT/config 不可写：compose 里要把 ./config 挂到 /app/config" >&2
  exit 1
fi

export HOME="$DATA_DIR"
export PI_CODING_AGENT_DIR="$DATA_DIR/pi"
export HOMEOS_PI_AGENT_DIR="$DATA_DIR/pi"
# cc-safety-net 构建期装好；运行时不联网、根目录只读
export HOMEOS_PI_EXTENSIONS="$ROOT/pi-ext/cc-safety-net/dist/pi/index.js"

python3 "$ROOT/docker/prepare.py" --token "$ROOT/config" "$ROOT/defaults"

# models/settings 直接指到挂载的 config/：改完点「新对话」即生效
ln -sfn "$ROOT/config/pi-models.json" "$DATA_DIR/pi/models.json"
ln -sfn "$ROOT/config/pi-settings.json" "$DATA_DIR/pi/settings.json"

RELOAD=()
if [ "${HOMEOS_DEV:-0}" = 1 ]; then
  # watchfiles 默认只认 .py，yaml 要显式 include
  RELOAD=(--reload --reload-dir "$ROOT/app" --reload-dir "$ROOT/config" --reload-include='*.yaml')
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --log-level info "${RELOAD[@]}"
