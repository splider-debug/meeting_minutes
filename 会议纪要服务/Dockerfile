# syntax=docker/dockerfile:1
# 注意：构建上下文是"仓库根"（会议资料整理/），不是本目录。
# 因为 tools/ 在仓库根，服务必须复用它作为唯一版式真源：
#   cd 会议纪要服务 && docker compose build      （compose 里已写 context: ..）
#
# OCR 引擎：RapidOCR（ONNX 运行时），模型打包在 wheel 内，构建阶段无需下载。
#
# 境内网络注意：python:3.11-slim 是 Debian，默认走 deb.debian.org 与 PyPI 官方源，
# 实测会超时（pip Read timed out / apt 卡住）。所以下面两处都换成了国内镜像：
#   · apt  → mirrors.aliyun.com
#   · pip  → mirrors.aliyun.com/pypi
# 换源后如果某个包拉不到，把 ARG 里的地址改成其它可用镜像再重建。

# ---- 阶段 1：依赖 ----
FROM python:3.11-slim AS deps

# 国内 PyPI 镜像 + 放宽超时/重试（大包如 opencv/onnxruntime 各约 50-60MB）
# 部署在腾讯云时用 mirrors.cloud.tencent.com（同机房内网，最快）；
# 其它机房可覆盖：--build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/ --build-arg PIP_HOST=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/
ARG PIP_HOST=mirrors.cloud.tencent.com
ENV PIP_INDEX_URL=${PIP_INDEX} \
    PIP_TRUSTED_HOST=${PIP_HOST} \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /w
COPY 会议纪要服务/requirements-core.txt 会议纪要服务/requirements-ocr-rapid.txt 会议纪要服务/requirements-server.txt ./
# 缓存挂载：重建时复用已下载的 wheel，不用每次重下
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-server.txt

# ---- 阶段 2：运行 ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    TOOLS_DIR=/app/app/tools \
    DATA_DIR=/data

# Debian 换国内源（兼容 bookworm 的 sources.list 与 trixie 的 sources.list.d/*.sources）
ARG DEB_MIRROR=mirrors.cloud.tencent.com
RUN set -eux; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${DEB_MIRROR}|g; s|security.debian.org|${DEB_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    for f in /etc/apt/sources.list.d/*.sources; do \
        [ -e "$f" ] && sed -i "s|deb.debian.org|${DEB_MIRROR}|g; s|security.debian.org|${DEB_MIRROR}|g" "$f" || true; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
        libgl1 libglib2.0-0 ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 只拷库不够：pip 装的命令行入口（uvicorn 等）在 /usr/local/bin 下。
# 两个都拷，否则容器启动会报 "exec: uvicorn: not found"。
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin/ /usr/local/bin/
COPY 会议纪要服务/app/ ./app/
COPY 会议纪要服务/run_m1.py 会议纪要服务/check_env.py 会议纪要服务/fetch_models.py ./
COPY 会议纪要服务/docker/entrypoint.sh /app/entrypoint.sh
# 仓库 tools/ 原样进镜像：服务与本地手工整理共用同一套版式实现
COPY tools/ ./app/tools/

# 构建期自检：依赖装错、模块缺失、入口不可用时当场失败，而不是起容器后崩溃重启
RUN python -c "import uvicorn, fastapi, argon2, PIL, httpx; \
import onnxruntime, rapidocr_onnxruntime; \
import app.web; print('deps + app import OK')" \
    && python -m uvicorn --version

RUN useradd -u 10001 -m app && chmod +x /app/entrypoint.sh \
    && mkdir -p /data && chown -R app:app /data /app /home/app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
# 用 python -m 而非裸 uvicorn：不依赖 PATH 里的命令行脚本，最稳
CMD ["python", "-m", "uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
