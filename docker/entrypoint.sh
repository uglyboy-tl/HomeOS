#!/usr/bin/env bash
# 容器前台入口：摆好环境 → 准备配置与密钥 → 起 web 与 opencode，谁先退出就整体退出。
# 本机开发用 scripts/dev.sh（那边管 pid、日志、端口，和这里无关，别把开发逻辑搬回来）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PORT=8080
OC_PORT=14100
DATA_DIR=/data

cd "$ROOT"
if [ ! -w "$DATA_DIR" ]; then
  echo "/data 不可写：检查 compose 的 tmpfs 或 volumes" >&2
  exit 1
fi
mkdir -p "$DATA_DIR/config" "$DATA_DIR/opencode" "$DATA_DIR/cache"

export HOME="$DATA_DIR"
export XDG_CONFIG_HOME="$DATA_DIR/config"
# opencode 要往配置根写 service.json，镜像根只读；每次都从镜像那份重建，避免残留旧文件
rm -rf "$XDG_CONFIG_HOME/opencode"
cp -r "$ROOT/opencode" "$XDG_CONFIG_HOME/"
export XDG_DATA_HOME="$DATA_DIR/opencode" XDG_CACHE_HOME="$DATA_DIR/cache"
export HOMEOS_DEV="${HOMEOS_DEV:-0}"
export OPENCODE_DISABLE_PROJECT_CONFIG=1     # 不加载项目根 AGENTS.md，只用 opencode/AGENTS.md
# 插件构建时装在镜像里，运行时不联网、根目录只读，所以复制进可写缓存
if [ -d "$ROOT/plugin-cache" ]; then
  cp -r "$ROOT/plugin-cache/." "$XDG_CACHE_HOME"/
fi

# 空挂载目录补默认配置；token 首次生成后固定，opencode 密码每次启动重换。
# 密码要交给 opencode serve（它只认环境变量），所以这里 export。
if [ ! -w "$ROOT/config" ]; then
  echo "$ROOT/config 不可写：compose 里要把 ./config 挂到 /app/config" >&2
else
  # prepare.py 按固定顺序打印三行：opencode 密码、模型 key、搜索 key
  if ! prepared="$(python3 "$ROOT/docker/prepare.py" "$ROOT/config" "$ROOT/defaults")"; then
    echo "准备配置失败：$ROOT/config 里的 yaml 解析不了？" >&2
    exit 1
  fi
  # 命令替换会吃掉末尾空行，所以缺行不算错（否则 set -e 直接退出）
  { read -r pw; read -r go_key; read -r exa_key; } <<<"$prepared" || true
  export OPENCODE_SERVER_PASSWORD="$pw"
  # 环境变量优先（.env / compose）；没设才用 yaml 里的
  export OPENCODE_GO_API_KEY="${OPENCODE_GO_API_KEY:-$go_key}"
  export EXA_API_KEY="${EXA_API_KEY:-$exa_key}"
fi

RELOAD=()
if [ "$HOMEOS_DEV" = 1 ]; then
  # watchfiles 默认只认 .py，yaml 要显式 include；改配置 = 进程重启后重读
  RELOAD=(--reload --reload-dir "$ROOT/app" --reload-dir "$ROOT/config" --reload-include='*.yaml')
fi

start_web() {
  exec uvicorn app.main:app --host 0.0.0.0 --port "$WEB_PORT" --log-level info \
    --log-config "$ROOT/docker/logging.json" "${RELOAD[@]}"
}
start_agent() { cd "$ROOT/web" && exec opencode serve --hostname 127.0.0.1 --port "$OC_PORT"; }

pids=()
names=()
stop_children() {
  kill -TERM "${pids[@]}" 2>/dev/null || true
  wait "${pids[@]}" 2>/dev/null || true
}
# 收到停止信号：转发给孩子并等它们退完，这是正常退出
trap 'stop_children; exit 0' TERM INT

start_web & pids+=($!); names+=(web)
start_agent & pids+=($!); names+=(agent)

# wait -n -p 给的是 pid，换成名字才看得出是谁挂了
exited=""
wait -n -p exited || true
for i in "${!pids[@]}"; do
  [ "${pids[$i]}" = "$exited" ] && died="${names[$i]}"
done
echo "进程 ${died:-未知} 退出了，整体退出交给 restart policy" >&2
stop_children
exit 1
