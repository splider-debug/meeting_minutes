# -*- coding: utf-8 -*-
"""手动预下载 PaddleOCR 模型到项目内（不依赖 paddle 自己的下载器）。

用法：
    python fetch_models.py              # 下载 det + rec（约 133MB）
    python fetch_models.py --list       # 只打印地址与目标目录
    python fetch_models.py --only rec   # 只下识别模型

为什么需要它：
  paddle/paddlex 自带下载器会往用户目录写临时文件，某些机器上会被拒（WinError 5），
  表现为"OCR 引擎初始化失败"。本脚本用标准库直接下到 data/models，绕开这条路径。
"""
import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0"
MODELS = {
    "det": "PP-OCRv6_medium_det",     # 文本检测，约 59 MB
    "rec": "PP-OCRv6_medium_rec",     # 文本识别，约 73 MB
}

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from app import config                      # 复用同一套目录规则
    MODEL_HOME = os.path.join(config.MODEL_DIR, "official_models")
except Exception:
    MODEL_HOME = os.path.join(HERE, "data", "models", "official_models")


def target_dir(name: str) -> str:
    return os.path.join(MODEL_HOME, name)


def is_ready(name: str) -> bool:
    d = target_dir(name)
    return (os.path.isfile(os.path.join(d, "inference.json"))
            and os.path.isfile(os.path.join(d, "inference.pdiparams")))


def download(key: str) -> bool:
    name = MODELS[key]
    url = "%s/%s_infer.tar" % (BASE, name)
    dst = target_dir(name)
    if is_ready(name):
        print("跳过 %s：已存在（%s）" % (name, dst))
        return True

    print("下载 %s\n  %s" % (name, url))
    os.makedirs(dst, exist_ok=True)
    tmp_dir = os.path.join(MODEL_HOME, "tmp_download")
    os.makedirs(tmp_dir, exist_ok=True)
    tar_path = os.path.join(tmp_dir, name + ".tar")

    last = [-1]

    def hook(blocks, block_size, total):
        if total <= 0:
            return
        pct = int(blocks * block_size * 100 / total)
        if pct >= last[0] + 10:
            last[0] = pct
            print("  %3d%%" % pct, flush=True)

    try:
        urllib.request.urlretrieve(url, tar_path, reporthook=hook)
    except Exception as e:
        print("下载失败：%s: %s" % (type(e).__name__, e))
        print("可手动下载后解压到：%s" % dst)
        return False

    print("  解压…")
    with tarfile.open(tar_path) as tf:
        # 逐个成员解压：自己建目录（用普通权限），避免 tarfile 用受限模式建目录后写不进去
        for m in tf.getmembers():
            rel = m.name.replace("\\", "/").lstrip("./")
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                continue
            out = os.path.join(tmp_dir, rel)
            if m.isdir():
                os.makedirs(out, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            src_f = tf.extractfile(m)
            if src_f is None:
                continue
            with src_f, open(out, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)
    os.remove(tar_path)

    # tar 里可能直接是文件，也可能套一层目录，两种都兼容
    entries = [e for e in os.listdir(tmp_dir) if e != os.path.basename(tar_path)]
    src = os.path.join(tmp_dir, entries[0]) if len(entries) == 1 and \
        os.path.isdir(os.path.join(tmp_dir, entries[0])) else tmp_dir
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    ok = is_ready(name)
    print("  %s → %s" % ("完成" if ok else "解压后缺少 inference.json/pdiparams", dst))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印地址与目录")
    ap.add_argument("--only", choices=list(MODELS) + ["all"], default="all")
    a = ap.parse_args()

    keys = list(MODELS) if a.only == "all" else [a.only]
    if a.list:
        for k in keys:
            print("%-4s %s/%s_infer.tar\n     → %s" % (k, BASE, MODELS[k], target_dir(MODELS[k])))
        return 0

    print("模型目录：%s\n" % MODEL_HOME)
    ok = all(download(k) for k in keys)
    print("\n结果：%s" % ("全部就绪" if ok else "有失败项，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
