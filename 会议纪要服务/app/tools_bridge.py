# -*- coding: utf-8 -*-
"""以"库"的方式调用 tools/ 里的脚本（零依赖、只用 stdlib）。

为什么不用 subprocess：服务里要拿它们的输出做事件流，进程内调用最省事，
也避免 Windows 控制台编码问题。
"""
import contextlib
import io
import sys

from .config import TOOLS_DIR

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import docx2md        # noqa: E402
import md2docx        # noqa: E402
import outline_docx   # noqa: E402
import verify_docx    # noqa: E402

__all__ = ["docx2md", "md2docx", "outline_docx", "verify_docx", "run_main", "TOOLS_VERSION"]


def _read_tools_version() -> str:
    try:
        return (TOOLS_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unversioned"


TOOLS_VERSION = _read_tools_version()


def run_main(fn, argv) -> tuple[int, str]:
    """跑某个脚本的 main()，返回 (退出码, 它打印的全部输出)。"""
    old_argv = sys.argv
    buf = io.StringIO()
    code = 0
    try:
        sys.argv = ["script"] + list(argv)
        with contextlib.redirect_stdout(buf):
            fn()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old_argv
    return code, buf.getvalue()
