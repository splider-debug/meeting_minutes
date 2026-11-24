# -*- coding: utf-8 -*-
"""docx 解析：直接复用 tools/docx2md.py，保留它的 identical 自证。

这是整条链路里唯一"强可自证"的输入类型：
    docx 全部 w:t 拼接  ==  md 去掉结构标记后的全文
不一致就报错，绝不带着偏差往下走。
"""
import os
import zipfile

from ..errors import ParseError, TypeMismatch
from ..tools_bridge import docx2md

KEEP_SYMBOLS = False   # 行首 ▪/• 转成 "- "，与 SOP §7.2 一致


def _check_zip(path: str) -> None:
    if not zipfile.is_zipfile(path):
        raise TypeMismatch("文件扩展名是 .docx，但不是一个有效的 zip 包（可能已损坏或被改名）。")
    try:
        with zipfile.ZipFile(path) as z:
            total = sum(i.file_size for i in z.infolist())
            if total > 200 * 1024 * 1024 or len(z.infolist()) > 2000:
                raise ParseError("docx 解压后过大（%.0f MB / %d 个条目），疑似异常文件，已拒绝。"
                                 % (total / 1024 / 1024, len(z.infolist())))
    except zipfile.BadZipFile:
        raise TypeMismatch("docx 无法解压（zip 结构损坏）。")


def parse_docx(path: str) -> tuple[str, dict]:
    """docx → (逐字转写 md, 元信息)。校验不通过直接抛错。"""
    _check_zip(path)
    try:
        md, doc = docx2md.convert(path, KEEP_SYMBOLS, False, False)
    except Exception as e:                     # 加密文档、缺部件等
        raise ParseError("docx 读取失败：%s: %s" % (type(e).__name__, e))

    ok, msg = docx2md.verify(doc, md)
    if not ok:
        raise ParseError("逐字转写校验未通过，为防改动原文已中止。%s" % msg)

    body = doc.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body")
    n_tbl = len(list(body.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl")))
    n_head = sum(1 for line in md.split("\n") if line.startswith("#"))

    return md, {
        "kind": "docx",
        "filename": os.path.basename(path),
        "identical": True,
        "identical_msg": msg,
        "chars": len(msg.split("（")[1].split(" ")[0]) if "（" in msg else -1,
        "headings": n_head,
        "tables": n_tbl,
        "md_lines": md.count("\n"),
    }
