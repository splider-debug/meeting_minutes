# -*- coding: utf-8 -*-
"""P5 组装：plan + 分节正文 + 表格 → 符合 SOP 的交付稿 md。

"SOP 版式"在这里落地：一、／（一）／表格「表 N」编号连续、疑误清单固定成章。
模型给什么内容都行，编号与顺序由这里决定。
"""
import re

from .structure import TIME_RE


_INTERNAL_COL = re.compile(r"^(依据块|块号|依据|blocks?|source)$", re.I)


def _clean_caption(caption: str) -> str:
    """去掉模型自带的"表 N"前缀，编号由组装阶段统一给。"""
    return re.sub(r"^表\s*\d+\s*[　.、:：]?\s*", "", caption or "").strip() or "表格"


def _normalize_tables(plan: dict, blocks_by_id: dict) -> None:
    """把模型表格里的内部字段翻译成读者能用的信息。

    「依据块」这类列原本是给流水线用的块编号，直接交付等于把内部 ID 泄漏给读者，
    也会让守恒校验把块号当成"原文没有的数字"。这里把它换成块内的时间区间（出处）；
    若某个块没有时间区间且整列都空，就把该列整列删掉。
    """
    for t in plan["tables"]:
        cols = t["columns"]
        idxs = [i for i, c in enumerate(cols) if _INTERNAL_COL.match(c.strip())]
        if not idxs:
            continue
        for i in idxs:
            for row in t["rows"]:
                if i >= len(row):
                    continue
                ids = [int(x) for x in re.findall(r"\d+", row[i])]
                times = []
                for bid in ids:
                    b = blocks_by_id.get(bid)
                    if b:
                        for tt in TIME_RE.findall(b["text"]):
                            if tt not in times:
                                times.append(tt)
                row[i] = "、".join(times)
        # 整列为空 → 删列；否则把表头改成"出处"，并把非时间内容清空
        for i in sorted(idxs, reverse=True):
            if all((i >= len(r)) or not r[i].strip() for r in t["rows"]):
                for r in t["rows"]:
                    if i < len(r):
                        del r[i]
                del cols[i]
            else:
                cols[i] = "出处"

    # 任何"出处/时间"列里不是时间区间的值都清掉，避免出现"出处=待办"这种怪值
    for t in plan["tables"]:
        for i, c in enumerate(t["columns"]):
            if c.strip() in ("出处", "时间区间", "时间"):
                for row in t["rows"]:
                    if i < len(row) and row[i].strip() and not TIME_RE.search(row[i]):
                        row[i] = ""


def _md_table(columns: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join(["---"] * len(columns)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(c.replace("|", "/") for c in r) + " |")
    return "\n".join(out)


def _table_for(plan: dict, chapter_no: str) -> list[dict]:
    return [t for t in plan["tables"] if t["anchor"] == chapter_no]


def _fill_missing_tables(plan: dict) -> None:
    """模型漏表时的兜底：议题速览可由章节+时间区间确定性生成。"""
    has_overview = any("议题速览" in t["caption"] for t in plan["tables"])
    if has_overview:
        return
    rows = []
    for i, ch in enumerate(plan["chapters"], 1):
        if ch["heading"].startswith("会议") or "概况" in ch["heading"]:
            continue
        times = []
        for sec in ch["sections"]:
            for t in TIME_RE.findall(sec["heading"] + " " + " ".join(sec.get("points") or [])):
                times.append(t)
        core = "；".join((ch["sections"][0].get("points") or [])[:2])[:60] or ch["heading"]
        rows.append([str(i), ch["heading"], times[0] if times else "", core])
    if rows:
        plan["tables"].insert(0, {"caption": "议题速览", "anchor": plan["chapters"][0]["no"],
                                  "columns": ["序号", "议题", "时间区间", "核心内容"],
                                  "rows": rows})


def assemble(plan: dict, bodies: dict, blocks_by_id: dict, title: str,
             source_note: str = "", appendix_ai: list[str] | None = None) -> str:
    _fill_missing_tables(plan)
    _normalize_tables(plan, blocks_by_id)
    lines = ["# %s" % title, ""]
    if plan.get("subtitle"):
        lines += [plan["subtitle"], ""]
    note = ("整理形式：议题速览＋议题纪要＋共识与待办｜记录来源：%s" % source_note
            if source_note else (plan.get("note") or "整理形式：议题速览＋议题纪要＋共识与待办"))
    if "块" in note or "逐字稿（" in note:      # 模型把内部块数当成来源，换回真实文件名
        note = "整理形式：议题速览＋议题纪要＋共识与待办｜记录来源：%s" % (source_note or "会议听记")
    lines += [note, "", "---", ""]

    table_no = 0
    for ch in plan["chapters"]:
        lines += ["## %s、%s" % (ch["no"], ch["heading"]), ""]
        for sec in ch["sections"]:
            lines += ["### %s%s" % (sec["no"], sec["heading"]), ""]
            body = (bodies.get((ch["no"], sec["no"])) or "").strip()
            if not body:                      # 模型失败：用该节原文兜底，绝不空着
                texts = []
                for i in sec.get("blocks") or []:
                    b = blocks_by_id.get(i)
                    if not b:
                        continue
                    from .writer import block_text
                    t = block_text(b)
                    texts.append("- %s" % t if not t.startswith("#") else t)
                body = "\n".join(texts) or "（本节原文缺失）"
            lines += [body, ""]

        for t in _table_for(plan, ch["no"]):
            table_no += 1
            lines += ["表 %d　%s" % (table_no, _clean_caption(t["caption"])), "",
                      _md_table(t["columns"], t["rows"]), ""]

    # 疑误清单固定成章（SOP 要求），表号接着排
    if plan.get("uncertain"):
        nxt = len(plan["chapters"])
        from .structure import CN_NO
        no = CN_NO[nxt] if nxt < len(CN_NO) else str(nxt + 1)
        table_no += 1
        lines += ["## %s、需确认与核实的事项" % no, "",
                  "表 %d　疑误用词与待核实事项" % table_no, "",
                  _md_table(["序号", "原文表述", "疑点与建议"],
                            [[str(i), u.split("→")[0].strip(),
                              "→".join(u.split("→")[1:]).strip() or "待核实"]
                             for i, u in enumerate(plan["uncertain"], 1)]), ""]

        lines += ["整理边界：数字、金额、时间、型号一律照原文，不做换算；同一事实多口径并列保留；"
                  "原文转写疑点单列上表，未在正文擅自改正。", ""]

    # AI 洞察只能进附录（原文是听记工具自动生成的分析意见，不是参会人结论）
    if appendix_ai:
        nxt = len(plan["chapters"]) + (1 if plan.get("uncertain") else 0)
        from .structure import CN_NO
        no = CN_NO[nxt] if nxt < len(CN_NO) else str(nxt + 1)
        lines += ["## %s、附录：听记工具提炼要点（非参会人结论）" % no, "",
                  "> 以下条目由听记工具自动生成，**不是会议结论**，仅供回看时参考。", ""]
        lines += ["- %s" % a for a in appendix_ai]
        lines += [""]

    return "\n".join(lines).rstrip() + "\n"
