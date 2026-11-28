# syntax=docker/dockerfile:1
# 运行时镜像：一个容器跑 web，agent（pi）由 web 按请求拉起子进程。支持 amd64 与 arm64。
#
# pi 官方只发 glibc 包，而 alpine 是 musl：所以自己用 bun 交叉编一份 musl 目标（两个架构都能编）。
# 代价是拿不到上游二进制与 sha256，只有版本号钉住；实测 chat/工具/扩展都正常。
FROM oven/bun:1 AS pibin
ARG PI_VERSION=1.7.0
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-arm64}" in \
      amd64) target=x64 ;; \
      arm64) target=arm64 ;; \
      *) echo "不支持的架构：${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /w; cd /w; \
    bun add "@earendil-works/pi-coding-agent@${PI_VERSION}"; \
    bun build --compile --target="bun-linux-${target}-musl" \
      --no-compile-autoload-bunfig --no-compile-autoload-dotenv --compile-autoload-package-json \
      node_modules/@earendil-works/pi-coding-agent/dist/bun/cli.js --outfile /w/pi-musl

# cc-safety-net 是 pi 的进程内扩展：只用 bun 把它装下来，最终镜像里不装 bun。
# 它的 dist/ 是分包产物（入口 619B + ../chunks/*），所以拷整个 dist，不挑单文件。
FROM oven/bun:1 AS piext
ARG CCSN_VERSION=2.8.3
RUN mkdir -p /pi-ext && cd /pi-ext \
    && printf '{"name":"pi-ext","private":true}\n' > package.json \
    && bun add "cc-safety-net@${CCSN_VERSION}" \
    && test -f node_modules/cc-safety-net/dist/pi/index.js

FROM python:3.12-alpine

# 与运行模式无关的镜像级环境
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    TMPDIR=/tmp \
    TZ=Asia/Shanghai

# libstdc++ 必须有：bun 编出来的 musl 二进制是 C++，缺它报 symbol not found
RUN apk add --no-cache bash ca-certificates libstdc++ tini tzdata \
    && rm -rf /usr/share/doc /usr/share/man /usr/lib/bash

COPY --from=pibin /w/pi-musl /opt/pi/pi
RUN ln -sf /opt/pi/pi /usr/local/bin/pi

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

COPY app ./app
COPY web ./web
# 默认配置给挂载目录用：挂载为空时 entrypoint 复制过去，镜像里不建 /app/config
COPY config ./defaults
# 只要扩展自己的 dist 与 manifest，不搬整棵 node_modules
COPY --from=piext /pi-ext/node_modules/cc-safety-net/dist /app/pi-ext/cc-safety-net/dist
COPY --from=piext /pi-ext/node_modules/cc-safety-net/package.json /app/pi-ext/cc-safety-net/

# 这三个放镜像 ENV（不是启动脚本 export）：docker exec 起的进程不继承 entrypoint 的环境，
# 少了它们直接跑 pi 会去写只读的 /root 与 /app/.pi。必须在依赖装完之后设，否则构建期缓存会写到 /data。
ENV HOME=/data \
    PI_CODING_AGENT_DIR=/data/pi \
    HOMEOS_PI_AGENT_DIR=/data/pi

COPY docker/entrypoint.sh docker/prepare.py /app/docker/
# 策略必须住在只读层：它是 agent 的护栏，不能放在可写的 config 挂载里
COPY config/cc-safety-net-policy.json /app/web/.cc-safety-net/policy.json
RUN mkdir -p /app/web/pages /data/sessions \
    && test -s /app/web/.cc-safety-net/policy.json \
    && chmod +x /app/docker/entrypoint.sh \
    && chmod -R a+rX /app/app /app/web /app/pi-ext

# 运行时只认 --extension 传的绝对路径：项目级扩展（cwd 下的 .pi/extensions）永不出现
RUN test ! -e /app/web/.pi

# uid/gid 必须与宿主机挂载目录的所有者一致（默认 1000），否则写不进卷
RUN addgroup -g 1000 homeos \
    && adduser -D -u 1000 -G homeos -h /app -s /sbin/nologin homeos \
    && chown -R 1000:1000 /data /app/web/pages
USER 1000:1000

# 可写点只有两个：会话目录（命名卷，且必须在镜像里预建并 chown，
# 否则命名卷挂到 tmpfs 下的空目录会是 root:root，uid 1000 写不了）与功能页。
VOLUME ["/data/sessions", "/app/web/pages"]

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/api/ready || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/app/docker/entrypoint.sh"]
