# -*- coding: utf-8 -*-
"""M1 命令行：解析 → 逐字转写 md → 渲染 docx → 结构自检。

用法：
    python run_m1.py <文件>                      # 输出到 runs/<时间>_<文件名>/
    python run_m1.py <文件> --out 我的输出目录
    python run_m1.py <文件> --title "自定义主标题"
    python run_m1.py <图片> --slice-only         # 只验证切片，不跑 OCR

退出码：0 成功 / 2 业务错误（带 code）/ 1 未预期异常
"""
import os
import sys
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.errors import AppError                # noqa: E402
from app.pipeline import run_task             # noqa: E402

ICON = {"stage": "▸", "step": "  ·"}


def make_printer():
    t0 = time.time()

    def emit(kind, **kw):
        if kind == "**":                       # 阶段事件
            state = kw.get("state")
            stage = kw.get("stage", "")
            name = kw.get("name", "")
            el = kw.get("elapsed", 0)
            detail = kw.get("detail", "")
            if state == "running":
                print("\n[%s] %s %s" % (stage, name, "…"))
            else:
                print("[%s] %s 完成（%.1fs）%s" % (stage, name, el, (" " + detail) if detail else ""))
        elif kind == "step":
            print("   · %-12s %s" % (kw.get("label", ""), kw.get("detail", "")))
        else:
            print("   ", kind, kw)
    return emit, lambda: round(time.time() - t0, 2)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2
    src = os.path.abspath(args[0])
    title = None
    out_dir = None
    for i, a in enumerate(argv):
        if a == "--title" and i + 1 < len(argv):
            title = argv[i + 1]
        if a == "--out" and i + 1 < len(argv):
            out_dir = os.path.abspath(argv[i + 1])

    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = os.path.splitext(os.path.basename(src))[0]
    out_dir = out_dir or os.path.join(HERE, "runs", "%s_%s" % (stamp, stem[:24]))

    print("输入：%s" % src)
    print("输出：%s" % out_dir)

    if "--slice-only" in flags:
        from app.parse.image_in import slice_image
        slices = slice_image(src, os.path.join(out_dir, "slices"))
        print("\n切片完成：%d 片" % len(slices))
        for p in slices[:5]:
            print("   %s（%.1f KB）" % (os.path.basename(p), os.path.getsize(p) / 1024))
        if len(slices) > 5:
            print("   … 其余 %d 片" % (len(slices) - 5))
        return 0

    emit, elapsed = make_printer()
    try:
        result = run_task(src, out_dir, title=title, emit=emit,
                          use_llm=(False if "--no-llm" in flags else None))
    except AppError as e:
        print("\n失败 [%s] %s" % (e.code, e.message))
        if e.retryable:
            print("（该错误可重试）")
        return 2
    except Exception:
        print("\n未预期异常：")
        traceback.print_exc()
        return 1

    print("\n" + "=" * 66)
    print("完成，用时 %.1fs" % result["elapsed"])
    print("主标题：%s" % result["title"])
    plan = result.get("plan") or {}
    if plan.get("chapters"):
        print("结构：  %d 章 / %d 节 / %d 表"
              % (len(plan["chapters"]),
                 sum(len(c["sections"]) for c in plan["chapters"]),
                 len(plan.get("tables") or [])))
    if result.get("final_md"):
        print("纪要源：%s" % os.path.basename(result["final_md"]))
    print("逐字转写：%s" % os.path.basename(result["md"]))
    print("交付物：  %s" % os.path.basename(result["docx"]))
    print("打包：    %s" % os.path.basename(result["zip"]))
    print("自检：    %s" % result["verify"]["conclusion"])
    g = result.get("guard") or {}
    if g:
        st = g.get("structure") or {}
        nu = g.get("numbers") or {}
        print("守恒：    %s（数字 %s；结构 %d 章/%d 节/%d 表）"
              % ("通过" if g.get("ok") else "未通过（%s）" % g.get("mode"),
                 "全部对得上" if nu.get("ok") else "多出 " + "、".join(nu.get("missing", [])[:6]),
                 st.get("chapters", 0), st.get("sections", 0), st.get("tables", 0)))
    s = result.get("llm") or {}
    if s.get("calls"):
        print("模型：    %d 次 / 输入 %d tok / 输出 %d tok"
              % (s.get("calls", 0), s.get("prompt_tokens", 0), s.get("completion_tokens", 0)))
    if result["meta"].get("identical"):
        print("保真：    %s" % result["meta"]["identical_msg"])
    elif result["meta"].get("kind") == "text":
        print("保真：    严格解码 %s，乱码率 %.3f%%（无逐字符自证，文本类输入靠解码校验）"
              % (result["meta"]["encoding"], result["meta"]["garbage_ratio"] * 100))
    else:
        print("保真：    图片输入无 identical，OCR 两遍一致率 %.3f（阈值 %.3f）%s"
              % (result["meta"].get("agree_worst", 0), result["meta"].get("agree_threshold", 0),
                 "，有 %d 行待复核" % len(result["meta"].get("needs_review", []))
                 if result["meta"].get("needs_review") else ""))
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
