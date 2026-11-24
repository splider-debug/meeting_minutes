# -*- coding: utf-8 -*-
"""
docx -> Markdown 逐字转写（结构化还原 + 自证一致性）。

为什么用它：比"导入 Univer 再 dump 段落"快一个数量级，且能直接给出
"逐字符一致"的校验结论（SOP §7 要求逐字稿必须自证）。

层级判定优先级（先命中先用）:
  1) 段落自带 outlineLvl（真实 Word/WPS 文档通常有）
  2) 段落样式名 heading N / Title
  3) 文本编号模式 + 加粗：一、 / （一） / 1. / （1）
  4) 字号映射（--map "22:1,15:2,12.5:3"，或用 --auto-map 由字号聚类）

用法:
  python docx2md.py in.docx out.md                 # 转写 + 一致性校验
  python docx2md.py in.docx out.md --keep-symbols   # 保留 ▪ 等原字符
  python docx2md.py in.docx out.md --auto-map       # 用字号聚类推层级
  python docx2md.py in.docx out.md --report         # 打印判定明细

只读源文件，只写目标 md；不依赖第三方库。
"""
import re
import sys
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def q(tag, ns=W):
    return '{%s}%s' % (ns, tag)


def val(el, name='val'):
    return None if el is None else el.get(q(name))


# ----------------------------------------------------------------- 读取
def load_parts(path):
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    doc = ET.fromstring(z.read('word/document.xml'))
    styles = ET.fromstring(z.read('word/styles.xml')) if 'word/styles.xml' in names else None
    numbering = ET.fromstring(z.read('word/numbering.xml')) if 'word/numbering.xml' in names else None
    return doc, styles, numbering


def style_table(styles):
    """styleId -> {name, outline, sz, bold}"""
    out = {}
    if styles is None:
        return out
    for st in styles.iter(q('style')):
        sid = st.get(q('styleId'))
        if not sid:
            continue
        ppr, rpr = st.find(q('pPr')), st.find(q('rPr'))
        ol = ppr.find(q('outlineLvl')) if ppr is not None else None
        sz = rpr.find(q('sz')) if rpr is not None else None
        out[sid] = {
            'name': (val(st.find(q('name'))) or '').strip(),
            'outline': int(val(ol)) if ol is not None else None,
            'sz': int(val(sz)) / 2.0 if sz is not None else None,
            'bold': rpr is not None and rpr.find(q('b')) is not None,
        }
    return out


def numbering_table(numbering):
    """numId -> 0 级 numFmt（bullet / decimal / ...）"""
    out = {}
    if numbering is None:
        return out
    abs_map = {}
    for an in numbering.iter(q('abstractNum')):
        lvl = an.find(q('lvl'))
        fmt = val(lvl.find(q('numFmt'))) if lvl is not None else None
        abs_map[an.get(q('abstractNumId'))] = fmt or 'bullet'
    for num in numbering.iter(q('num')):
        nid = num.get(q('numId'))
        ref = num.find(q('abstractNumId'))
        if nid is not None and ref is not None:
            out[nid] = abs_map.get(val(ref), 'bullet')
    return out


def para_raw(p, smap):
    ppr = p.find(q('pPr'))
    info = {'style': None, 'outline': None, 'num': None, 'ilvl': 0,
            'sz': None, 'bold': False, 'ind': {}}
    if ppr is not None:
        info['style'] = val(ppr.find(q('pStyle')))
        ol = ppr.find(q('outlineLvl'))
        if ol is not None:
            info['outline'] = int(val(ol))
        np = ppr.find(q('numPr'))
        if np is not None:
            info['num'] = val(np.find(q('numId')))
            lv = np.find(q('ilvl'))
            info['ilvl'] = int(val(lv)) if lv is not None else 0
        ind = ppr.find(q('ind'))
        if ind is not None:
            for k in ('left', 'hanging', 'firstLine'):
                v = val(ind, k)
                if v is not None:
                    info['ind'][k] = int(v) if v.lstrip('-').isdigit() else v
    for r in p.iter(q('r')):
        rpr = r.find(q('rPr'))
        if rpr is None:
            continue
        sz = rpr.find(q('sz'))
        if sz is not None:
            info['sz'] = int(val(sz)) / 2.0
        b = rpr.find(q('b'))
        if b is not None and val(b) not in ('0', 'false'):
            info['bold'] = True
    st = smap.get(info['style']) if info['style'] else None
    if st:
        if info['sz'] is None and st['sz']:
            info['sz'] = st['sz']
        if st['bold']:
            info['bold'] = True
        if info['outline'] is None and st['outline'] is not None:
            info['outline'] = st['outline']
    info['text'] = ''.join(t.text or '' for t in p.iter(q('t')))
    return info


# ----------------------------------------------------------------- 层级判定
PATTERNS = [
    (re.compile(r'^[一二三四五六七八九十百]+、'), 2),
    (re.compile(r'^（[一二三四五六七八九十百]+）'), 3),
    (re.compile(r'^\d+[.、]\s*'), 4),
    (re.compile(r'^（\d+）'), 5),
]


def detect_level(info, smap, size_map):
    if info['outline'] is not None:
        return info['outline'] + 2  # 主标题占 1 级
    st = smap.get(info['style']) or {}
    m = re.match(r'heading\s*(\d)', st.get('name', '').lower())
    if m:
        return int(m.group(1)) + 1
    if st.get('name', '').lower() == 'title':
        return 1
    text = info['text'].strip()
    if info['bold'] and text and len(text) <= 40:
        for pat, lv in PATTERNS:
            if pat.match(text):
                return lv
    if size_map and info['sz'] in size_map:
        return size_map[info['sz']]
    return 0


def build_size_map(infos, body_sz):
    """把"明显大于正文"的加粗字号按降序映射到 2/3/4 级。"""
    sizes = sorted({i['sz'] for i in infos
                    if i['bold'] and i['sz'] and i['sz'] > (body_sz or 0) + 0.6
                    and not i['outline']}, reverse=True)
    return {sz: min(4, idx + 2) for idx, sz in enumerate(sizes[:3])}


# ----------------------------------------------------------------- 写出
BULLET_CHARS = ('▪', '•', '·', '●', '○', '◆', '■', '□', '➢', '➤', '‣')
SEP_ROW = re.compile(r'^[\s:\-|]*$')


def md_width(text, minimum=3):
    return max(minimum, len(text))


def table_md(tbl):
    rows = []
    for tr in tbl.findall(q('tr')):
        cells = []
        for tc in tr.findall(q('tc')):
            cell_paras = []
            for p in tc.iter(q('p')):
                t = ''.join(x.text or '' for x in p.iter(q('t'))).strip()
                if t:
                    cell_paras.append(t)
            cells.append('<br>'.join(cell_paras))
        rows.append(cells)
    if not rows:
        return []
    ncol = max(len(r) for r in rows)
    rows = [r + [''] * (ncol - len(r)) for r in rows]
    head = rows[0]
    out = ['| ' + ' | '.join(head) + ' |',
           '|' + '|'.join(['---'] * ncol) + '|']
    for r in rows[1:]:
        out.append('| ' + ' | '.join(r) + ' |')
    return out


def convert(path, keep_symbols, auto_map, report):
    doc, styles, numbering = load_parts(path)
    smap = style_table(styles)
    nmap = numbering_table(numbering)
    body = doc.find(q('body'))

    items = []  # (kind, level|None, text, ilvl, numfmt)
    for child in body:
        if child.tag == q('p'):
            info = para_raw(child, smap)
            items.append(('p', info, None))
        elif child.tag == q('tbl'):
            items.append(('tbl', None, child))

    infos = [it[1] for it in items if it[0] == 'p']
    body_sz = None
    sizes = Counter(i['sz'] for i in infos if i['text'].strip() and not i['outline'])
    if sizes:
        body_sz = sizes.most_common(1)[0][0]
    size_map = build_size_map(infos, body_sz) if auto_map else {}

    # 文档主标题：首个"最大字号且 ≥18pt 的加粗段"——统一升为 1 级，
    # 其余标题自动落到 2 级起（# 文档标题 / ## 章节 / ### 小节 / #### 条目）
    title_obj = None
    cand = [i for i in infos if i['text'].strip() and i['sz']]
    if cand:
        max_sz = max(i['sz'] for i in cand)
        if max_sz >= 18:
            for i in cand:
                if i['sz'] == max_sz:
                    title_obj = i
                    break

    # 先算每段的层级，再整体归一到 1 级起（避免源文档 outlineLvl 偏移导致 #### 开头）
    levels = {}
    for kind, info, node in items:
        if kind != 'p' or not info['text'].strip():
            continue
        levels[id(info)] = detect_level(info, smap, size_map)
    if title_obj is not None:
        levels[id(title_obj)] = 1
    others = [v for k, v in levels.items() if v and (title_obj is None or k != id(title_obj))]
    shift = 0
    if others:
        target = 2 if title_obj is not None else 1
        shift = target - min(others)
    if shift:
        for k, v in list(levels.items()):
            if v and (title_obj is None or k != id(title_obj)):
                levels[k] = max(1, min(6, v + shift))

    lines = []
    stats = Counter()
    level_log = []
    for kind, info, node in items:
        if kind == 'tbl':
            lines.append('')
            lines.extend(table_md(node))
            continue
        text = info['text'].strip()
        if not text:
            continue
        lv = levels.get(id(info), 0)
        if lv:
            lines.append('')
            lines.append('#' * min(lv, 6) + ' ' + text)
            lines.append('')
            stats['heading'] += 1
            level_log.append((lv, text))
            continue
        st_name = (smap.get(info['style']) or {}).get('name', '').lower()
        if 'quote' in st_name or '引用' in st_name:
            lines.append('> ' + text)
            stats['quote'] += 1
            continue
        if info['num'] is not None:
            fmt = nmap.get(info['num'], 'bullet')
            indent = '  ' * max(0, info['ilvl'])
            lines.append(indent + ('- ' if fmt == 'bullet' else '1. ') + text)
            stats['list'] += 1
            continue
        if not keep_symbols and text[:1] in BULLET_CHARS:
            indent = '  ' if info['ind'].get('left', 0) and int(info['ind'].get('left', 0)) > 600 else ''
            lines.append(indent + '- ' + text[1:].strip())
            stats['list'] += 1
            continue
        lines.append('')
        lines.append(text)
        lines.append('')
        stats['para'] += 1

    md = '\n'.join(lines)
    md = re.sub(r'\n{3,}', '\n\n', md).strip() + '\n'
    if report:
        print('  字号映射: %s（正文 %spt）'
              % ({k: 'H%d' % (v - 1) for k, v in size_map.items()}, body_sz))
        print('  层级判定:')
        for lv, text in level_log[:40]:
            print('     H%-2d %s' % (lv - 1, text[:60]))
    print('  统计: 标题 %d｜列表 %d｜引用 %d｜正文段 %d'
          % (stats['heading'], stats['list'], stats['quote'], stats['para']))
    return md, doc


# ----------------------------------------------------------------- 一致性校验
def norm(s):
    s = re.sub(r'[\u0000-\u001f\u007f\u200b\ufeff]', '', s)
    s = re.sub(r'\s+', '', s)
    for ch in BULLET_CHARS + ('-', '*', '#', '|', '>', '`'):
        s = s.replace(ch, '')
    return s


def verify(doc, md_text):
    body = doc.find(q('body'))
    src = ''.join(''.join(t.text or '' for t in p.iter(q('t'))) for p in body.iter(q('p')))
    a = norm(src)
    body_lines = [l for l in md_text.split('\n') if not SEP_ROW.match(l)]
    # md 侧行首的列表标记（"- " / "1. "）是结构，不是原文文字：
    # Word 自动编号的序号只存在于 numbering.xml，w:t 里没有，不剥掉就会假报不一致。
    body_lines = [re.sub(r'^\s*(?:\d+\.|[-*+])\s+', '', l) for l in body_lines]
    b = norm('\n'.join(body_lines))
    if a == b:
        return True, 'identical（%d 字符）' % len(a)
    n = min(len(a), len(b))
    i = next((k for k in range(n) if a[k] != b[k]), n)
    ctx = 25
    return False, ('首个差异在第 %d 字符\n   docx: ...%s\n   md  : ...%s\n'
                   % (i, a[max(0, i - ctx):i + ctx], b[max(0, i - ctx):i + ctx]))


def main():
    src, dst = sys.argv[1], sys.argv[2]
    keep_symbols = '--keep-symbols' in sys.argv
    auto_map = '--auto-map' in sys.argv
    report = '--report' in sys.argv
    md, doc = convert(src, keep_symbols, auto_map, report)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(md)
    print('written %s（%d 行）' % (dst, md.count('\n')))
    ok, msg = verify(doc, md)
    print('一致性校验: %s %s' % ('通过' if ok else '未通过', msg))
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
