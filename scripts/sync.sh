#!/usr/bin/env bash
# 本地 → NAS：推容器挂载的两份代码（app/ 与 web/）。默认演练，加 --go 才真写。
# 用法: ./scripts/sync.sh                 看看会改什么
#       ./scripts/sync.sh --go            推送（app/ 热重载、web/ 刷新即见，不用重启）
#       ./scripts/sync.sh --go --restart  推完再重建容器（改了 compose.yaml 时用）
# 不推 docker/（启动脚本属镜像，改了就重建镜像）与 config/、.env（机器真值）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${HOMEOS_REMOTE:-/srv/Dev/Work/Docker/homeos/}"
HOST="${NAS_HOST:-nas}"

MODE=dry
DO_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --go) MODE=real ;;
    --restart) DO_RESTART=1 ;;
    *) echo "未知参数: $arg"; echo "用法: $0 [--go] [--restart]"; exit 1 ;;
  esac
done

DRY=()
if [ "$MODE" = dry ]; then DRY=(--dry-run); fi

echo "目标: $HOST:$REMOTE  模式: $MODE"
# web/pages 只是挂载点（容器里被 ./pages 覆盖），推过去不影响 NAS 的功能页
for path in app/ web/; do
  rsync -a --itemize-changes --exclude='__pycache__/' "${DRY[@]}" \
    "$ROOT/$path" "$HOST:$REMOTE$path"
done
rsync -a --itemize-changes "${DRY[@]}" "$ROOT/compose.yaml" "$HOST:${REMOTE}compose.yaml"

echo
if [ "$MODE" = dry ]; then
  echo "以上是演练结果。确认无误后: ./scripts/sync.sh --go"
  exit 0
fi
if [ "$DO_RESTART" = 0 ]; then
  echo "已推送：app/ 热重载、web/ 刷新即见。改了 compose.yaml 才需要 --restart。"
  exit 0
fi

echo "已推送，重建容器…"
# 重建只为让 compose.yaml 的挂载/env 生效；只改 app/、web/ 不需要（还会清掉 /data 里的会话与缓存）
ssh -o ConnectTimeout=10 "$HOST" "cd $REMOTE && docker compose -f compose.yaml -f compose.dev.yaml up -d --force-recreate" ||
  { echo "重建失败：看上面的报错，或 ssh $HOST 'docker compose logs --tail 30'"; exit 1; }

for i in $(seq 1 45); do
  status=$(ssh -o ConnectTimeout=5 "$HOST" "docker inspect homeos --format '{{.State.Health.Status}}'" 2>/dev/null || echo unknown)
  [ "$status" = "healthy" ] && { echo "容器已恢复（${i}s）"; exit 0; }
  sleep 1
done
echo "45 秒内没恢复到 healthy：ssh $HOST 'docker logs homeos --tail 30'"
exit 1
