# -*- coding: utf-8 -*-
"""图片解析：切片 → 双遍 OCR → 按行距还原段落。

为什么双遍：图片没有文字层，拿不到 identical。用两种缩放各跑一遍、逐行比对，
一致率低于阈值就把差异行标出来，让人复核 —— 这是产线 E 的替代自证手段。
"""
import os
import statistics

from ..config import (DATA_DIR, IMAGE_CHUNK, IMAGE_MAX_SIDE, IMAGE_OVERLAP,
                      MODEL_DIR, OCR_MIN_AGREE, OCR_SCALES)
from ..errors import OcrUnavailable, ParseError


def _prepare_model_env() -> None:
    """把 paddle 的缓存目录钉在项目内（config 已设，这里再兜一次）。

    某些 Windows 机器上创建 C:\\Users\\<user>\\.paddlex 会被拒（WinError 5），
    paddle 默认就往那里写 → 初始化直接失败。
    """
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.environ["PADDLE_PDX_CACHE_HOME"] = MODEL_DIR
    os.environ.setdefault("XDG_CACHE_HOME", os.path.join(DATA_DIR, ".cache"))


def _open_image(path: str):
    try:
        from PIL import Image
    except ImportError:
        raise OcrUnavailable("缺少 Pillow，无法处理图片。请 pip install -r requirements-core.txt")
    Image.MAX_IMAGE_PIXELS = None          # 18428px 长图必须放开默认上限
    try:
        img = Image.open(path)
        img.load()
        return img
    except Exception as e:
        raise ParseError("图片无法打开：%s: %s" % (type(e).__name__, e))


def slice_image(path: str, out_dir: str, chunk: int = IMAGE_CHUNK,
                overlap: int = IMAGE_OVERLAP) -> list[str]:
    """竖切成若干 PNG，返回切片路径列表（带重叠，避免切断文字行）。"""
    img = _open_image(path)
    w, h = img.size
    if max(w, h) > IMAGE_MAX_SIDE:
        scale = IMAGE_MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), img.LANCZOS)
        w, h = img.size
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    y, idx = 0, 1
    while y < h:
        box = (0, y, w, min(y + chunk, h))
        p = os.path.join(out_dir, "part%02d.png" % idx)
        img.crop(box).save(p)
        paths.append(p)
        if y + chunk >= h:
            break
        y += chunk - overlap
        idx += 1
    return paths


def _norm_line(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def _agree(a: list[str], b: list[str]) -> tuple[float, list[str]]:
    """两遍结果的一致率与仅出现在一遍里的行。"""
    na = [_norm_line(x) for x in a if _norm_line(x)]
    nb = [_norm_line(x) for x in b if _norm_line(x)]
    if not na and not nb:
        return 1.0, []
    sa, sb = set(na), set(nb)
    hit = len(sa & sb)
    base = max(len(sa), len(sb)) or 1
    only = sorted(sa ^ sb)
    return hit / base, only


def _build_paragraphs(lines, gap_ratio: float = 0.6) -> list[str]:
    """按行距把 OCR 行合并成段落。"""
    if not lines:
        return []
    heights = [h for _t, _s, _y, _x, h in lines if h > 0] or [20]
    med = statistics.median(heights)
    paras, buf, prev = [], [], None
    for text, _score, y, _x, h in lines:
        text = text.strip()
        if not text:
            continue
        if prev is not None:
            gap = y - (prev[2] + prev[4])
            if gap > gap_ratio * med:
                if buf:
                    paras.append("".join(buf))
                buf = []
        buf.append(text)
        prev = (text, _score, y, _x, h)
    if buf:
        paras.append("".join(buf))
    return paras


def parse_image(path: str, work_dir: str, emit=None) -> tuple[str, dict]:
    """图片 → (逐字转写 md, 元信息)。OCR 不可用时报 OcrUnavailable。"""
    _prepare_model_env()
    try:
        from .ocr_engine import kind_label, make_ocr, ocr_lines
    except ImportError as e:
        raise OcrUnavailable("OCR 组件未安装：%s" % e)

    slice_dir = os.path.join(work_dir, "slices")
    slices = slice_image(path, slice_dir)
    if emit:
        emit("step", label="切图", detail="%d 片（%dpx/片，重叠 %dpx）"
             % (len(slices), IMAGE_CHUNK, IMAGE_OVERLAP))

    try:
        engine, kind = make_ocr()
    except ImportError as e:
        raise OcrUnavailable("OCR 组件未安装（rapidocr 或 paddleocr）：%s" % e)
    except Exception as e:
        msg = str(e)
        if "PermissionError" in type(e).__name__ or "拒绝访问" in msg or "WinError 5" in msg:
            raise ParseError(
                "OCR 模型缓存目录不可写（%s）。已改用项目内目录 %s，仍失败请检查该目录权限，"
                "或在 .env 里显式设置 PADDLE_PDX_CACHE_HOME 指向一个可写目录。原始错误：%s"
                % (MODEL_DIR, MODEL_DIR, msg[:160]))
        if "download" in msg.lower() or "model source" in msg.lower():
            raise ParseError(
                "OCR 模型下载失败。原因通常是：①服务器不能出网（需能访问 modelscope.cn 与 *.bcebos.com）；"
                "②模型缓存目录不可写（设置 PADDLE_PDX_CACHE_HOME 到可写目录）。"
                "可先在能联网的机器上预下载模型目录再拷进来。原始错误：%s" % msg[:200])
        raise ParseError("OCR 引擎初始化失败：%s: %s" % (type(e).__name__, msg[:200]))
    if emit:
        emit("step", label="OCR 引擎", detail=kind_label(kind))

    from PIL import Image
    all_lines, per_slice = [], []
    agreements, uncertain = [], []
    for i, sp in enumerate(slices, 1):
        lines_a = ocr_lines(engine, kind, sp)
        # 第二遍：放大 1.2 倍再识别（不同尺度下仍一致，说明结果可信）
        tmp = os.path.join(slice_dir, "_scaled.png")
        with Image.open(sp) as im:
            im = im.resize((int(im.width * OCR_SCALES[-1]), int(im.height * OCR_SCALES[-1])),
                           Image.LANCZOS)
            im.save(tmp)
        lines_b = ocr_lines(engine, kind, tmp)
        rate, only = _agree([x[0] for x in lines_a], [x[0] for x in lines_b])
        agreements.append((i, rate))
        uncertain.extend(only)
        per_slice.append(lines_a)
        all_lines.extend(lines_a)
        if emit:
            emit("step", label="识别第 %d/%d 片" % (i, len(slices)),
                 detail="%d 行｜两遍一致率 %.3f" % (len(lines_a), rate))
        os.remove(tmp)

    worst = min(r for _i, r in agreements)
    uncertain_sorted = None
    if worst < OCR_MIN_AGREE:
        uncertain_sorted = sorted(uncertain)
        if emit:
            emit("step", label="需要注意", detail="有 %d 行两遍结果不一致，已列入待复核"
                 % len(uncertain_sorted))

    paras = []
    for lines in per_slice:
        paras.extend(_build_paragraphs(lines))

    md_parts = []
    for p in paras:
        md_parts.append(p)
        md_parts.append("")
    md = "\n".join(md_parts).strip() + "\n"

    scores = [s for lines in per_slice for _t, s, _y, _x, _h in lines]
    stats = {
        "kind": "image",
        "filename": os.path.basename(path),
        "slices": len(slices),
        "ocr_lines": len(all_lines),
        "paragraphs": len(paras),
        "mean_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "min_score": round(min(scores), 3) if scores else 0.0,
        "agree_worst": round(worst, 4),
        "agree_threshold": OCR_MIN_AGREE,
        "needs_review": (uncertain_sorted or [])[:50],
        "identical": False,      # 图片没有文字层，无法机器自证
    }
    return md, stats

