#!/bin/sh
# 容器启动脚本：把镜像里预热的 OCR 模型同步到可写卷，再启动服务。
# 原因：compose 里 root 文件系统是 read_only，paddle 可能在缓存目录写锁/标记，
# 直接指向镜像内 /opt/models 会失败；而用命名卷覆盖 /opt/models 又会把预热模型挡掉。
set -e

SRC=/opt/models
DST=/data/models

if [ -d "$SRC" ]; then
  if [ ! -f "$DST/.synced" ]; then
    mkdir -p "$DST"
    cp -a "$SRC"/. "$DST"/ 2>/dev/null || true
    touch "$DST/.synced"
  fi
  export PADDLE_PDX_CACHE_HOME="$DST"
fi

exec "$@"
