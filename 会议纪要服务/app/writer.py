# -*- coding: utf-8 -*-
"""P4 分节写正文：按 plan 里的 block 编号回取原文，逐节生成。

分节的意义：
  · 每次输入只有该节那点原文，模型不可能"看到后面忘了前面"；
  · 单节失败可以只重试这一节；
  · 进度可以做到"第 3/19 节完成"。
"""
import os
import re

from . import llm, prompt
from .errors import AppError


def _balance_bold(text: str) -> str:
    """把不成对的 ** 去掉：md2docx 只认成对粗体，落单的会变成字面星号，
    进而让 verify_docx 报"残留标记 + 正文缺失"。按段落判断（粗体可跨行）。"""
    paras = re.split(r"\n\s*\n", text)
    fixed = []
    for p in paras:
        if p.count("**") % 2 == 1:
            p = p.replace("**", "")
        fixed.append(p)
    return "\n\n".join(fixed)


def strip_heading_lines(text: str) -> str:
    """清理模型输出：去掉自造标题、代码围栏，以及内部块号引用。"""
    out, fence = [], False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if re.match(r"^#{1,6}\s+", s):
            continue
        out.append(line.rstrip())
    body = "\n".join(out)
    body = prompt.BLOCK_REF_RE.sub("", body)          # （[74] xxx） → （xxx）
    body = re.sub(r"[（(]\s*[）)]", "", body)          # 清掉被掏空的括号
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"[ \t]+([，。；：、])", r"\1", body)
    return _balance_bold(body.strip())


def block_text(block: dict) -> str:
    """去掉标题井号，保留原文。"""
    return re.sub(r"^#{1,6}\s+", "", block["text"]).strip()


def write_section(chapter: dict, section: dict, blocks_by_id: dict,
                  emit=None) -> str:
    ids = section.get("blocks") or []
    texts = [block_text(blocks_by_id[i]) for i in ids if i in blocks_by_id]
    if not texts:
        return ""
    blocks_text = "\n\n".join("[%d] %s" % (i, block_text(blocks_by_id[i]))
                              for i in ids if i in blocks_by_id)
    # 不要把"第 N/M 节"这类流水线元信息传给模型：它会当成内容写进正文。
    messages = prompt.write_messages(
        chapter="%s、%s" % (chapter["no"], chapter["heading"]),
        section="%s %s" % (section["no"], section["heading"]),
        points=section.get("points") or [],
        blocks_text=blocks_text,
    )
    r = llm.chat(messages, max_tokens=900, temperature=0.3)
    body = strip_heading_lines(r["text"])
    if emit:
        emit("step", label="写 %s%s" % (chapter["no"], section["no"]),
             detail="%d 字｜%d 块原文" % (len(body), len(ids)))
    return body


def write_all(plan: dict, blocks: list[dict], emit=None,
              allow_llm: bool = True) -> dict:
    """返回 {(章号, 节号): 正文}。模型失败时该节留空，由组装阶段用原文兜底。"""
    blocks_by_id = {b["id"]: b for b in blocks}
    bodies, total = {}, 0
    sections = [(ch, sec) for ch in plan["chapters"] for sec in ch["sections"]]
    for idx, (ch, sec) in enumerate(sections, 1):
        key = (ch["no"], sec["no"])
        if not allow_llm or not llm.api_key():
            bodies[key] = ""
            continue
        try:
            bodies[key] = write_section(ch, sec, blocks_by_id, emit=emit)
        except AppError as e:
            bodies[key] = ""
            if emit:
                emit("step", label="写 %s%s 失败" % key,
                     detail="%s：%s（该节改用原文兜底）" % (e.code, (e.message or "")[:100]))
        total += len(bodies[key])
    if emit:
        emit("step", label="正文合计", detail="%d 字 / %d 节" % (total, len(sections)))
    return bodies


def save_drafts(bodies: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for (cno, sno), body in bodies.items():
        safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", "%s%s" % (cno, sno))
        with open(os.path.join(out_dir, safe + ".md"), "w", encoding="utf-8") as f:
            f.write(body or "")
