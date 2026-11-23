# syntax=docker/dockerfile:1
# HomeOS 运行时镜像：一个容器起两个进程（web / opencode），除卷之外全只读。
# 目标平台只有 arm64（NAS），opencode 也只装 arm64 的 release。
FROM python:3.12-slim

ARG OPENCODE_VERSION=2.2.5
# release 的 sha256（v2.2.5 / opencode-linux-arm64.tar.gz）；换版本时一起换
ARG OPENCODE_SHA256=5a528f42caf576c6cdece6a191eb273d3db54effa5518e0402684d8f8026aeb6
ARG TARGETARCH

# HOME 与 XDG_* 由 entrypoint 按模式设置，这里只放与运行时无关的
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp

# git：opencode 的快照/撤销靠它。ripgrep：agent 搜文件用。tini：容器里的 PID 1。
# passwd：提供 useradd/groupadd（镜像最后要降成非 root）。
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git ripgrep tini passwd \
    && rm -rf /var/lib/apt/lists/*

# opencode 二进制（钉死版本 + 校验和；只有 arm64 这条路，amd64 构建直接失败）
RUN set -eux; \
    case "${TARGETARCH:-arm64}" in \
      arm64) : ;; \
      *) echo "只支持 arm64，当前 TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 \
      "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-arm64.tar.gz" \
      -o /tmp/opencode.tgz; \
    echo "${OPENCODE_SHA256}  /tmp/opencode.tgz" | sha256sum -c -; \
    tar -xz -C /usr/local/bin -f /tmp/opencode.tgz opencode; \
    rm /tmp/opencode.tgz; \
    chmod 755 /usr/local/bin/opencode; \
    opencode --version

WORKDIR /app

# 依赖先装，改代码不失效这层缓存
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 代码与前端。web/pages 是卷，镜像里只建空目录（.dockerignore 排掉了内容）
COPY app ./app
COPY web ./web
COPY config ./config
COPY opencode ./opencode
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN mkdir -p /app/web/pages /data && chmod +x /app/docker/entrypoint.sh

# 不用 root 跑：agent 有 shell 权限，只读根挡不住读/写卷。
# uid/gid 必须与宿主机挂载目录的所有者一致（默认 1000），否则卷写不进去。
RUN groupadd -g 1000 homeos \
    && useradd -u 1000 -g 1000 -M -d /app -s /usr/sbin/nologin homeos \
    && chown -R 1000:1000 /data
USER 1000:1000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD curl -fsS http://127.0.0.1:8080/api/ready || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
