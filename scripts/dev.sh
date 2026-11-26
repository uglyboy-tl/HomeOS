#!/usr/bin/env bash
# 本机开发：把 web 与 opencode 起成后台进程（容器不用这个，那边是 docker/entrypoint.sh）。
#   ./scripts/dev.sh up|down|restart [web|agent|all]|status|log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT=8080
OC_PORT=14100
LOG_DIR="${HOMEOS_LOG_DIR:-/tmp/homeos-dev}"
DATA_DIR="$ROOT/.devdata"

port_of() { case "$1" in web) echo "$WEB_PORT" ;; agent) echo "$OC_PORT" ;; esac; }
# 停进程用的命令行特征：pid 文件丢了也能停干净
pattern_of() {
  case "$1" in
    web) echo "uvicorn app.main:app" ;;
    agent) echo "serve --hostname 127.0.0.1 --port $OC_PORT" ;;
  esac
}
cmd_of() { echo "cmd_$1"; }

# .env 是 shell 赋值：值含空格/特殊字符要自己加引号，否则会被拆词
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env" || echo "读 $ROOT/.env 出错，检查里面的赋值" >&2
  set +a
fi

export HOMEOS_DEV="${HOMEOS_DEV:-1}"
# 配置放 .devdata：仓库 config/ 只是模板，真值不进 git
DEV_CONFIG="$DATA_DIR/config"
mkdir -p "$DEV_CONFIG"
for f in "$ROOT"/config/*.yaml; do
  if [ ! -e "$DEV_CONFIG/$(basename "$f")" ]; then
    cp "$f" "$DEV_CONFIG/"
    echo "已从模板复制 $(basename "$f") 到 $DEV_CONFIG（填服务地址/token）"
  fi
done
export HOMEOS_CONFIG_DIR="$DEV_CONFIG"
export XDG_CONFIG_HOME="$ROOT"        # 本机直接用仓库里的 opencode/，改完即生效
export XDG_DATA_HOME="$DATA_DIR/opencode"
export XDG_CACHE_HOME="$DATA_DIR/cache"
export OPENCODE_DISABLE_PROJECT_CONFIG=1

# opencode 二进制：本机装了就在 PATH 上，也可以用 HOMEOS_OPENCODE_BIN 指定
oc_bin() {
  local b
  for b in "${HOMEOS_OPENCODE_BIN:-}" "$(command -v opencode || true)"; do
    [ -n "$b" ] && [ -x "$b" ] && { echo "$b"; return 0; }
  done
  echo "找不到 opencode：装一份，或设 HOMEOS_OPENCODE_BIN=/path/to/opencode" >&2
  return 1
}

cmd_web() {
  exec uvicorn app.main:app --host 127.0.0.1 --port "$WEB_PORT" --log-level info \
    --reload --reload-dir "$ROOT/app" --reload-dir "$DEV_CONFIG" --reload-include='*.yaml'
}

cmd_agent() {
  local bin
  bin="$(oc_bin)" || exit 1
  exec "$bin" serve --hostname 127.0.0.1 --port "$OC_PORT"
}

mkdir -p "$DATA_DIR/opencode" "$DATA_DIR/cache" "$LOG_DIR"
[ -x "$ROOT/.venv/bin/uvicorn" ] || uv sync --project "$ROOT" --quiet
export PATH="$ROOT/.venv/bin:$PATH"
cd "$ROOT"

wait_up() {
  local port="$1"
  for _ in $(seq 1 30); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:$port/" && return 0
    sleep 0.2
  done
  echo "警告: $port 起来了但 6 秒内没应答"
}

# 只 kill 不等待的话，紧接着的启动会撞上 address already in use
wait_gone() {
  local port="$1" code
  for _ in $(seq 1 25); do
    code=$(curl -s -o /dev/null -m 1 -w '%{http_code}' "http://127.0.0.1:$port/" 2>/dev/null || true)
    [ "${code:-000}" = "000" ] && return 0
    sleep 0.2
  done
  echo "警告: 端口 $port 还没释放"
}

up_one() {
  local unit="$1" port log pid_file
  port="$(port_of "$unit")"
  log="$LOG_DIR/$unit.log"
  pid_file="$LOG_DIR/$unit.pid"

  if curl -sf -o /dev/null -m 1 "http://127.0.0.1:$port/"; then
    echo "$unit 已在 $port 运行，复用"
    return 0
  fi
  # 后台子 shell 继承上面导出的环境；cmd_* 里 exec，所以 $! 就是真正的进程
  ( cd "$ROOT" && "$(cmd_of "$unit")" ) >"$log" 2>&1 &
  echo $! >"$pid_file"
  echo "$unit 启动: pid=$(cat "$pid_file") log=$log"
  wait_up "$port"
}

stop_one() {
  local unit="$1" pid_file
  pid_file="$LOG_DIR/$unit.pid"
  if [ -f "$pid_file" ]; then
    kill "$(cat "$pid_file")" 2>/dev/null && echo "已停 $unit (pid=$(cat "$pid_file"))" || true
    rm -f "$pid_file"
  fi
  pkill -f "$(pattern_of "$unit")" 2>/dev/null || true
  # reload 的服务进程是 spawn_main 子进程，上面那条模式打不上，按 venv 路径再兜一次
  [ "$unit" = web ] && pkill -f "$ROOT/.venv/bin/uvicorn" 2>/dev/null || true
  return 0
}

up() {
  up_one web
  # 本机大多没装 opencode：别白等 6 秒再报一个警告
  if oc_bin >/dev/null 2>&1; then
    up_one agent
  else
    echo "跳过 agent：本机没装 opencode（装一份或设 HOMEOS_OPENCODE_BIN）"
  fi
  echo "打开 http://127.0.0.1:$WEB_PORT/"
}

down() {
  # 全都试一遍：pid 文件丢了也要能停干净
  stop_one web
  stop_one agent
  wait_gone "$WEB_PORT"
  wait_gone "$OC_PORT"
  echo 已停止
}

restart() {
  case "${1:-all}" in
    web | agent)
      stop_one "$1"; wait_gone "$(port_of "$1")"; up_one "$1" ;;
    all) down; up ;;
    *) echo "用法: $0 restart [web|agent|all]"; exit 1 ;;
  esac
}

status() {
  local pw code
  pw="$(python3 -c "import sys,yaml;print((yaml.safe_load(open(sys.argv[1])) or {}).get('opencode',{}).get('serve_password',''))" "$DEV_CONFIG/homeos.yaml" 2>/dev/null || true)"
  code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/" || true)
  echo "web:$code        http://127.0.0.1:$WEB_PORT/"
  code=$(curl -s -o /dev/null -m 2 -u "opencode:$pw" -w '%{http_code}' "http://127.0.0.1:$OC_PORT/api/info" || true)
  echo "agent:$code      http://127.0.0.1:$OC_PORT/"
}

case "${1:-up}" in
  up) up ;;
  down) down ;;
  restart) restart "${2:-all}" ;;
  status) status ;;
  log) tail -n 40 "$LOG_DIR"/*.log 2>/dev/null ;;
  *) echo "用法: $0 up|down|restart [web|agent|all]|status|log"; exit 1 ;;
esac
