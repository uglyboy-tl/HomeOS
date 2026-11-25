# syntax=docker/dockerfile:1
# HomeOS 运行时镜像：一个容器起两个进程（web / opencode），除卷之外全只读。
# 目标平台只有 arm64（NAS），opencode 也只装 arm64 的 release。
#
# 基底用 Alpine（musl）：python:3.12-slim 是 145MB，alpine 约 50MB。
# 前提是 opencode 有 musl 版（releases 里有 opencode-linux-arm64-musl.tar.gz），
# 依赖也都有 musllinux 轮子（pillow / pycryptodome 已验证）。
FROM python:3.12-alpine

ARG OPENCODE_VERSION=2.2.5
# 对应 opencode-linux-arm64-musl.tar.gz 的 sha256；换版本时一起换（musl 与 glibc 的包不同！）
ARG OPENCODE_SHA256=bbf927e3f7a92040f25fd78436c251f41deb25cefac1c906e79c01aa572bf73b
ARG OPENCODE_URL=
ARG TARGETARCH

# HOME 与 XDG_* 由 entrypoint 按模式设置，这里只放与运行时无关的
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp \
    TZ=Asia/Shanghai

# git：opencode 的快照/撤销靠它。ripgrep：agent 搜文件。tini：PID 1。
# bash：entrypoint 是 bash 脚本。tzdata：容器里 TZ=Asia/Shanghai 才有意义。
# libstdc++：opencode 的 musl 版是 C++（Bun）编出来的，缺它报 "symbol not found"
RUN apk add --no-cache bash ca-certificates git libstdc++ ripgrep tini tzdata \
    && rm -rf /usr/share/doc /usr/share/man


# opencode 二进制（钉死版本 + 校验和；只有 arm64 这条路，amd64 构建直接失败）。
# 用 python 下载：alpine 基础镜像里没有 curl。
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

# 依赖先装，改代码不失效这层缓存。
# uv 只为构建而来：装完就用它同步依赖，然后连同缓存一起删掉（省 35MB）。
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --no-install-project \
    && pip uninstall -y uv \
    && rm -rf /root/.cache /tmp/uv-*

# 代码与前端。web/pages 是卷，镜像里只建空目录（.dockerignore 排掉了内容）。
COPY app ./app
COPY web ./web
# 设备表：镜像里带一份，不挂 config 卷时米家页能直接用；
# 想用机器上那份就在 compose 里放开 `./config:/app/config:ro`（改表不用重建镜像）。
COPY config ./config
COPY opencode ./opencode

# 插件在构建时装好：运行时不联网（NAS 上 npm 很慢，根文件系统也写不了）。
# 缓存落在 /app/plugin-cache，entrypoint 启动时复制到可写的 XDG_CACHE_HOME。
# 不需要额外装 bun：opencode 自带运行时，安装和运行插件都用它。
ENV HOMEOS_PLUGIN_CACHE=/app/plugin-cache
RUN set -eux; \
    export XDG_CACHE_HOME="$HOMEOS_PLUGIN_CACHE" XDG_CONFIG_HOME=/tmp/ocfg \
           XDG_DATA_HOME=/tmp/ocdata HOME=/tmp; \
    mkdir -p /tmp/ocfg /tmp/ocdata "$HOMEOS_PLUGIN_CACHE"; \
    opencode plugin add cc-safety-net; \
    test -d "$HOMEOS_PLUGIN_CACHE/opencode/npm"; \
    rm -rf /tmp/ocfg /tmp/ocdata
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
# 镜像里以 uid 1000 读这些文件，而 COPY 会原样带过宿主机的权限（600 也带过来），
# 所以统一放开读权限，否则 entrypoint 拷不了 opencode 配置。
RUN mkdir -p /app/web/pages /data && chmod +x /app/docker/entrypoint.sh && chmod -R a+rX /app/opencode

# 不用 root 跑：agent 有 shell 权限，只读根挡不住读/写卷。
# uid/gid 必须与宿主机挂载目录的所有者一致（默认 1000），否则卷写不进去。
# 运行时用不到 pip / ensurepip / idle / lib2to3：删掉省 ~10MB
RUN rm -rf /usr/local/lib/python3.12/ensurepip \
           /usr/local/lib/python3.12/idlelib \
           /usr/local/lib/python3.12/lib2to3 \
           /usr/local/lib/python3.12/test \
           /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/site-packages/pkg_resources \
           /usr/local/lib/python3.12/site-packages/wheel* \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/idle3

RUN addgroup -g 1000 homeos \
    && adduser -D -u 1000 -G homeos -h /app -s /sbin/nologin homeos \
    && chown -R 1000:1000 /data
USER 1000:1000

EXPOSE 8080
# busybox 自带 wget，不用为 healthcheck 单独装 curl
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/api/ready || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/app/docker/entrypoint.sh"]
