#!/usr/bin/env bash
# HomeOS 的唯一启动入口：容器与本机开发共用一份，不再有第二个脚本。
#
#   容器里（无参数）：前台起两个进程，任一退出就整体退出，交给 restart policy
#   /app/docker/entrypoint.sh
#
#   本机开发（带子命令）：后台起、带 pid 文件与日志，可单独重启某一块
#   docker/entrypoint.sh up|down|restart [web|agent|all]|status|log
#
# 模式判定：无参数 = 容器前台；带子命令 = 本机托管。要强制可设 HOMEOS_MODE=container|dev。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT=8080
OC_PORT=14100
LOG_DIR="${HOMEOS_LOG_DIR:-/tmp/homeos-dev}"

# ---------- 模式与环境 ----------

mode() {
  if [ -n "${HOMEOS_MODE:-}" ]; then echo "$HOMEOS_MODE"; return; fi
  if [ "$#" -eq 0 ]; then echo container; else echo dev; fi
}

# opencode serve 与 web 之间的口令：没人需要手输，所以自动生成，落在卷里（重启不变）。
# 两边进程都从这个环境变量读，所以由 prepare 统一 export。
ensure_opencode_password() {
  local file="$HOMEOS_DATA_DIR/auth/opencode"
  if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    mkdir -p "$(dirname "$file")"
    if [ ! -s "$file" ]; then
      ( umask 077; head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$file" )
    fi
    OPENCODE_SERVER_PASSWORD="$(cat "$file")"
    export OPENCODE_SERVER_PASSWORD
  fi
}

# 两个模式走同一套环境：只有绑定地址、数据目录、uvicorn 是否 reload 不同
prepare() {
  local m="$1"
  # 本机跑时把仓库根的 .env 读进来（容器里没有这个文件：Dockerfile 只 COPY 代码，
  # 环境变量由 compose 注入）。它是 shell 赋值：值含空格/特殊字符要加引号，否则会被拆词。
  if [ -f "$ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env" || echo "读 $ROOT/.env 出错，检查里面的赋值" >&2
    set +a
  fi
  # 显式设了 HOMEOS_DEV 就以它为准：NAS 调试栈靠这个在容器里开热重载
  export HOMEOS_DEV="${HOMEOS_DEV:-$([ "$m" = dev ] && echo 1 || echo 0)}"
  if [ "$m" = container ]; then
    export HOMEOS_DATA_DIR="${HOMEOS_DATA_DIR:-/data}"
    export HOME=/data                       # 米家 token 落 /data/.config/mijia-api/auth.json
    # opencode 会往配置根里写 service.json，镜像根是只读的，所以整份配置复制到卷里再指过去。
    # 代价：改了 opencode/ 下的配置要重建镜像（每次启动都覆盖）。
    export XDG_CONFIG_HOME="$HOMEOS_DATA_DIR/config"
    mkdir -p "$XDG_CONFIG_HOME"
    cp -r "$ROOT/opencode" "$XDG_CONFIG_HOME/"
    export HOMEOS_WARM_MIJIA="${HOMEOS_WARM_MIJIA:-1}"   # 启动后预热米家登录与设备列表
    BIND_HOST=0.0.0.0
  else
    export HOMEOS_DATA_DIR="${HOMEOS_DATA_DIR:-$ROOT/.devdata}"
    export HOMEOS_WARM_MIJIA="${HOMEOS_WARM_MIJIA:-1}"
    export XDG_CONFIG_HOME="$ROOT"          # 本机直接用仓库里的 opencode/，改完即生效
    BIND_HOST=127.0.0.1
  fi
  # 热重载只盯 app/（前端改完刷新即见，不用重载）；容器里也认 HOMEOS_DEV=1
  if [ "$HOMEOS_DEV" = 1 ]; then
    RELOAD="--reload --reload-dir $ROOT/app"
  else
    RELOAD=""
  fi
  export XDG_DATA_HOME="$HOMEOS_DATA_DIR/opencode"   # session 与 snapshot
  export XDG_CACHE_HOME="$HOMEOS_DATA_DIR/cache"
  # 插件是构建时预装好的（镜像里的 /app/plugin-cache），运行时根目录只读、也不联网，
  # 所以每次启动复制进可写缓存；没有就跳过（旧镜像或本机模式）。
  if [ -d /app/plugin-cache ]; then
    mkdir -p "$XDG_CACHE_HOME"
    cp -r /app/plugin-cache/. "$XDG_CACHE_HOME"/
  fi
  export OPENCODE_DISABLE_PROJECT_CONFIG=1           # 不加载项目根 AGENTS.md，只用 opencode/AGENTS.md
  # 仓库/镜像里的 venv（uv sync 的产物）优先，两条路径同一个位置
  if [ -x "$ROOT/.venv/bin/uvicorn" ]; then
    export PATH="$ROOT/.venv/bin:$PATH"
  fi
  mkdir -p "$HOMEOS_DATA_DIR/opencode" "$HOMEOS_DATA_DIR/cache"
  ensure_opencode_password

  if [ "$m" = dev ]; then
    [ -x "$ROOT/.venv/bin/uvicorn" ] || uv sync --project "$ROOT" --quiet
    mkdir -p "$LOG_DIR"
    cd "$ROOT"
  fi
}

# opencode 二进制：容器里在 PATH 上；本机优先环境变量，最后借用旧 checkout 里钉死的那份
oc_bin() {
  local b
  for b in "${HOMEOS_OPENCODE_BIN:-}" "$(command -v opencode || true)" \
           "$ROOT/opencode/bin/opencode" "$HOME/Code/kiosk/host/bin/opencode"; do
    [ -n "$b" ] && [ -x "$b" ] && { echo "$b"; return 0; }
  done
  echo "找不到 opencode：装一份，或设 HOMEOS_OPENCODE_BIN=/path/to/opencode" >&2
  return 1
}

# ---------- 三个进程 ----------

cmd_web() {
  # RELOAD 是空或 --reload ...，故意不加引号
  # shellcheck disable=SC2086
  exec uvicorn app.main:app --host "$BIND_HOST" --port "$WEB_PORT" --log-level info $RELOAD
}

cmd_agent() {
  local bin
  bin="$(oc_bin)"
  cd "$ROOT/web"        # cwd = web/：agent 的权限与文件路径都相对这里
  exec "$bin" serve --hostname 127.0.0.1 --port "$OC_PORT"
}

# 单元表：名字|端口|停进程时用来认进程的命令行特征（三处各写一遍容易对不上）
UNITS=(
  "web|$WEB_PORT|uvicorn app.main:app"
  "agent|$OC_PORT|serve --hostname 127.0.0.1 --port $OC_PORT"
)

unit_field() {
  local line
  for line in "${UNITS[@]}"; do
    [ "${line%%|*}" = "$1" ] || continue
    echo "$line" | cut -d'|' -f"$2"
    return 0
  done
  return 1
}

# ---------- 容器模式：前台 ----------

run_foreground() {
  prepare container
  local pids=()
  cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
  trap cleanup TERM INT

  cmd_web & pids+=($!)
  cmd_agent & pids+=($!)

  wait -n || true
  echo "有一个进程退出了，整体退出交给 restart policy" >&2
  cleanup
  exit 1
}

# ---------- 本机模式：后台托管 ----------

# 起完等它真的应答，不然紧接着的 status / 请求会打到还没监听的端口上
wait_up() {
  local port="$1" path="${2:-/}"
  for _ in $(seq 1 30); do
    curl -s -o /dev/null -m 1 "http://127.0.0.1:$port$path" && return 0
    sleep 0.2
  done
  echo "警告: $port 起来了但 6 秒内没应答"
}

# 等端口真的不再应答：只 kill 不等待的话，紧接着的启动会撞上 address already in use
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
  local unit="$1" port log pid_file cmd
  port="$(unit_field "$unit" 2)"
  log="$LOG_DIR/$unit.log"
  pid_file="$LOG_DIR/$unit.pid"
  cmd="cmd_$unit"

  if curl -sf -o /dev/null -m 1 "http://127.0.0.1:$port/"; then
    echo "$unit 已在 $port 运行，复用"
    return 0
  fi
  # 后台子 shell 直接继承 prepare 导出的环境，cmd_* 里 exec，所以 $! 就是真正的进程
  ( cd "$ROOT" && "$cmd" ) >"$log" 2>&1 &
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
  pkill -f "$(unit_field "$unit" 3)" 2>/dev/null || true
  # uvicorn --reload 的服务进程是 multiprocessing 子进程（命令行是 spawn_main），
  # 打不上上一条模式，按 venv 路径再兜一次，避免"刚停就复用了残留端口"
  [ "$unit" = web ] && pkill -f "$ROOT/.venv/bin/uvicorn" 2>/dev/null || true
}

up() {
  local line
  for line in "${UNITS[@]}"; do up_one "${line%%|*}"; done
  echo "打开 http://127.0.0.1:$WEB_PORT/"
}

down() {
  local line
  # 全试一遍：pid 文件丢了也要能停干净
  for line in "${UNITS[@]}"; do stop_one "${line%%|*}"; done
  for line in "${UNITS[@]}"; do wait_gone "$(unit_field "${line%%|*}" 2)"; done
  echo 已停止
}

restart() {
  local what="${1:-all}"
  case "$what" in
    web|agent)
      stop_one "$what"; wait_gone "$(unit_field "$what" 2)"; up_one "$what" ;;
    all)
      down; up ;;
    *)
      echo "用法: $0 restart [web|agent|all]"; exit 1 ;;
  esac
}

status() {
  local pw code
  pw="$(cat "$HOMEOS_DATA_DIR/auth/opencode" 2>/dev/null || true)"
  code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/" || true)
  echo "web:$code        http://127.0.0.1:$WEB_PORT/"
  code=$(curl -s -o /dev/null -m 2 -u "opencode:$pw" -w '%{http_code}' "http://127.0.0.1:$OC_PORT/api/info" || true)
  echo "agent:$code      http://127.0.0.1:$OC_PORT/"
}

if [ "$(mode "$@")" = container ]; then
  run_foreground
else
  prepare dev
  case "${1:-up}" in
    up) up ;;
    down) down ;;
    restart) restart "${2:-all}" ;;
    status) status ;;
    log) tail -n 40 "$LOG_DIR"/*.log 2>/dev/null ;;
    *) echo "用法: $0 up|down|restart [web|agent|all]|status|log"; exit 1 ;;
  esac
fi
