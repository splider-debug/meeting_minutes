# -*- coding: utf-8 -*-
"""P6 守恒校验：模型最容易犯的错是"数字被顺手改掉"。

规则：
  · 交付稿正文与表格里出现的每个数字，必须在原文里出现过；
  · 章号连续（一、二、三…）、节号连续（（一）（二）…）、表号连续（表 1、2、3…）。
不符合就列出清单并中止 —— 这是防幻觉的硬闸门，不做"提醒一下继续"。
"""
import re

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
CHAPTER_RE = re.compile(r"^##\s+([一二三四五六七八九十]+)、", re.M)
SECTION_RE = re.compile(r"^###\s+（([一二三四五六七八九十]+)）", re.M)
TABLE_RE = re.compile(r"^表\s*(\d+)[　.、:：]", re.M)
CN_NO = "一二三四五六七八九十"


def collect_numbers(text: str) -> set[str]:
    """收集数字并做等价归一：700万 与 7,000,000 不换算（SOP 要求照原文），
    但去掉千分位逗号与前后空格，避免纯格式差异造成误报。"""
    t = text.replace(",", "").replace("，", "")
    return {m.group(0) for m in NUM_RE.finditer(t)}


def check_numbers(source_text: str, pieces: list[str]) -> dict:
    src = collect_numbers(source_text)
    missing = []
    for piece in pieces:
        for n in collect_numbers(piece):
            if n not in src and n not in missing:
                missing.append(n)
    # 结构自带的小整数（表号、序号、节号）不算违规：调用方已排除标题与序号列
    return {"ok": not missing, "missing": missing[:30], "checked": len(src)}


def check_structure(md: str) -> dict:
    problems = []
    chapters = CHAPTER_RE.findall(md)
    if chapters:
        expect = CN_NO[:len(chapters)]
        if "".join(chapters) != expect:
            problems.append("章号不连续：实际 %s，应为 %s"
                            % ("、".join(chapters), "、".join(expect)))
    else:
        problems.append("没有识别到任何章标题（## 一、…）")

    # 节号在每个章内从（一）重新开始
    cur, secs = [], []
    for line in md.split("\n"):
        if line.startswith("## "):
            if cur:
                secs.append(cur)
            cur = []
        m = re.match(r"^###\s+（([一二三四五六七八九十]+)）", line)
        if m:
            cur.append(m.group(1))
    if cur:
        secs.append(cur)
    for i, group in enumerate(secs, 1):
        if not group:
            continue
        expect = CN_NO[:len(group)]
        if "".join(group) != expect:
            problems.append("第 %d 章节号不连续：实际 %s"
                            % (i, "、".join(group)))

    tables = [int(x) for x in TABLE_RE.findall(md)]
    if tables:
        if tables != list(range(1, len(tables) + 1)):
            problems.append("表号不连续：%s" % tables)

    return {"ok": not problems, "problems": problems,
            "chapters": len(chapters), "sections": sum(len(s) for s in secs),
            "tables": len(tables)}
