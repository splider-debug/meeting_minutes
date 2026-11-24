# -*- coding: utf-8 -*-
"""
打印 docx 的标题树（人工检查排版顺序用）。

判定标题：pStyle 名 heading N / Title，或段落自带 outlineLvl。
兼容 Word/WPS 直接格式文档（无 pStyle 时按 outlineLvl 或"字号大且加粗"识别）。

用法: python outline_docx.py file.docx [--all]
"""
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def q(tag):
    return '{%s}%s' % (W, tag)


def style_levels(styles):
    out = {}
    if styles is None:
        return out
    for st in styles.iter(q('style')):
        sid = st.get(q('styleId'))
        ppr = st.find(q('pPr'))
        name = st.find(q('name'))
        lvl = None
        if ppr is not None and ppr.find(q('outlineLvl')) is not None:
            lvl = int(ppr.find(q('outlineLvl')).get(q('val')))
        nm = (name.get(q('val')) if name is not None else '') or ''
        m = re.match(r'heading\s*(\d)', nm.lower())
        if lvl is None and m:
            lvl = int(m.group(1)) - 1
        if lvl is None and nm.lower() == 'title':
            lvl = -1
        if lvl is not None:
            out[sid] = lvl
    return out


PATTERNS = [(re.compile(r'^[一二三四五六七八九十百]+、'), 0),
            (re.compile(r'^（[一二三四五六七八九十百]+）'), 1),
            (re.compile(r'^\d+[.、]\s*'), 2),
            (re.compile(r'^（\d+）'), 3)]


def guess_level(text, bold):
    """没有 outlineLvl / pStyle 时，按"编号模式 + 加粗"兜底（直接格式文档常见）。"""
    if not bold or not text or len(text) > 40:
        return None
    for pat, lv in PATTERNS:
        if pat.match(text):
            return lv
    return None


def main():
    path = sys.argv[1]
    show_all = '--all' in sys.argv
    z = zipfile.ZipFile(path)
    doc = ET.fromstring(z.read('word/document.xml'))
    styles = ET.fromstring(z.read('word/styles.xml')) if 'word/styles.xml' in z.namelist() else None
    levels = style_levels(styles)

    n = 0
    for p in doc.iter(q('p')):
        ppr = p.find(q('pPr'))
        sid = lvl = None
        if ppr is not None:
            ps = ppr.find(q('pStyle'))
            sid = ps.get(q('val')) if ps is not None else None
            ol = ppr.find(q('outlineLvl'))
            lvl = int(ol.get(q('val'))) if ol is not None else None
        if lvl is None and sid in levels:
            lvl = levels[sid]
        text = ''.join(t.text or '' for t in p.iter(q('t'))).strip()
        if lvl is None and text:
            bold = any(r.find(q('rPr')) is not None and r.find(q('rPr')).find(q('b')) is not None
                       for r in p.iter(q('r')))
            lvl = guess_level(text, bold)
        if lvl is None or not text:
            continue
        n += 1
        pad = '  ' * max(0, lvl + 1)
        tag = 'TITLE' if lvl < 0 else 'H%d' % (lvl + 1)
        print('%s%-5s %s' % (pad, tag, text[:70]))

    body = doc.find(q('body'))
    total_p = len(list(doc.iter(q('p'))))
    print('---\n标题 %d 个｜段落 %d 个｜表格 %d 张'
          % (n, total_p, len(list(body.iter(q('tbl'))))))
    if not show_all and n == 0:
        print('提示: 未识别到标题，可用 docx_dump.py 看字号分布后手工判断层级。')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python outline_docx.py file.docx [--all]')
        sys.exit(2)
    main()
