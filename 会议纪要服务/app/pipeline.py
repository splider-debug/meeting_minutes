# -*- coding: utf-8 -*-
"""流水线（M1 范围）：接收 → 解析 → 画像 → 渲染 → 自检 → 打包。

阶段编号与技术方案 §四 D5 一致，后续 M3 插入 P3 结构设计 / P4 写正文 / P6 守恒校验
即可，不改调用方。
"""
import os
import re
import shutil
import time

from . import config
from .errors import AppError, FileTooLarge, ParseError, UnsupportedType, VerifyError
from .parse.docx_in import parse_docx
from .parse.image_in import parse_image
from .parse.text_in import parse_text, to_markdown
from .render import outline_docx_file, render_docx, skeleton_md, verify_docx_file

STAGES = [
    ("P0", "接收与校验"),
    ("P1", "解析输入"),
    ("P2", "材料画像"),
    ("P3", "结构设计"),
    ("P4", "写正文"),
    ("P5", "组装纪要"),
    ("P6", "守恒校验"),
    ("P7", "渲染 docx"),
    ("P8", "结构自检"),
    ("P9", "打包产物"),
]

# 主标题清洗用：这些前缀不属于主题本身（SOP §3 命名规则）
_LEAD_NOISE = ("会议纪要", "会议记录", "整理稿", "纪要")
# 形如 11-19 / 2026-11-19 / 2026.11.19 / 2026年11月19日 的前缀，
# 且后面必须紧跟分隔符——避免把"2026年度经营分析"这种真标题误伤
_DATE_PREFIX = re.compile(
    r"^\s*\d{2,4}\s*[-_.年]\s*\d{1,2}(?:\s*[-_.月]\s*\d{1,2}\s*日?)?(?=[\s_\-—－．.,，])")
# 裸日期码：261119（YYMMDD）或 1118（MMDD），后面必须跟分隔符才算前缀
_DATE_CODE = re.compile(r"^\d{6}(?=[\s_\-—－．.,，])|^\d{4}(?=[\s_\-—－．.,，])")
_SEP = " -_—－．.,，:："


def clean_title(stem: str) -> str:
    """文件名 → 主标题：剥掉"纪要""日期前缀"等噪声，保留会议主题。"""
    t = stem.replace("_", " ").strip()
    for _ in range(6):
        before = t
        for noise in _LEAD_NOISE:
            if t.startswith(noise):
                t = t[len(noise):].lstrip(_SEP).strip()
        m = _DATE_PREFIX.match(t) or _DATE_CODE.match(t)
        if m:
            t = t[m.end():].lstrip(_SEP).strip()
        if t and t[0] in "-—－":
            t = t.lstrip("-—－").strip()
        if t == before:
            break
    return t if len(t) >= 2 else stem


def _noop(*_a, **_k):
    return None


def _llm_stats() -> dict:
    try:
        from . import llm
        return llm.stats()
    except Exception:
        return {"calls": 0}


def _dispatch(path: str, work_dir: str, emit) -> tuple[str, dict]:
    ext = os.path.splitext(path)[1].lower()
    if ext in config.DOCX_EXT:
        return parse_docx(path)
    if ext in config.TEXT_EXT:
        text, meta = parse_text(path)
        return text, {"kind": "text", **meta}
    if ext in config.IMAGE_EXT:
        return parse_image(path, work_dir, emit=emit)
    raise UnsupportedType(
        "不支持的文件类型 %s。当前支持：%s"
        % (ext or "(无扩展名)", "、".join(sorted(config.SUPPORTED_EXT))))


def run_task(input_path: str, out_dir: str, title: str | None = None, emit=None,
             use_llm: bool | None = None, guard_mode: str | None = None) -> dict:
    """跑一遍全流程。返回结果字典；失败抛 AppError（带 code）。

    use_llm=True 走模型（P3-P6），False 只出骨架稿；None 时看配置/是否有 key。
    guard_mode: strict（默认，守恒校验不过就中止）/ warn（只告警继续）。
    """
    emit = emit or _noop
    if use_llm is None:
        use_llm = os.environ.get("ENABLE_LLM", "1") not in ("0", "false", "False")
        if use_llm:
            from . import llm as _llm
            use_llm = bool(_llm.api_key())
    guard_mode = guard_mode or os.environ.get("GUARD_MODE", "strict")
    t_all = time.time()
    t_stage = {}

    def stage(code: str, state: str, extra: str = ""):
        name = dict(STAGES)[code]
        ev = {"event": "stage", "stage": code, "name": name, "state": state,
              "elapsed": round(time.time() - t_stage.get(code, time.time()), 2)}
        if extra:
            ev["detail"] = extra
        emit("**", **ev)

    # ---------- P0 接收与校验 ----------
    t_stage["P0"] = time.time()
    stage("P0", "running")
    if not os.path.isfile(input_path):
        raise ParseError("文件不存在：%s" % input_path)
    size = os.path.getsize(input_path)
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise FileTooLarge("文件 %.1f MB，超过上限 %d MB" % (size / 1024 / 1024, config.MAX_UPLOAD_MB))
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in config.SUPPORTED_EXT:
        raise UnsupportedType("不支持的文件类型 %s" % (ext or "(无扩展名)"))
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "work")
    os.makedirs(work_dir, exist_ok=True)
    emit("step", label="接收文件", detail="%s（%.1f MB）" % (os.path.basename(input_path), size / 1024 / 1024))
    stage("P0", "done")

    # ---------- P1 解析 ----------
    t_stage["P1"] = time.time()
    stage("P1", "running")
    body, meta = _dispatch(input_path, work_dir, emit)
    title = title or clean_title(os.path.splitext(os.path.basename(input_path))[0])
    if meta.get("kind") in ("text", "docx"):
        md = body if meta.get("kind") == "docx" else to_markdown(body, title)
        if meta.get("kind") == "text":
            emit("step", label="编码识别", detail="%s，乱码率 %.3f%%"
                 % (meta["encoding"], meta["garbage_ratio"] * 100))
    else:
        md = body
    md_path = os.path.join(out_dir, "01_逐字转写.md")
    with open(md_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    if meta.get("identical"):
        emit("step", label="一致性校验", detail=meta["identical_msg"])

    # 骨架稿：逐字稿里可能有原文自带的 ** 等 markdown 冲突字符，渲染前做一次安全化。
    # 模型可用时它是"兜底交付稿"，模型不可用时它就是唯一产物。
    skel_path = os.path.join(out_dir, "02_纪要_骨架.md")
    with open(skel_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(skeleton_md(md, title))
    emit("step", label="骨架稿", detail=os.path.basename(skel_path))
    stage("P1", "done")

    # ---------- P2 材料画像 ----------
    t_stage["P2"] = time.time()
    stage("P2", "running")
    profile = {
        "chars": len(md),
        "lines": md.count("\n"),
        "headings": sum(1 for l in md.split("\n") if l.startswith("#")),
        "table_rows": sum(1 for l in md.split("\n") if l.strip().startswith("|")),
    }
    emit("step", label="材料画像",
         detail="%d 字符 / %d 行 / 标题 %d / 表格行 %d"
         % (profile["chars"], profile["lines"], profile["headings"], profile["table_rows"]))
    stage("P2", "done")

    # ---------- P3 结构设计 ----------
    t_stage["P3"] = time.time()
    stage("P3", "running")
    from . import structure as structure_mod
    blocks_raw = structure_mod.build_blocks(md)
    blocks, extras = structure_mod.split_extras(blocks_raw)
    if extras.get("ai"):
        emit("step", label="分离附录", detail="剥出 %d 条听记工具 AI 洞察（改放附录）" % len(extras["ai"]))
    if blocks_raw and len(blocks) != len(blocks_raw):
        emit("step", label="剥离元信息", detail="去掉 %d 个听记元信息块" % (len(blocks_raw) - len(blocks)))
    plan = structure_mod.design_plan(blocks, title, emit=emit, allow_llm=use_llm)
    if plan.get("title"):
        title = plan["title"].strip() or title
    plan_path = os.path.join(work_dir, "plan.json")
    structure_mod.dump_plan(plan, plan_path)
    stage("P3", "done")

    # ---------- P4 写正文 ----------
    t_stage["P4"] = time.time()
    stage("P4", "running")
    from . import writer as writer_mod
    bodies = writer_mod.write_all(plan, blocks, emit=emit, allow_llm=use_llm)
    writer_mod.save_drafts(bodies, os.path.join(work_dir, "draft"))
    stage("P4", "done")

    # ---------- P5 组装 ----------
    t_stage["P5"] = time.time()
    stage("P5", "running")
    from . import assemble as assemble_mod
    blocks_by_id = {b["id"]: b for b in blocks}
    final_md = assemble_mod.assemble(plan, bodies, blocks_by_id, title,
                                     source_note=meta.get("filename", ""),
                                     appendix_ai=extras.get("ai"))
    final_path = os.path.join(out_dir, "02_会议纪要.md")
    with open(final_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(final_md)
    emit("step", label="组装纪要", detail="%d 字 → %s"
         % (len(final_md), os.path.basename(final_path)))
    stage("P5", "done")

    # ---------- P6 守恒校验 ----------
    t_stage["P6"] = time.time()
    stage("P6", "running")
    from . import guard as guard_mod
    pieces = [b for b in bodies.values() if b]
    for t in plan.get("tables") or []:
        skip = 1 if (t["columns"] and t["columns"][0] in ("序号", "编号")) else 0
        for row in t["rows"]:
            pieces.append(" ".join(row[skip:]))
    num = guard_mod.check_numbers(md, pieces)
    st = guard_mod.check_structure(final_md)
    emit("step", label="数字守恒",
         detail=("通过（原文 %d 个数字全部对得上）" % num["checked"]) if num["ok"]
         else "发现 %d 个原文没有的数字：%s" % (len(num["missing"]), "、".join(num["missing"][:10])))
    emit("step", label="结构连续",
         detail="通过（%d 章 / %d 节 / %d 表）" % (st["chapters"], st["sections"], st["tables"])
         if st["ok"] else "；".join(st["problems"]))
    guard_ok = num["ok"] and st["ok"]
    if not guard_ok and guard_mode == "strict":
        raise VerifyError("守恒校验未通过，已中止交付。%s%s"
                          % ("数字：" + "、".join(num["missing"][:10]) + "。" if not num["ok"] else "",
                             "结构：" + "；".join(st["problems"]) if not st["ok"] else ""))
    stage("P6", "done")

    # ---------- P7 渲染 ----------
    t_stage["P7"] = time.time()
    stage("P7", "running")
    safe_title = title.replace("/", "_").replace("\\", "_")
    src_md = final_path if use_llm else skel_path
    if not use_llm:
        emit("step", label="渲染来源", detail="未启用模型，仅渲染骨架稿")
    docx_path = os.path.join(out_dir, "03_%s.docx" % safe_title)
    r = render_docx(src_md, docx_path, safe_title)
    emit("step", label="渲染 docx", detail="theme=%s，%.1f KB" % (r["theme"], r["bytes"] / 1024))
    stage("P7", "done")

    # ---------- P8 结构自检 ----------
    t_stage["P8"] = time.time()
    stage("P8", "running")
    v = verify_docx_file(src_md, docx_path)
    emit("step", label="verify_docx", detail=v["conclusion"])
    o = outline_docx_file(docx_path)
    emit("step", label="outline_docx", detail=o["stats"])
    stage("P8", "done")

    # ---------- P9 打包 ----------
    t_stage["P9"] = time.time()
    stage("P9", "running")
    import zipfile
    zip_path = os.path.join(out_dir, "result.zip")
    pack = [md_path, docx_path] + ([final_path] if use_llm else [skel_path])
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pack:
            z.write(p, os.path.basename(p))
    emit("step", label="打包", detail="%s（%.1f KB）" % (os.path.basename(zip_path),
                                                     os.path.getsize(zip_path) / 1024))
    stage("P9", "done")

    return {
        "title": title,
        "input": input_path,
        "out_dir": out_dir,
        "md": md_path,
        "skeleton": skel_path,
        "final_md": final_path if use_llm else None,
        "plan": plan,
        "guard": {"numbers": num, "structure": st, "mode": guard_mode, "ok": guard_ok},
        "llm": (_llm_stats() if use_llm else {"calls": 0}),
        "docx": docx_path,
        "zip": zip_path,
        "meta": meta,
        "profile": profile,
        "verify": v,
        "outline_stats": o["stats"],
        "outline_tree": o["tree"],
        "elapsed": round(time.time() - t_all, 2),
    }
