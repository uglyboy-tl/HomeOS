#!/usr/bin/env bash
# 本机开发：把 web 起成后台进程（容器里用 docker/entrypoint.sh）。
# agent 内核是 pi，不用常驻进程：每个请求自己拉子进程，这里只准备它的 agent 目录。
#   ./scripts/dev.sh up|down|restart|status|log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT=8080
LOG_DIR="${HOMEOS_LOG_DIR:-/tmp/homeos-dev}"
DATA_DIR="$ROOT/.devdata"
DEV_CONFIG="$DATA_DIR/config"
PI_HOME="$DATA_DIR/pi"
LOG="$LOG_DIR/web.log"
PID_FILE="$LOG_DIR/web.pid"

warn() { echo "提示: $*" >&2; }

# .env 是 shell 赋值：值含空格要自己加引号，否则会被拆词
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env" || warn "读 $ROOT/.env 出错，检查里面的赋值"
  set +a
fi
export HOMEOS_DEV="${HOMEOS_DEV:=1}" # 想验生产形态的认证：HOMEOS_DEV=0 ./scripts/dev.sh up

# 配置放 .devdata（仓库 config/ 只是模板）。补文件交给 prepare.py，别在 shell 里再写一遍
mkdir -p "$LOG_DIR"
python3 "$ROOT/docker/prepare.py" "$DEV_CONFIG" "$ROOT/config"
# DEV=1 时不认证，留着这份令牌反而让人以为要带 header；验认证的路径自己再生成
[ "$HOMEOS_DEV" = 1 ] && rm -f "$DEV_CONFIG/server.token"
export HOMEOS_CONFIG_DIR="$DEV_CONFIG"

# cc-safety-net 只从 cwd 的项目目录读策略：容器里由 Dockerfile 烤一份，本机从仓库拷一份
mkdir -p "$ROOT/web/.cc-safety-net"
cp "$ROOT/config/cc-safety-net-policy.json" "$ROOT/web/.cc-safety-net/policy.json"

# pi 的 agent 目录：models/settings 软链到 config/，改配置不用再拷；运行时文件落在这里
mkdir -p "$PI_HOME" "$DATA_DIR/sessions"
ln -sfn "$DEV_CONFIG/pi-models.json" "$PI_HOME/models.json"
ln -sfn "$DEV_CONFIG/pi-settings.json" "$PI_HOME/settings.json"
export HOMEOS_PI_BIN="${HOMEOS_PI_BIN:-$(command -v pi || echo pi)}"
export HOMEOS_PI_AGENT_DIR="$PI_HOME"
export HOMEOS_PI_SESSION_DIR="$DATA_DIR/sessions" # 删掉它 = 全新会话
# 米家登录态走真实 HOME：本机直接复用 ~/.config/mijia-api（容器里 HOME=/data，挂载点在 compose 里）

# 扩展用本机 pi 自己装的那份；没装就不带，只影响本机（危险命令与密钥文件不再被拦）
SAFETY="$HOME/.config/pi/npm/node_modules/cc-safety-net/dist/pi/index.js"
if [ -f "$SAFETY" ]; then
  export HOMEOS_PI_EXTENSIONS="$SAFETY"
else
  export HOMEOS_PI_EXTENSIONS=""
  warn "本机没装 cc-safety-net，dev 环境不拦危险命令与密钥文件"
fi
command -v "$HOMEOS_PI_BIN" >/dev/null || warn "找不到 pi，/api/agent/* 会 503"

# 模型 key 在 config/pi-models.json（写死或 ${OPENCODE_GO_API_KEY} 插值），没配只提醒
model_key_ready() {
  [ -n "${OPENCODE_GO_API_KEY:-}" ] && return 0
  [ -f "$DEV_CONFIG/pi-models.json" ] || return 1
  python3 - "$DEV_CONFIG/pi-models.json" <<'PY'
import json, sys
providers = json.load(open(sys.argv[1])).get("providers", {})
key = next(iter(providers.values()), {}).get("apiKey", "")
sys.exit(0 if key and not key.startswith("${") else 1)
PY
}
model_key_ready || warn "模型 key 没配（pi-models.json 的 apiKey 或 .env 的 OPENCODE_GO_API_KEY），/api/agent/* 会报没 key"

# web 的状态码：000 = 没起来（连接被拒或超时）
probe() {
  curl -s -o /dev/null -m 1 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/" 2>/dev/null || true
}

wait_up() {
  for _ in $(seq 1 30); do
    [ "$(probe)" != "000" ] && return 0
    sleep 0.2
  done
  warn "$WEB_PORT 起来了但 6 秒内没应答"
}

# 只 kill 不等待的话，紧接着的启动会撞上 address already in use
wait_gone() {
  for _ in $(seq 1 25); do
    [ "$(probe)" = "000" ] && return 0
    sleep 0.2
  done
  warn "端口 $WEB_PORT 还没释放"
}

up() {
  if [ "$(probe)" != "000" ]; then
    echo "web 已在 $WEB_PORT 运行，复用"
  else
    # 后台子 shell 继承上面导出的环境；exec 让 $! 就是真正的进程
    (
      cd "$ROOT"
      exec uvicorn app.main:app --host 127.0.0.1 --port "$WEB_PORT" --log-level info \
        --reload --reload-dir "$ROOT/app" --reload-dir "$DEV_CONFIG" --reload-include='*.yaml'
    ) >"$LOG" 2>&1 &
    echo $! >"$PID_FILE"
    echo "web 启动: pid=$(cat "$PID_FILE") log=$LOG"
    wait_up
  fi
  echo "打开 http://127.0.0.1:$WEB_PORT/"
}

down() {
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  # reload 的服务进程是 spawn_main 子进程，上面那条模式打不上，按 venv 路径再兜一次
  pkill -f "$ROOT/.venv/bin/uvicorn" 2>/dev/null || true
  wait_gone
  echo 已停止
}

status() {
  echo "web:$(probe)        http://127.0.0.1:$WEB_PORT/"
  echo "pi: $(command -v "$HOMEOS_PI_BIN" || echo 未安装)  agent 目录: $HOMEOS_PI_AGENT_DIR"
  echo "config: $HOMEOS_CONFIG_DIR  DEV: $HOMEOS_DEV"
}

[ -x "$ROOT/.venv/bin/uvicorn" ] || uv sync --project "$ROOT" --quiet
export PATH="$ROOT/.venv/bin:$PATH"
cd "$ROOT"

case "${1:-up}" in
  up) up ;;
  down) down ;;
  restart) down; up ;;
  status) status ;;
  log) tail -n 40 "$LOG_DIR"/*.log 2>/dev/null ;;
  *) echo "用法: $0 up|down|restart|status|log"; exit 1 ;;
esac
