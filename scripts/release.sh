#!/usr/bin/env bash
# 发版：改版本号 → 提交 → 打 annotated tag → 推 main 与 tag（CI 在 v* 上出镜像）。
# 用法: ./scripts/release.sh [patch|minor|major|X.Y.Z] [--go]   默认演练，只看计划
# 前置条件：在 main、工作区干净、不落后于 origin、tag 不存在。未推送的提交会一起推上去。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() {
  echo "$*" >&2
  exit 1
}

# 规范的 semver：CI 的 metadata-action 不认前导零，只推 :latest 不推版本 tag
SEMVER='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
maxver() { printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1; }

GO=0
KIND=""
for arg in "$@"; do
  case "$arg" in
    --go)
      GO=1
      continue
      ;;
    patch | minor | major) ;;
    [0-9]*)
      [[ "$arg" =~ $SEMVER ]] ||
        die "版本号要写成 X.Y.Z（不带前导零，CI 的 semver 只认这种）: $arg"
      ;;
    *)
      echo "未知参数: $arg"
      echo "用法: $0 [patch|minor|major|X.Y.Z] [--go]"
      exit 1
      ;;
  esac
  [ -z "$KIND" ] || die "只能给一个版本参数: $KIND / $arg"
  KIND="$arg"
done
KIND="${KIND:-patch}"

current=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)
[[ "$current" =~ $SEMVER ]] || die "pyproject.toml 的版本不是规范的 X.Y.Z: '$current'"

# 目标版本：显式给就用给的，否则按 semver 进位
if [[ "$KIND" =~ $SEMVER ]]; then
  next="$KIND"
else
  IFS=. read -r major minor patch <<<"$current"
  case "$KIND" in
    major) next="$((major + 1)).0.0" ;;
    minor) next="$major.$((minor + 1)).0" ;;
    patch) next="$major.$minor.$((patch + 1))" ;;
  esac
fi
tag="v$next"

[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "只在 main 上发版"
[ -z "$(git status --porcelain)" ] || die "工作区不干净，先提交或 stash"
uv lock --check --quiet || die "uv.lock 与 pyproject 不一致，先 uv lock（别把依赖漂移混进发版提交）"
# 带上 --tags：远端有、本地没有的 tag 也要算进下面的下限
git fetch --quiet --tags origin main || die "拉取 origin/main 失败（网络？）"
# 落后要先同步（否则推送必然被拒）；领先没关系——本次的原子推送会把它们一起送上去
behind=$(git rev-list --count HEAD..origin/main) || die "算不出与 origin/main 的差异（远端缺 main？）"
[ "$behind" = 0 ] || die "远端 main 比本地多 $behind 个提交，先 git pull --rebase"
ahead=$(git rev-list --count origin/main..HEAD) || die "算不出领先数（origin/main 缺失？）"

# 降级会让 CI 把 :latest 指回旧镜像，NAS 一 pull 就回退；已有 tag 也算下限（手工打过 tag 时 pyproject 可能落后）
# 必须在 fetch 之后取，否则刚推上去、本地还没有的 tag 会被漏掉
latest=$(git tag --list 'v[0-9]*' --sort=-v:refname)
latest=${latest%%$'\n'*} # 不用管道取首行：tag 极多时 head 会 SIGPIPE 掉整个赋值
floor=$(maxver "$current" "${latest#v}")
[ "$next" != "$current" ] && [ "$(maxver "$floor" "$next")" = "$next" ] ||
  die "目标版本 $next 不高于当前 $current${latest:+ / 已有 tag $latest}（会把 :latest 指回去）"

git rev-parse -q --verify "refs/tags/$tag" >/dev/null &&
  die "本地已有 tag $tag（上次发布的用更高版本号；上次推送失败留下的先 git tag -d $tag）"
[ -z "$(git ls-remote --tags origin "refs/tags/$tag")" ] || die "远端已有 tag $tag"

# tag 正文：自上一个 v* tag 以来的提交标题（GitHub Release 的说明由 CI --generate-notes 另生成）
since=$(git describe --tags --match 'v[0-9]*' --abbrev=0 2>/dev/null || echo "")
range="HEAD"
[ -n "$since" ] && range="$since..HEAD"
notes=$(git log --no-merges --pretty='- %s' "$range")

echo "当前版本: $current"
echo "目标版本: $next   tag: $tag"
[ "$ahead" = 0 ] || echo "未推送的提交: $ahead 个（会随本次一起推上去）"
echo
echo "将要做："
echo "  1. pyproject.toml 版本改为 $next，uv lock 跟着更新"
echo "  2. 提交 chore(release): $next"
echo "  3. 打 annotated tag $tag，正文为下面这些提交"
echo "  4. 推 main 与 tag（CI 在 v* 上构建镜像）"
echo
[ -n "$notes" ] && echo "$notes" | sed 's/^/    /'

if [ "$GO" = 0 ]; then
  echo
  echo "演练结束。确认后: ./scripts/release.sh $KIND --go"
  exit 0
fi

echo
echo "跑 CI 同款检查…"
uv run ruff check .
uv run ruff format --check .
uv run pytest -q --cov=app --cov-fail-under=95

# 改版本号之前先留底：提交或推送失败都还原，别留下半成品
backup=$(mktemp -d)
cp pyproject.toml uv.lock "$backup/"
released=0
committed=0
cleanup() {
  if [ "$released" = 1 ]; then
    rm -rf "$backup"
    return
  fi
  cp "$backup/pyproject.toml" "$backup/uv.lock" .
  git reset -q -- pyproject.toml uv.lock
  [ "$committed" = 1 ] && git reset -q HEAD~1 # 把 chore(release) 提交撤掉，配合上面的还原回到出发状态
  rm -rf "$backup"
}
trap cleanup EXIT
sed -i "s/^version = \"$current\"$/version = \"$next\"/" pyproject.toml
# 重新从 pyproject 取值比对，而不是全文 grep：将来多一个 version 行也不会假通过
[ "$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml)" = "$next" ] ||
  die "改版本号没命中，pyproject.toml 的格式变了？"
uv lock --quiet || die "uv lock 失败（断网？），两个文件已还原"
# 只认 homeos 自己那一段：整份 lock 里任何包撞上同版本号都会假通过
awk '/^name = "homeos"$/{found=1; next} found && /^version = /{print; exit}' uv.lock |
  grep -qx "version = \"$next\"" || die "uv.lock 没跟上版本（CI 用 --frozen）"
git add pyproject.toml uv.lock
git commit -q -m "chore(release): $next" || die "提交失败，两个版本文件已还原（看上面 git 的输出）"
committed=1
git tag -a "$tag" -m "$tag" -m "$notes" || die "打 tag 失败，提交已撤回"
# --atomic：任一失败就都不落地，避免远端有 chore(release) 却没有 tag
git push --atomic --quiet origin main "refs/tags/$tag" ||
  die "推送失败，本地提交已撤回。本地 tag $tag 还留着：git ls-remote --tags origin $tag 能看到就说明已发布（git pull 即可），看不到就 git tag -d $tag 再重试"
released=1

echo
echo "已发布 $tag（$(git rev-parse --short HEAD)）"
echo "看构建: gh run list --limit 1"
echo "上 NAS : 先在 NAS 栈目录 docker compose pull，再 ./scripts/sync.sh --go --restart"
