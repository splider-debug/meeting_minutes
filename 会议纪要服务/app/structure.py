# -*- coding: utf-8 -*-
"""P3 结构设计：逐字稿 → 内容块 → 章节树（JSON）+ 表格设计。

产出 plan 里每个 section 都带 blocks（原文块编号），P4 只按编号回取原文，
从机制上避免模型"自由发挥"时编造材料。
"""
import json
import re

from . import llm, prompt
from .errors import AppError

TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[—–\-~至]\s*\d{1,2}:\d{2}")
CN_NO = "一二三四五六七八九十"
CN_SUB = "一二三四五六七八九十"

# 听记工具塞进来的元信息（不属于会议内容，进了正文就是噪声）
META_RE = re.compile(r"(内容由.{0,16}生成|扫一扫|^钉钉\s*AI\s*听记$|^AI\s*听记$)")
STATS_RE = re.compile(r"^(议题数\s*[:：]\s*\d+.*|听记时长\s*[:：].*)$")
# 听记工具自动生成的分析意见：按 SOP 只能进附录，不能当参会人结论
AI_HEAD_RE = re.compile(r"^AI\s*洞察$")


def split_extras(blocks: list[dict]) -> tuple[list[dict], dict]:
    """把元信息与"AI 洞察"从正文块里剥出来，并对保留块重新编号。

    返回 (正文块, extras)。extras 里有：
      stats  —— 议题数 / 听记时长
      ai     —— AI 洞察条目（走附录）
    """
    kept, stats, ai = [], {}, []
    in_ai = False
    for b in blocks:
        t = b["text"].strip()
        if META_RE.search(t) or STATS_RE.match(t):
            if STATS_RE.match(t):
                stats.setdefault("raw", []).append(t)
            continue
        if b["kind"] == "heading":
            in_ai = bool(AI_HEAD_RE.search(re.sub(r"^#{1,6}\s+", "", t).strip()))
            if in_ai:
                continue
        if in_ai:
            for line in t.split("\n"):
                s = re.sub(r"^[-*•▪]\s*", "", line).strip()
                if s and not s.startswith("#"):
                    ai.append(s)
            continue
        kept.append(b)
    for i, b in enumerate(kept, 1):
        b["id"] = i
    return kept, {"stats": stats, "ai": ai}


def build_blocks(md: str) -> list[dict]:
    """把 md 按空行切成内容块；块号从 1 开始（跳过首个主标题）。"""
    lines = md.split("\n")
    blocks, buf = [], []
    first_heading_skipped = False

    def flush():
        if not buf:
            return
        text = "\n".join(buf).strip()
        if not text:
            buf.clear()
            return
        level = 0
        m = re.match(r"^(#{1,6})\s+(.*)$", text.split("\n")[0])
        if m and len(buf) == 1:
            level = len(m.group(1))
        blocks.append({"id": len(blocks) + 1, "kind": "heading" if level else "text",
                       "level": level, "text": text,
                       "times": TIME_RE.findall(text)})
        buf.clear()

    for line in lines:
        if line.strip() == "":
            flush()
            continue
        if not first_heading_skipped and line.startswith("# "):
            first_heading_skipped = True       # 主标题由 plan 决定，不进块
            continue
        buf.append(line)
    flush()
    return blocks


def blocks_to_text(blocks: list[dict], per_block: int = 320, limit_blocks: int = 400) -> str:
    out = []
    for b in blocks[:limit_blocks]:
        tag = "标题" if b["kind"] == "heading" else "正文"
        text = re.sub(r"\s+", " ", b["text"])[:per_block]
        out.append("[%d]（%s）%s" % (b["id"], tag, text))
    return "\n".join(out)


def _norm_no(items, kind: str):
    """强制章号/节号按顺序重排，避免模型跳号。"""
    for i, it in enumerate(items):
        if kind == "chapter":
            it["no"] = CN_NO[i] if i < len(CN_NO) else str(i + 1)
        else:
            it["no"] = "（%s）" % (CN_SUB[i] if i < len(CN_SUB) else str(i + 1))
    return items


def _clean_plan(plan: dict, blocks: list[dict]) -> dict:
    n = len(blocks)
    chapters = []
    for ch in plan.get("chapters") or []:
        sections = []
        for sec in (ch.get("sections") or []):
            ids = [int(x) for x in (sec.get("blocks") or [])
                   if str(x).isdigit() or isinstance(x, int)]
            ids = sorted({i for i in ids if 1 <= i <= n})
            if not sec.get("heading"):
                continue
            sections.append({"no": sec.get("no", ""), "heading": str(sec["heading"]).strip(),
                             "blocks": ids,
                             "points": [str(p) for p in (sec.get("points") or [])][:6]})
        if not ch.get("heading") or not sections:
            continue
        chapters.append({"heading": str(ch["heading"]).strip(), "sections": sections})

    if not chapters:
        raise AppError("模型没有给出可用的章节树", code="PLAN_EMPTY")

    chapters = _norm_no(chapters, "chapter")
    for ch in chapters:
        _norm_no(ch["sections"], "section")

    tables = []
    for t in (plan.get("tables") or []):
        cols = [str(c).strip() for c in (t.get("columns") or []) if str(c).strip()]
        rows = [[str(c).strip() for c in r] for r in (t.get("rows") or []) if r]
        if not cols or not rows:
            continue
        rows = [r + [""] * (len(cols) - len(r)) if len(r) < len(cols) else r[:len(cols)]
                for r in rows]
        tables.append({"caption": str(t.get("caption") or "表格").strip(),
                       "anchor": str(t.get("anchor") or chapters[0]["no"]).strip(),
                       "columns": cols, "rows": rows})

    return {
        "title": str(plan.get("title") or "").strip(),
        "subtitle": str(plan.get("subtitle") or "").strip(),
        "note": str(plan.get("note") or "").strip(),
        "chapters": chapters,
        "tables": tables,
        "uncertain": [str(u).strip() for u in (plan.get("uncertain") or []) if str(u).strip()][:20],
    }


def fallback_plan(blocks: list[dict], title: str) -> dict:
    """模型不可用时的兜底：按原标题层级成章，不设计表格。

    层级选择：统计各标题级别的数量，挑"数量在 2—8 个之间"的最浅一级当章，
    比它深一级的当节；这样 261119 那种 ##/###/#### 三层结构能落成 6 章。
    """
    levels = {}
    for b in blocks:
        if b["kind"] == "heading":
            levels.setdefault(b["level"], []).append(b)
    chapter_level, section_level = None, None
    for lv in sorted(levels):
        if 2 <= len(levels[lv]) <= 8:
            chapter_level = lv
            deeper = [k for k in sorted(levels) if k > lv]
            section_level = deeper[0] if deeper else None
            break
    if chapter_level is None:
        chapter_level = max(levels) if levels else 0
        section_level = None

    chapters, cur = [], None
    for b in blocks:
        if b["kind"] == "heading" and b["level"] == chapter_level:
            heading = re.sub(r"^#{1,6}\s+", "", b["text"]).strip()
            cur = {"heading": heading, "sections": [
                {"heading": heading, "blocks": [b["id"]], "points": []}]}
            chapters.append(cur)
            continue
        if cur is None:
            cur = {"heading": "会议概况", "sections": [
                {"heading": "会议概况", "blocks": [], "points": []}]}
            chapters.append(cur)
        if b["kind"] == "heading" and section_level and b["level"] == section_level:
            heading = re.sub(r"^#{1,6}\s+", "", b["text"]).strip()
            cur["sections"].append({"heading": heading, "blocks": [b["id"]], "points": []})
        else:
            cur["sections"][-1]["blocks"].append(b["id"])
    chapters = [c for c in chapters if any(s["blocks"] for s in c["sections"])]
    if not chapters:
        chapters = [{"heading": "会议纪要", "sections": [
            {"heading": "全文", "blocks": [b["id"] for b in blocks], "points": []}]}]
    chapters = _norm_no(chapters, "chapter")
    for ch in chapters:
        _norm_no(ch["sections"], "section")
    return {"title": title, "subtitle": "", "note": "", "chapters": chapters,
            "tables": [], "uncertain": []}


def design_plan(blocks: list[dict], title: str, emit=None,
                allow_llm: bool = True) -> dict:
    """P3 主入口：有 key 走模型，失败/禁用走兜底。"""
    if not allow_llm or not llm.api_key():
        if emit:
            emit("step", label="结构设计", detail="跳过模型，使用兜底结构（未配置 key 或已禁用）")
        return fallback_plan(blocks, title)

    outline = blocks_to_text(blocks, per_block=150)
    stats = {"chars": sum(len(b["text"]) for b in blocks), "blocks": len(blocks),
             "headings": sum(1 for b in blocks if b["kind"] == "heading")}
    try:
        raw = llm.chat_json(prompt.plan_messages(outline, stats), max_tokens=6000)
        plan = _clean_plan(raw, blocks)
        if emit:
            emit("step", label="结构设计",
                 detail="%d 章 / %d 节 / %d 张表（模型）"
                 % (len(plan["chapters"]),
                    sum(len(c["sections"]) for c in plan["chapters"]), len(plan["tables"])))
        return plan
    except AppError as e:
        if emit:
            emit("step", label="结构设计",
                 detail="模型失败（%s：%s），改用兜底结构"
                 % (e.code, (e.message or "")[:120]))
        return fallback_plan(blocks, title)


def dump_plan(plan: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
