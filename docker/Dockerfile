# syntax=docker/dockerfile:1
# 运行时镜像：一个容器跑 web 与 opencode 两个进程，只有挂载点可写。只支持 arm64。
FROM python:3.12-alpine

ARG OPENCODE_VERSION=2.2.5
# 换版本要一起换：musl 包的 sha256 与 glibc 不同
ARG OPENCODE_SHA256=bbf927e3f7a92040f25fd78436c251f41deb25cefac1c906e79c01aa572bf73b
ARG OPENCODE_URL=
ARG TARGETARCH

# HOME 与 XDG_* 由 entrypoint 按模式设置，这里只放与运行模式无关的
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp \
    TZ=Asia/Shanghai

# libstdc++ 必须有：opencode 的 musl 包是 C++（Bun）编的，缺它报 symbol not found。
# /usr/lib/bash 是 bash 的可加载内建（只有 enable -f 用），删了省 2.7MB
RUN apk add --no-cache bash ca-certificates git libstdc++ ripgrep tini tzdata \
    && rm -rf /usr/share/doc /usr/share/man /usr/lib/bash

# 用 python 下载：alpine 基础镜像里没有 curl
RUN set -eux; \
    case "${TARGETARCH:-arm64}" in \
      arm64) : ;; \
      *) echo "只支持 arm64，当前 TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    url="${OPENCODE_URL:-https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-arm64-musl.tar.gz}"; \
    python3 -c "import sys,urllib.request;urllib.request.urlretrieve(sys.argv[1],'/tmp/opencode.tgz')" "$url"; \
    echo "${OPENCODE_SHA256}  /tmp/opencode.tgz" | sha256sum -c -; \
    tar -xz -C /usr/local/bin -f /tmp/opencode.tgz opencode; \
    rm /tmp/opencode.tgz; \
    chmod 755 /usr/local/bin/opencode; \
    opencode --version

WORKDIR /app

# 依赖先装，改代码不会失效这层缓存。uv 的装卸、strip 都必须和 sync 同一个 RUN：
# 后续层里删或改文件不会减小镜像（层是累加的）。
COPY pyproject.toml uv.lock ./
RUN apk add --no-cache --virtual .build binutils \
    && pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project \
    && pip uninstall -y uv \
    && find /app/.venv -name '*.so*' -exec strip --strip-unneeded {} + \
    && apk del .build \
    && rm -rf /root/.cache /tmp/uv-*

# web/pages 是卷：镜像里只建空目录（.dockerignore 排掉了内容）
COPY app ./app
COPY web ./web
# 默认配置给挂载目录用：挂载为空时 entrypoint 复制过去，镜像里不建 /app/config
COPY config ./defaults
COPY opencode ./opencode

# 插件构建时装好（运行时不联网）；opencode 自带 bun，不用另装
ENV HOMEOS_PLUGIN_CACHE=/app/plugin-cache
RUN set -eux; \
    export XDG_CACHE_HOME="$HOMEOS_PLUGIN_CACHE" XDG_CONFIG_HOME=/tmp/ocfg \
           XDG_DATA_HOME=/tmp/ocdata HOME=/tmp; \
    mkdir -p /tmp/ocfg /tmp/ocdata "$HOMEOS_PLUGIN_CACHE"; \
    opencode plugin add cc-safety-net; \
    test -d "$HOMEOS_PLUGIN_CACHE/opencode/npm"; \
    rm -rf /tmp/ocfg /tmp/ocdata
COPY docker/entrypoint.sh docker/prepare.py docker/logging.json /app/docker/
# COPY 会带过宿主机权限（600 也带过来），统一放开读，否则 entrypoint 拷不了 opencode 配置
RUN mkdir -p /app/web/pages /data && chmod +x /app/docker/entrypoint.sh && chmod -R a+rX /app/opencode

# uid/gid 必须与宿主机挂载目录的所有者一致（默认 1000），否则写不进卷
RUN addgroup -g 1000 homeos \
    && adduser -D -u 1000 -G homeos -h /app -s /sbin/nologin homeos \
    && chown -R 1000:1000 /data
USER 1000:1000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/api/ready || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/app/docker/entrypoint.sh"]
