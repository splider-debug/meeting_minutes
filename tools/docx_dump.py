# -*- coding: utf-8 -*-
"""
docx 只读摸底：一次看清页面设置、字号/加粗分布、标题候选、表格规格。

用途：整理会议资料前先跑一遍，用真实字号分布决定"字号→标题级别"的映射表，
不要照抄别的文档的数值（见 SOP §7）。

用法:
  python docx_dump.py file.docx              # 概览
  python docx_dump.py file.docx --text 60    # 另外打印前 60 个段落的文本
  python docx_dump.py file.docx --json       # 机器可读（给下游脚本用）

只读：不修改任何文件，不依赖第三方库（zipfile + ElementTree）。
"""
import json
import sys
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def q(tag):
    return '{%s}%s' % (W, tag)


def val(el, name='val'):
    return None if el is None else el.get(q(name))


def parse_docx(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    doc = ET.fromstring(z.read('word/document.xml'))
    styles = ET.fromstring(z.read('word/styles.xml')) if 'word/styles.xml' in names else None
    footer_refs = [n for n in names if n.startswith('word/footer')]
    return z, names, doc, styles, footer_refs


def style_map(styles):
    """styleId -> {name, outline, sz, bold, basedOn}"""
    out = {}
    if styles is None:
        return out
    for st in styles.iter(q('style')):
        sid = st.get(q('styleId'))
        if not sid:
            continue
        name_el = st.find(q('name'))
        ppr = st.find(q('pPr'))
        rpr = st.find(q('rPr'))
        ol = ppr.find(q('outlineLvl')) if ppr is not None else None
        sz_el = rpr.find(q('sz')) if rpr is not None else None
        out[sid] = {
            'name': val(name_el),
            'outline': int(val(ol)) if ol is not None else None,
            'sz': (int(val(sz_el)) / 2.0) if sz_el is not None else None,
            'bold': rpr is not None and rpr.find(q('b')) is not None,
        }
    return out


def para_info(p, smap, in_table=False):
    ppr = p.find(q('pPr'))
    pstyle = outline = None
    num = None
    ilvl = 0
    ind = {}
    if ppr is not None:
        pstyle = val(ppr.find(q('pStyle')))
        ol = ppr.find(q('outlineLvl'))
        outline = int(val(ol)) if ol is not None else None
        np = ppr.find(q('numPr'))
        if np is not None:
            num = val(np.find(q('numId')))
            lv = np.find(q('ilvl'))
            ilvl = int(val(lv)) if lv is not None else 0
        i = ppr.find(q('ind'))
        if i is not None:
            for k in ('left', 'firstLine', 'hanging'):
                if val(i, k) is not None:
                    ind[k] = val(i, k)
    sz = None
    bold = False
    fonts = set()
    for r in p.iter(q('r')):
        rpr = r.find(q('rPr'))
        if rpr is None:
            continue
        s = rpr.find(q('sz'))
        if s is not None:
            sz = int(val(s)) / 2.0
        b = rpr.find(q('b'))
        if b is not None and val(b) not in ('0', 'false'):
            bold = True
        rf = rpr.find(q('rFonts'))
        if rf is not None:
            fam = rf.get(q('eastAsia')) or rf.get(q('ascii'))
            if fam:
                fonts.add(fam)
    text = ''.join(t.text or '' for t in p.iter(q('t')))
    if pstyle and pstyle in smap:
        st = smap[pstyle]
        if sz is None and st['sz']:
            sz = st['sz']
        if st['bold']:
            bold = True
        if outline is None and st['outline'] is not None:
            outline = st['outline']
    return {
        'text': text, 'style': pstyle, 'outline': outline, 'sz': sz, 'bold': bold,
        'num': num, 'ilvl': ilvl, 'ind': ind, 'fonts': sorted(fonts), 'in_table': in_table,
    }


def table_info(tbl):
    rows = tbl.findall(q('tr'))
    grid = tbl.find(q('tblGrid'))
    ncols = len(grid.findall(q('gridCol'))) if grid is not None else 0
    head = []
    if rows:
        for tc in rows[0].findall(q('tc')):
            head.append(''.join(t.text or '' for t in tc.iter(q('t'))).strip())
    return {'rows': len(rows), 'cols': ncols or len(head), 'header': head}


def page_setup(doc):
    sect = doc.find(q('body')).find(q('sectPr'))
    if sect is None:
        for p in reversed(list(doc.iter(q('p')))):
            ppr = p.find(q('pPr'))
            if ppr is not None and ppr.find(q('sectPr')) is not None:
                sect = ppr.find(q('sectPr'))
                break
    if sect is None:
        return None
    pg = sect.find(q('pgSz'))
    mar = sect.find(q('pgMar'))
    out = {}
    if pg is not None:
        out['w'], out['h'] = val(pg, 'w'), val(pg, 'h')
        out['orient'] = val(pg, 'orient') or 'portrait'
    if mar is not None:
        for k in ('top', 'bottom', 'left', 'right', 'header', 'footer'):
            out[k] = val(mar, k)
    out['footer_refs'] = len(sect.findall(q('footerReference')))
    return out


def main():
    path = sys.argv[1]
    show_text = 0
    as_json = '--json' in sys.argv
    if '--text' in sys.argv:
        show_text = int(sys.argv[sys.argv.index('--text') + 1])

    z, names, doc, styles, footer_refs = parse_docx(path)
    smap = style_map(styles)

    body = doc.find(q('body'))
    paras, tables = [], []
    for child in body:
        if child.tag == q('p'):
            paras.append(para_info(child, smap))
        elif child.tag == q('tbl'):
            tables.append(table_info(child))
            for p in child.iter(q('p')):
                paras.append(para_info(p, smap, in_table=True))
    all_p = list(doc.iter(q('p')))
    empty = sum(1 for p in all_p if ''.join(t.text or '' for t in p.iter(q('t'))).strip() == '')

    ps = page_setup(doc)
    outer = [p for p in paras if not p['in_table']]
    sz_dist = Counter()
    for p in outer:
        if p['text'].strip():
            sz_dist[(p['sz'], p['bold'])] += 1
    style_dist = Counter(p['style'] for p in outer if p['style'])
    heads = [p for p in outer
             if p['outline'] is not None or (p['style'] or '').lower().startswith('heading')
             or p['style'] == 'Title']
    caption = [p for p in outer if p['text'].strip().startswith('表') and len(p['text'].strip()) < 30]

    if as_json:
        print(json.dumps({
            'file': path, 'parts': len(names), 'footers': footer_refs,
            'page': ps, 'paragraphs_all': len(all_p), 'empty': empty,
            'outer_paragraphs': len(outer), 'tables': tables,
            'sz_dist': [[k[0], k[1], v] for k, v in sz_dist.most_common()],
            'style_dist': style_dist.most_common(),
            'headings': [{'level': p['outline'], 'style': p['style'], 'sz': p['sz'],
                          'bold': p['bold'], 'text': p['text'][:60]} for p in heads],
        }, ensure_ascii=False, indent=1))
        return

    print('== %s' % path)
    print('  部件 %d 个（footer: %s）' % (len(names), ', '.join(footer_refs) or '无'))
    if ps:
        print('  页面: %s×%s twips  边距 上%s 下%s 左%s 右%s  orient=%s'
              % (ps.get('w'), ps.get('h'), ps.get('top'), ps.get('bottom'),
                 ps.get('left'), ps.get('right'), ps.get('orient')))
        try:
            cw = int(ps.get('w')) - int(ps.get('left')) - int(ps.get('right'))
            print('  内容宽 %d twips = %.1f px(96DPI) ← 表格列宽总和' % (cw, cw / 15.0))
        except (TypeError, ValueError):
            pass
    else:
        print('  页面: 未找到 sectPr')
    print('  段落 %d 个（表外 %d｜空段落 %d）  表格 %d 张'
          % (len(all_p), len(outer), empty, len(tables)))
    print('  字号/加粗分布（表外非空段落）:')
    for (sz, bold), n in sz_dist.most_common(12):
        print('     %5s pt  %s  ×%d' % (sz, '加粗' if bold else '    ', n))
    if style_dist:
        print('  段落样式: ' + ', '.join('%s×%d' % (k, v) for k, v in style_dist.most_common(10)))
    print('  标题候选 %d 个（level 取 outlineLvl，无则看样式名）:' % len(heads))
    for p in heads[:25]:
        print('     L%-3s %-10s %5spt %s  %s'
              % ('-' if p['outline'] is None else p['outline'], p['style'] or '-',
                 p['sz'], 'B' if p['bold'] else ' ', p['text'][:55]))
    if len(heads) > 25:
        print('     ... 余 %d 个' % (len(heads) - 25))
    if tables:
        print('  表格规格:')
        for i, t in enumerate(tables, 1):
            print('     表%d %d行×%d列  表头: %s'
                  % (i, t['rows'], t['cols'], ' | '.join(t['header'])))
    if caption:
        print('  表题候选 %d 个: %s'
              % (len(caption), ' / '.join(p['text'][:20] for p in caption[:6])))
    if show_text:
        print('  --- 前 %d 段文本 ---' % show_text)
        for i, p in enumerate(outer[:show_text], 1):
            flag = 'H%s' % (p['outline'] if p['outline'] is not None else '-')
            print('  %3d [%s|%spt|%s] %s'
                  % (i, flag, p['sz'], 'B' if p['bold'] else ' ',
                     p['text'].replace('\n', ' ')[:90]))


if __name__ == '__main__':
    main()
