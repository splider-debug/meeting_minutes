# -*- coding: utf-8 -*-
"""OCR 引擎适配层：兼容三种引擎，业务代码只调 ocr_lines()。

为什么要有这层：
  · rapidocr（ONNX 运行时）：不依赖 paddle，模型打包在 wheel 里，安装即用；
  · paddleocr 3.x：本机 Python 3.13 上 paddle 只有 3.x，与 paddlex 3.7 存在兼容问题；
  · paddleocr 2.x：服务器 Python 3.11 + paddle 2.6 的成熟组合。

优先级：rapidocr → paddleocr 3.x → paddleocr 2.x。
"""
from __future__ import annotations

KIND_RAPID = "rapid"
KIND_PADDLE3 = "paddle3"
KIND_PADDLE2 = "paddle2"

_KIND_LABEL = {KIND_RAPID: "RapidOCR（ONNX）",
               KIND_PADDLE3: "paddleocr 3.x",
               KIND_PADDLE2: "paddleocr 2.x"}


def _try_rapid():
    """返回 (engine, kind)；装了哪个包就用哪个 API。"""
    try:                                    # 旧包名（1.x，模型内置）
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR(), KIND_RAPID
    except Exception:
        pass
    try:                                    # 新包名（2.x/3.x）
        from rapidocr import RapidOCR
        return RapidOCR(), KIND_RAPID
    except Exception:
        return None, None


def _try_paddle():
    from paddleocr import PaddleOCR
    # 3.x 参数
    try:
        return PaddleOCR(use_doc_orientation_classify=False,
                         use_doc_unwarping=False,
                         use_textline_orientation=False,
                         lang="ch"), KIND_PADDLE3
    except TypeError:
        pass
    # 2.x 参数
    return PaddleOCR(use_angle_cls=False, lang="ch"), KIND_PADDLE2


def make_ocr():
    """返回 (engine, kind)。都不可用时抛 ImportError，由上层转成 OcrUnavailable。"""
    engine, kind = _try_rapid()
    if engine is not None:
        return engine, kind
    return _try_paddle()


def kind_label(kind: str) -> str:
    return _KIND_LABEL.get(kind, kind)


def _norm_rapid_result(raw) -> list[tuple]:
    """RapidOCR 返回结构在新旧版本间略有差异，统一成 [(文本, 分数, y, x, h)]。"""
    lines = []
    if raw is None:
        return lines
    # 旧版：[[box, text, score], ...]；box = [[x,y],...4点]
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)) \
            and len(raw[0]) >= 3 and isinstance(raw[0][1], str):
        for item in raw:
            box, text, score = item[0], item[1], item[2]
            ys = [float(p[1]) for p in box]
            xs = [float(p[0]) for p in box]
            lines.append((str(text), float(score), int(min(ys)), int(min(xs)),
                          int(max(ys) - min(ys))))
        return lines
    # 新版：dict / 带 .txts .boxes .scores 的对象
    txts = boxes = scores = None
    for attr in ("txts", "texts"):
        if hasattr(raw, attr):
            txts = getattr(raw, attr)
            break
    if txts is None and isinstance(raw, dict):
        txts = raw.get("txts") or raw.get("rec_texts")
        boxes = raw.get("boxes") or raw.get("rec_boxes")
        scores = raw.get("scores") or raw.get("rec_scores")
    if txts is not None:
        boxes = boxes if boxes is not None else getattr(raw, "boxes", None)
        scores = scores if scores is not None else getattr(raw, "scores", None)
        for i, t in enumerate(txts):
            b = boxes[i] if boxes is not None and i < len(boxes) else None
            s = scores[i] if scores is not None and i < len(scores) else 0.0
            if b is None:
                lines.append((str(t), float(s), 0, 0, 0))
                continue
            flat = [float(v) for v in (b if not isinstance(b[0], (list, tuple)) else
                                       [c for p in b for c in p])]
            xs, ys = flat[0::2], flat[1::2]
            lines.append((str(t), float(s), int(min(ys)), int(min(xs)),
                          int(max(ys) - min(ys))))
    return lines


def ocr_lines(engine, kind: str, image_path: str):
    """统一返回 [(文本, 置信度, y, x, h)]，按 y 再按 x 排序（阅读顺序）。"""
    if kind == KIND_RAPID:
        out = engine(str(image_path))
        raw = out[0] if isinstance(out, tuple) else out
        return sorted(_norm_rapid_result(raw), key=lambda r: (r[2], r[3]))

    if kind == KIND_PADDLE3:
        pages = engine.predict(str(image_path))
        lines = []
        for page in pages:
            for text, score, box in zip(page["rec_texts"], page["rec_scores"], page["rec_boxes"]):
                x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
                lines.append((str(text), float(score), y1, x1, abs(y2 - y1)))
        return sorted(lines, key=lambda r: (r[2], r[3]))

    res = engine.ocr(str(image_path), cls=False)[0] or []
    lines = []
    for box, (text, score) in res:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        lines.append((str(text), float(score), int(min(ys)), int(min(xs)),
                      int(max(ys) - min(ys))))
    return sorted(lines, key=lambda r: (r[2], r[3]))


def ocr_text(engine, kind: str, image_path: str) -> str:
    return "\n".join(t for t, _s, _y, _x, _h in ocr_lines(engine, kind, image_path))
