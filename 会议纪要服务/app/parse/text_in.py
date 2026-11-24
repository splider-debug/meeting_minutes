# -*- coding: utf-8 -*-
"""纯文本解析：严格解码 + 乱码率判定。

对应需求："输入文件支持…其它格式按照文本类格式解析（有乱码解析不出来要报错）"。
铁律：绝不用 errors="ignore" / "replace"，否则乱码也能出稿。
"""
import os

from ..config import GARBAGE_MAX
from ..errors import ParseError

# 顺序有讲究：gb18030 几乎能吃下所有字节，所以放在最后
CANDIDATES = ("utf-8-sig", "utf-8", "utf-16", "gb18030")

BOMS = (
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
)


def looks_binary(raw: bytes) -> bool:
    """二进制特征：出现 NUL，或控制字符占比过高。

    注意：UTF-16 文本里天然含 NUL，所以必须先做 BOM 判定再走这里。
    """
    sample = raw[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    ctrl = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return ctrl / len(sample) > 0.02


def garbage_ratio(text: str) -> float:
    """乱码特征占比：替换符、非法控制符、私用区字符。"""
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        o = ord(ch)
        if ch == "\ufffd" or (o < 32 and ch not in "\t\n\r") or 0xE000 <= o <= 0xF8FF:
            bad += 1
    return bad / len(text)


def decode_strict(raw: bytes) -> tuple[str, str]:
    """返回 (文本, 编码)。全部候选失败或乱码率超阈值 → ParseError。"""
    # 1) BOM 优先：UTF-16 文本里天然含 NUL 字节，不能先按"二进制"判掉
    for bom, enc in BOMS:
        if raw.startswith(bom):
            try:
                text = raw.decode(enc)                 # 严格模式
            except UnicodeDecodeError as e:
                raise ParseError("检测到 %s 的 BOM，但解码失败：%s" % (enc, e))
            ratio = garbage_ratio(text)
            if ratio <= GARBAGE_MAX:
                return text, enc
            raise ParseError("按 %s 解码后乱码率 %.2f%%（阈值 %.2f%%），文件可能已损坏。"
                             % (enc, ratio * 100, GARBAGE_MAX * 100))

    # 2) 二进制筛查
    if looks_binary(raw):
        raise ParseError("文件是二进制内容（含 NUL 或大量控制字符），不像文本。"
                         "若这是 PDF/Excel/图片，请改用对应格式或先转成文本。")

    # 3) 逐个候选严格解码
    tried = []
    for enc in CANDIDATES:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            tried.append(enc)
            continue
        ratio = garbage_ratio(text)
        if ratio <= GARBAGE_MAX:
            return text, enc
        tried.append("%s(乱码 %.2f%%)" % (enc, ratio * 100))

    raise ParseError("无法解码该文件（已尝试 %s）。请另存为 UTF-8 后重试；"
                     "若原文件是 PDF/Excel/压缩包，请先转成文本。" % "、".join(tried))


def parse_text(path: str) -> tuple[str, dict]:
    """读文本文件 → (正文, 元信息)。"""
    with open(path, "rb") as f:
        raw = f.read()
    text, enc = decode_strict(raw)
    text = (text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
            .lstrip("\ufeff"))
    return text, {"encoding": enc, "bytes": len(raw), "chars": len(text),
                  "garbage_ratio": round(garbage_ratio(text), 6),
                  "filename": os.path.basename(path)}


def to_markdown(text: str, title: str) -> str:
    """把纯文本包成 markdown：不做结构推断，只保证内容一字不少。"""
    body = text.strip("\n")
    return "# %s\n\n%s\n" % (title, body)
