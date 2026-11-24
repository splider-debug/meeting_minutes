# -*- coding: utf-8 -*-
"""图片解析：切片 → OCR → 去重 / 分栏还原 / 断段 → 逐字转写 markdown。

三个必要处理（都是实测踩出来的）：
  1. 切片有 120px 重叠 → 相邻片首尾会重复，必须去重，否则同一段出现两遍；
  2. 长截图常见左右双栏卡片 → 按 x 聚类分栏，读完左栏再读右栏，否则两栏文字交错；
  3. 换尺度二次识别会出现"行列切分不同"的假差异 → 一致率改按字符级相似度算，
     且默认只对低置信度切片做第二遍（省一半时间）。
"""
import difflib
import os
import statistics

from ..config import (DATA_DIR, IMAGE_CHUNK, IMAGE_MAX_SIDE, IMAGE_OVERLAP,
                      MODEL_DIR, OCR_MIN_AGREE, OCR_RECHECK_SCORE, OCR_SECOND_PASS)
from ..errors import OcrUnavailable, ParseError


def _prepare_model_env() -> None:
    """把 OCR 缓存目录钉在项目内（RapidOCR 不需要，但保留以兼容 paddle 引擎）。"""
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", MODEL_DIR)
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


# ---------------------------------------------------------------- 后处理
def _norm_line(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def _similar(a: str, b: str) -> float:
    """字符级相似度：对"换尺度后行列切分不同"比逐行集合比较稳得多。"""
    na, nb = _norm_line(a), _norm_line(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# 行首噪声：OCR 常把项目符号识别成这些字符
_LEAD_JUNK = "。．、，,）)]】》>"
_CJK = range(0x3400, 0xA000)


def _clean_line(text: str) -> str:
    """去掉行首误识别的项目符号（。 ） , 或单独一个 0/o 直接跟汉字）。"""
    t = text.strip()
    while t and t[0] in _LEAD_JUNK:
        t = t[1:].lstrip()
    if len(t) >= 2 and t[0] in "0oO〇" and ord(t[1]) in _CJK:
        t = t[1:].lstrip()
    return t


def _drop_overlap_by_y(lines: list, overlap: int, is_first: bool) -> list:
    """按像素重叠区裁掉重复行。

    比"文本前缀比对"可靠：分栏重排之后重复行在文本里已不连续，
    但它们在切片里的 y 坐标一定落在开头 overlap 像素内。
    """
    if is_first or overlap <= 0:
        return lines
    keep = []
    for text, score, y, x, h in lines:
        if y + h * 0.5 < overlap:          # 行中心落在重叠区 → 上一片已收录
            continue
        keep.append((text, score, y, x, h))
    return keep


def _dedupe_across_slices(acc: list, lines: list, tail: int = 15) -> list:
    """窗口内文本去重：本片开头若干行若已出现在上一片尾部，丢掉。"""
    if not acc:
        return lines
    prev_tail = [_norm_line(t) for t, _s, _y, _x, _h in acc[-tail:]]
    out = []
    for i, item in enumerate(lines):
        cur = _norm_line(item[0])
        if i < 10 and len(cur) >= 6 and cur in prev_tail:
            continue
        out.append(item)
    return out


def _split_columns(lines: list, slice_width: int) -> list:
    """左右双栏卡片还原成"左栏读完再读右栏"。

    判定：x 起点分成两簇（间隙 > 12% 宽度），两簇各 ≥3 行，且 y 区间有实质重叠。
    """
    if len(lines) < 6 or slice_width <= 0:
        return lines
    xs = sorted(x for _t, _s, _y, x, _h in lines)
    gap_min = slice_width * 0.12
    best_gap, best_at = 0, None
    for i in range(len(xs) - 1):
        g = xs[i + 1] - xs[i]
        if g > best_gap:
            best_gap, best_at = g, (xs[i] + xs[i + 1]) / 2
    if best_gap < gap_min or best_at is None:
        return lines
    left = [l for l in lines if l[3] < best_at]
    right = [l for l in lines if l[3] >= best_at]
    if len(left) < 3 or len(right) < 3:
        return lines
    ly = [l[2] for l in left]
    ry = [l[2] for l in right]
    overlap = min(max(ly), max(ry)) - max(min(ly), min(ry))
    if overlap < min(max(ly) - min(ly), max(ry) - min(ry)) * 0.5:
        return lines                      # 只是"标题靠左正文居中"，不是双栏
    return (sorted(left, key=lambda r: (r[2], r[3]))
            + sorted(right, key=lambda r: (r[2], r[3])))


def _clean_lines(lines: list) -> list:
    """清理后丢掉空行，以及"只剩一个符号"的残行（项目符号被单独识别出来的情况）。"""
    out = []
    for l in lines:
        t = l[0].strip()
        if not t:
            continue
        if len(t) == 1 and not t.isalnum():
            continue
        out.append((t, l[1], l[2], l[3], l[4]))
    return out


def _build_lines(lines, gap_ratio: float = 1.2) -> list[str]:
    """一行一条 markdown 行；行距明显变大才插空行（避免把不同条内容粘成一句）。"""
    if not lines:
        return []
    heights = [h for _t, _s, _y, _x, h in lines if h > 0] or [20]
    med = statistics.median(heights)
    out, prev = [], None
    for text, _score, y, _x, h in lines:
        text = text.strip()
        if not text:
            continue
        if prev is not None and y - (prev[2] + prev[4]) > gap_ratio * med:
            out.append("")
        out.append(text)
        prev = (text, _score, y, _x, h)
    return out


def _to_markdown(lines: list[str]) -> str:
    """空行分隔的段落 → markdown（段内换行保留，段间空行）。"""
    blocks, buf = [], []
    for line in lines:
        if line == "":
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return "\n\n".join(b for b in blocks if b.strip()) + "\n"


# ---------------------------------------------------------------- 主流程
def parse_image(path: str, work_dir: str, emit=None) -> tuple[str, dict]:
    """图片 → (逐字转写 md, 元信息)。OCR 不可用时报 OcrUnavailable。"""
    _prepare_model_env()
    try:
        from .ocr_engine import kind_label, make_ocr, ocr_lines
    except ImportError as e:
        raise OcrUnavailable("OCR 组件未安装：%s" % e)

    slice_dir = os.path.join(work_dir, "slices")
    slices = slice_image(path, slice_dir)
    slice_w = _open_image(slices[0]).size[0]
    if emit:
        emit("step", label="切图", detail="%d 片（%dpx/片，重叠 %d，宽 %d）"
             % (len(slices), IMAGE_CHUNK, IMAGE_OVERLAP, slice_w))

    try:
        engine, kind = make_ocr()
    except ImportError as e:
        raise OcrUnavailable("OCR 组件未安装（RapidOCR 或 paddleocr）：%s" % e)
    except Exception as e:
        msg = str(e)
        if "PermissionError" in type(e).__name__ or "拒绝访问" in msg or "WinError 5" in msg:
            raise ParseError(
                "OCR 缓存目录不可写（%s）。RapidOCR 不需要下载模型；若用 paddle 引擎，"
                "请把 PADDLE_PDX_CACHE_HOME 指到可写目录。原始错误：%s" % (MODEL_DIR, msg[:160]))
        raise ParseError("OCR 引擎初始化失败：%s: %s" % (type(e).__name__, msg[:200]))
    if emit:
        emit("step", label="OCR 引擎", detail=kind_label(kind))

    all_lines, agreements, uncertain = [], [], []
    for i, sp in enumerate(slices, 1):
        lines = ocr_lines(engine, kind, sp)
        scores = [s for _t, s, _y, _x, _h in lines] or [0.0]
        mean_score = sum(scores) / len(scores)

        note = "｜单遍（均分 %.2f）" % mean_score
        do_second = (OCR_SECOND_PASS == "always"
                     or (OCR_SECOND_PASS == "auto" and mean_score < OCR_RECHECK_SCORE))
        if do_second:
            from PIL import Image
            tmp = os.path.join(slice_dir, "scaled.png")
            with Image.open(sp) as im:
                im = im.resize((int(im.width * 1.2), int(im.height * 1.2)), Image.LANCZOS)
                im.save(tmp)
            lines2 = ocr_lines(engine, kind, tmp)
            os.remove(tmp)
            rate = _similar("\n".join(t for t, *_ in lines), "\n".join(t for t, *_ in lines2))
            agreements.append(rate)
            if rate < OCR_MIN_AGREE:
                sa = {_norm_line(t) for t, *_ in lines}
                sb = {_norm_line(t) for t, *_ in lines2}
                uncertain.extend(sorted(sa ^ sb))
            note = "｜复核两遍 %.3f" % rate

        lines = _clean_lines([( _clean_line(t), s, y, x, h) for t, s, y, x, h in lines])
        lines = _drop_overlap_by_y(lines, IMAGE_OVERLAP, is_first=(i == 1))
        lines = _dedupe_across_slices(all_lines, lines)
        lines = _split_columns(lines, slice_w)
        all_lines.extend(lines)
        if emit:
            emit("step", label="识别第 %d/%d 片" % (i, len(slices)),
                 detail="%d 行%s" % (len(lines), note))

    worst = min(agreements) if agreements else 1.0
    if agreements and worst < OCR_MIN_AGREE and emit:
        emit("step", label="需要注意",
             detail="复核切片一致率最低 %.3f，%d 行待人工核对" % (worst, len(uncertain)))

    lines_md = _build_lines(all_lines)
    md = _to_markdown(lines_md)

    scores = [s for _t, s, _y, _x, _h in all_lines]
    stats = {
        "kind": "image",
        "filename": os.path.basename(path),
        "slices": len(slices),
        "ocr_lines": len(all_lines),
        "paragraphs": md.count("\n\n") + 1,
        "mean_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "min_score": round(min(scores), 3) if scores else 0.0,
        "agree_worst": round(worst, 4),
        "agree_checked": len(agreements),
        "agree_threshold": OCR_MIN_AGREE,
        "needs_review": uncertain[:50],
        "identical": False,      # 图片没有文字层，无法机器自证
    }
    return md, stats
