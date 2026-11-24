# syntax=docker/dockerfile:1
# 注意：构建上下文是"仓库根"（会议资料整理/），不是本目录。
# 因为 tools/ 在仓库根，服务必须复用它作为唯一版式真源：
#   docker compose build            （在 会议纪要服务/ 目录下执行，compose 里已写 context: ..）
#
# ---- 阶段 1：依赖 ----
FROM python:3.11-slim AS deps
WORKDIR /w
COPY 会议纪要服务/requirements-core.txt 会议纪要服务/requirements-ocr-py311.txt 会议纪要服务/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

# ---- 阶段 2：运行 ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    TOOLS_DIR=/app/app/tools \
    DATA_DIR=/data \
    PADDLE_PDX_CACHE_HOME=/opt/models

# opencv（paddleocr 依赖）需要 libGL/libglib
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 fonts-noto-cjk ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY 会议纪要服务/app/ ./app/
COPY 会议纪要服务/run_m1.py 会议纪要服务/check_env.py ./
COPY 会议纪要服务/docker/entrypoint.sh /app/entrypoint.sh
# 仓库 tools/ 原样进镜像：服务与本地手工整理共用同一套版式实现
COPY tools/ ./app/tools/

# 预热 OCR 模型（约 60—150MB）：不做这一步，首个图片请求会现下载并可能撞 20 分钟超时
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, lang='ch')" || echo "OCR 预热跳过（离线构建）"

RUN useradd -u 10001 -m app && chmod +x /app/entrypoint.sh \
    && mkdir -p /data && chown -R app:app /data /app /opt/models /home/app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
