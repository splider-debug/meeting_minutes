#!/bin/sh
# 容器启动脚本。
# 现在 OCR 用 RapidOCR（模型在 wheel 里），不再需要把预热模型同步到 /data，
# 这里只保留一个兜底：若镜像里将来又塞了模型目录（/opt/models），就同步到可写卷。
set -e

SRC=/opt/models
DST=/data/models

if [ -d "$SRC" ] && [ "$(ls -A "$SRC" 2>/dev/null)" ]; then
  if [ ! -f "$DST/.synced" ]; then
    mkdir -p "$DST"
    cp -a "$SRC"/. "$DST"/ 2>/dev/null || true
    touch "$DST/.synced"
  fi
  export PADDLE_PDX_CACHE_HOME="$DST"
fi

exec "$@"
