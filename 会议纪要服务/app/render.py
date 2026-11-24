# -*- coding: utf-8 -*-
"""渲染层：只做 md → docx 与结构自检，全部调用 tools/ 里的既有实现。

版式（A4、页脚页码、表 N、字号层级、章号样式）由 tools/md2docx.py 决定，
这里不复制任何版式常量 —— 保证"服务产出的 docx"与"本地手工整理的 docx"同源。
"""
import os

from .config import RENDER_THEME
from .errors import RenderError, VerifyError
from .tools_bridge import md2docx, outline_docx, run_main, verify_docx

# 逐字稿可能是"原文自带 `**`"这类脏内容，直接渲染会被当成加粗、并让 verify 报残留。
# 骨架稿把它换成全角星号：视觉不变，markdown 语义消失，verify 也不会误判。
SAFE_STARS = "＊＊"


def skeleton_md(verbatim_md: str, title: str) -> str:
    """由逐字稿生成"渲染安全"的骨架稿 md：保留标题层级与正文，去掉 markdown 语义冲突。"""
    lines = []
    for line in verbatim_md.split("\n"):
        if line.startswith("|"):
            # 表格行原样保留（骨架阶段不重建表格）
            lines.append(line)
        else:
            lines.append(line.replace("**", SAFE_STARS))
    md = "\n".join(lines).strip() + "\n"
    if not md.lstrip().startswith("# "):
        md = "# %s\n\n%s" % (title, md)
    return md


def render_docx(md_path: str, docx_path: str, title: str) -> dict:
    """md → docx。失败抛 RenderError。"""
    code, out = run_main(md2docx.main,
                         [md_path, docx_path, title, "--theme", RENDER_THEME])
    if code != 0 or not os.path.isfile(docx_path):
        raise RenderError("渲染失败（md2docx 退出码 %s）：%s" % (code, out.strip()[-500:]))
    return {"theme": RENDER_THEME, "bytes": os.path.getsize(docx_path),
            "log": out.strip()}


def verify_docx_file(md_path: str, docx_path: str) -> dict:
    """结构自检。不通过抛 VerifyError —— 对应 SOP "交付稿必须 verify 0 退出"。"""
    code, out = run_main(verify_docx.main, [md_path, docx_path])
    if code != 0:
        raise VerifyError("结构自检未通过（verify_docx 退出码 %s）：\n%s" % (code, out.strip()))
    tail = [l for l in out.strip().splitlines() if l.strip()]
    return {"ok": True, "summary": tail[-2] if len(tail) >= 2 else "",
            "conclusion": tail[-1] if tail else "", "log": out.strip()}


def outline_docx_file(docx_path: str) -> dict:
    """标题树，用于判断层级与章号。"""
    code, out = run_main(outline_docx.main, [docx_path])
    lines = [l for l in out.strip().splitlines() if l.strip()]
    stats = lines[-1] if lines else ""
    return {"ok": code == 0, "tree": [l for l in lines if not l.startswith("---")],
            "stats": stats, "log": out.strip()}
