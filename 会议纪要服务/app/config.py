# -*- coding: utf-8 -*-
"""配置与路径解析。全部可用环境变量覆盖，便于容器里改而不改代码。"""
import os
import pathlib
import sys

SERVICE_DIR = pathlib.Path(__file__).resolve().parent.parent


def load_dotenv(path: pathlib.Path | None = None) -> int:
    """极简 .env 加载（不引入额外依赖）：只填还不存在的环境变量。"""
    p = path or (SERVICE_DIR / ".env")
    if not p.is_file():
        return 0
    n = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


DOTENV_LOADED = load_dotenv()


def _find_tools() -> pathlib.Path:
    """定位 tools/ 目录（md2docx.py 所在）。

    优先级：TOOLS_DIR 环境变量 → 服务目录/app/tools（镜像内）→ 仓库根/tools（本机开发）
    """
    env = os.environ.get("TOOLS_DIR")
    if env:
        p = pathlib.Path(env)
        if (p / "md2docx.py").is_file():
            return p
        raise RuntimeError("TOOLS_DIR 指向的目录里没有 md2docx.py：%s" % p)
    candidates = [SERVICE_DIR / "app" / "tools", SERVICE_DIR.parent / "tools"]
    for c in candidates:
        if (c / "md2docx.py").is_file():
            return c
    raise RuntimeError(
        "找不到 tools/ 目录。已尝试：%s。请设置环境变量 TOOLS_DIR 指向仓库的 tools 目录。"
        % "；".join(str(c) for c in candidates)
    )


TOOLS_DIR = _find_tools()

# 数据目录：默认 <服务目录>/data，容器里用 /data
DATA_DIR = os.environ.get("DATA_DIR") or str(SERVICE_DIR / "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 把"家目录"也指到项目内：paddle 内部有多处 expanduser('~/.cache/…')，
# 而有些 Windows 机器上 C:\Users\<user>\.cache、\.paddlex 创建会被拒（WinError 5），
# 只改 PADDLE_* 环境变量盖不住这些硬编码路径。容器里落到 /data/home，重启不丢。
HOME_DIR = os.path.join(DATA_DIR, "home")
os.makedirs(HOME_DIR, exist_ok=True)
os.environ["HOME"] = HOME_DIR
os.environ["USERPROFILE"] = HOME_DIR


def _writable_dir(preferred: str, fallback: str, label: str) -> str:
    """尽量用 preferred；建不出来（只读文件系统、无权限）就退到 fallback。

    典型场景：镜像 `read_only: true`，而 .env 里还留着 paddle 时代的
    PADDLE_PDX_CACHE_HOME=/opt/models（不存在且不可写）→ 启动即 OSError 30。
    """
    for path in (preferred, fallback):
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    print("警告：%s 无可写目录（%s / %s），相关功能可能不可用" % (label, preferred, fallback),
          file=sys.stderr)
    return fallback


# OCR 模型缓存：一定要落在项目内，绝不能用用户目录。
# 原因：paddle 默认写 C:\Users\<user>\.paddlex，而某些 Windows 机器上该路径创建被拒
# （报 WinError 5 拒绝访问），表现为"OCR 引擎初始化失败"。
# RapidOCR 不需要这个目录（模型在 wheel 内），paddle 引擎才用。
MODEL_DIR = _writable_dir(os.environ.get("PADDLE_PDX_CACHE_HOME") or os.path.join(DATA_DIR, "models"),
                          os.path.join(DATA_DIR, "models"), "OCR 模型缓存")
os.environ["PADDLE_PDX_CACHE_HOME"] = MODEL_DIR
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(DATA_DIR, ".cache"))

# paddle 自己的扩展缓存默认在 ~/.cache/paddle_extension；modelscope / huggingface 也会写用户目录。
# 统一改到项目内，避免"某些机器禁止写 C:\Users\<user>\.*"导致 OCR 初始化失败。
for _env, _sub in (("PADDLE_EXTENSION_DIR", "paddle_ext"),
                   ("MODELSCOPE_CACHE", "modelscope"),
                   ("HF_HOME", "hf")):
    _p = _writable_dir(os.environ.get(_env) or os.path.join(DATA_DIR, _sub),
                       os.path.join(DATA_DIR, _sub), _env)
    os.environ[_env] = _p

# 限额（对应技术方案 §二 需求清单 #13）
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
TASK_TIMEOUT_SEC = int(os.environ.get("TASK_TIMEOUT_SEC", "1200"))

# 图片切片（SOP §4 阶段 1-E：2100px/片、重叠 120px）
IMAGE_CHUNK = int(os.environ.get("IMAGE_CHUNK", "2100"))
IMAGE_OVERLAP = int(os.environ.get("IMAGE_OVERLAP", "120"))
IMAGE_MAX_SIDE = int(os.environ.get("IMAGE_MAX_SIDE", "30000"))
OCR_MIN_AGREE = float(os.environ.get("OCR_MIN_AGREE", "0.95"))
# 第二遍复核策略：auto（只查低置信度切片，默认）/ always / off
OCR_SECOND_PASS = os.environ.get("OCR_SECOND_PASS", "auto")
OCR_RECHECK_SCORE = float(os.environ.get("OCR_RECHECK_SCORE", "0.90"))
OCR_SCALES = [float(x) for x in os.environ.get("OCR_SCALES", "1.0,1.2").split(",")]

# 文本解码
GARBAGE_MAX = float(os.environ.get("GARBAGE_MAX", "0.005"))

# 支持的类型
DOCX_EXT = {".docx"}
TEXT_EXT = {".txt", ".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
SUPPORTED_EXT = DOCX_EXT | TEXT_EXT | IMAGE_EXT

# 渲染主题：交付稿用 report（SOP §6.2）
RENDER_THEME = os.environ.get("RENDER_THEME", "report")
