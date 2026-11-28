#!/usr/bin/env bash
# 本机 → NAS：推 app/ 与 web/（调试形态挂载进容器）。默认演练，加 --go 才真写。
# 不推 docker/（属镜像）、config/ 与 .env（机器真值）。--restart 顺带推 compose.yaml 并重建容器。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${NAS_HOST:-nas}"
REMOTE="${HOMEOS_REMOTE:-/srv/Dev/Work/Docker/homeos/}"

# REMOTE 打错（空或相对路径）时 --delete 会清掉别处
case "$REMOTE" in
  /*) ;;
  *)
    echo "REMOTE 必须是绝对路径: '$REMOTE'"
    exit 1
    ;;
esac

GO=0
RESTART=0
for arg in "$@"; do
  case "$arg" in
    --go) GO=1 ;;
    --restart) RESTART=1 ;;
    *)
      echo "未知参数: $arg"
      echo "用法: $0 [--go] [--restart]"
      exit 1
      ;;
  esac
done

# --delete 清掉那边的过期文件；pages/ 是 agent 写的功能页，只保护不传输；
# .cc-safety-net 的策略文件推得过去但不能被删（本地这份是 dev.sh 生成的，缺了会让 guard 失效）
RSYNC=(
  -a --itemize-changes --delete
  --exclude='__pycache__/'
  --exclude='/pages/'
  --filter='P /.cc-safety-net/'
)
[ "$GO" = 0 ] && RSYNC+=(--dry-run)

echo "目标: $HOST:$REMOTE  模式: $([ "$GO" = 1 ] && echo 真写 || echo 演练)"
rsync "${RSYNC[@]}" "$ROOT/app/" "$HOST:${REMOTE}app/"
rsync "${RSYNC[@]}" "$ROOT/web/" "$HOST:${REMOTE}web/"

if [ "$GO" = 0 ]; then
  echo
  echo "演练结束，确认后: ./scripts/sync.sh --go"
  exit 0
fi

if [ "$RESTART" = 0 ]; then
  echo "已推送：uvicorn 在 --reload，刷新页面即见；改了 compose 才需要 --restart。"
  exit 0
fi

# 有 compose.dev.yaml 就是调试形态（那份不进仓库，原样留在 NAS）
FILES="-f compose.yaml"
if ssh -o ConnectTimeout=10 "$HOST" "test -f ${REMOTE}compose.dev.yaml"; then
  FILES="-f compose.yaml -f compose.dev.yaml"
fi
rsync -a --itemize-changes "$ROOT/compose.yaml" "$HOST:${REMOTE}compose.yaml"
echo "已推送，重建容器（/data 是 tmpfs 会清空，会话在命名卷 homeos-sessions 里不受影响）…"
ssh -o ConnectTimeout=10 "$HOST" "cd $REMOTE && docker compose $FILES up -d --force-recreate" ||
  {
    echo "重建失败：ssh $HOST 'cd $REMOTE && docker compose $FILES logs --tail 30'"
    exit 1
  }

for _ in $(seq 1 60); do
  status=$(ssh -o ConnectTimeout=5 "$HOST" \
    "docker inspect homeos --format '{{.State.Health.Status}}'" 2>/dev/null || true)
  [ "$status" = "healthy" ] && {
    echo "容器已恢复"
    exit 0
  }
  sleep 1
done
echo "容器 60 秒内没变 healthy：ssh $HOST 'cd $REMOTE && docker compose $FILES logs --tail 50'"
exit 1
