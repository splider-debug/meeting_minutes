# -*- coding: utf-8 -*-
"""
Markdown -> .docx（纯 OOXML 生成，无第三方依赖）

与 Univer 路线相比：可写页脚页码、样式完全可控、一条命令可重复产出；
代价是"手写 OOXML"，因此必须配 verify_docx.py 做结构自检（SOP §5 阶段 4）。

用法:
  python md2docx.py in.md out.docx                        # 默认 classic 主题
  python md2docx.py in.md out.docx "文档标题" --theme report
  python md2docx.py in.md out.docx --theme report         # 会议纪要常用主题

主题:
  classic —— 微软雅黑标题、蓝色表头白字（261111 战略会用）
  report  —— 黑体标题 + 宋体正文、浅蓝表头深蓝字（261118 冰凌会议用，中文公文更稳）

支持: # 标题、有序/无序列表（含 2 空格嵌套）、> 引用、| 表格、**加粗**、--- 分隔线、
      <br> 单元格内换行、<!-- page-break --> 分页、表题自动样式（表 1／图 1）。
"""
import html
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

NS_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

FONT_SONG = '宋体'
FONT_HEI = '黑体'


def fonts(ascii_font, east_font):
    return ('<w:rFonts w:ascii="%s" w:hAnsi="%s" w:eastAsia="%s" w:cs="%s"/>'
            % (ascii_font, ascii_font, east_font, ascii_font))


THEMES = {
    'classic': {
        'margins': (1440, 1440, 1418, 1418),   # 上 下 左 右（twips）
        'title': dict(fonts=fonts('Microsoft YaHei', '微软雅黑'), sz=40, color='1F3864', rule='1F3864'),
        'h1': dict(fonts=fonts('Microsoft YaHei', '微软雅黑'), sz=30, color='1F3864',
                   bar='2F5496', before=320, after=140, line=300),
        'h2': dict(fonts=fonts('Microsoft YaHei', '微软雅黑'), sz=24, color='2E5C8A',
                   before=220, after=100, line=300),
        'h3': dict(fonts=fonts('Microsoft YaHei', '微软雅黑'), sz=22, color='333333',
                   before=160, after=80, line=300),
        'body': dict(fonts=fonts('Times New Roman', FONT_SONG), sz=21, line=340, after=80, first=420),
        'quote': dict(fonts=fonts('KaiTi', '楷体'), sz=20, color='3A4A5C', fill='F4F7FB', bar='B4C6E7'),
        'list': dict(left=480, hanging=300, bullet='•', bullet_color='2F5496'),
        'table': dict(sz=19, head_fill='2F5496', head_color='FFFFFF', head_fonts=fonts('Microsoft YaHei', '微软雅黑'),
                      zebra='F4F7FB', border='C9D3E0', inner='DCE3EC', border_sz=4, cell_mar=(60, 90)),
        'caption': dict(fonts=fonts('Microsoft YaHei', '微软雅黑'), sz=19, color='404040'),
    },
    'report': {
        'margins': (1440, 1440, 1440, 1440),
        'title': dict(fonts=fonts(FONT_HEI, FONT_HEI), sz=44, color='1F3864', rule='1F3864'),
        'h1': dict(fonts=fonts(FONT_HEI, FONT_HEI), sz=30, color='1F3864',
                   bar=None, before=240, after=120, line=312),
        'h2': dict(fonts=fonts(FONT_HEI, FONT_HEI), sz=25, color='1F3864',
                   before=180, after=75, line=312),
        'h3': dict(fonts=fonts(FONT_HEI, FONT_HEI), sz=21, color='1F3864',
                   before=120, after=30, line=336),
        'body': dict(fonts=fonts(FONT_SONG, FONT_SONG), sz=21, line=348, after=45, first=420),
        'quote': dict(fonts=fonts('KaiTi', '楷体'), sz=20, color='3A4A5C', fill='F5F8FC', bar='8EA9C8'),
        'list': dict(left=420, hanging=210, bullet='▪', bullet_color='1F3864'),
        'table': dict(sz=19, head_fill='E8EEF7', head_color='1F3864', head_fonts=fonts(FONT_HEI, FONT_HEI),
                      zebra='F5F8FC', border='8EA9C8', inner='8EA9C8', border_sz=6, cell_mar=(75, 150)),
        'caption': dict(fonts=fonts(FONT_HEI, FONT_HEI), sz=19, color='404040'),
    },
}

CAPTION_RE = re.compile(r'^(表|图)\s*\d+[　\s.、:：]')


def esc(t):
    return html.escape(t, quote=False)


# ----------------------------------------------------------------- 行内
def _split_code(text):
    """按 `行内代码` 切分：返回 [(片段, 是否代码)]。反引号不成对时整段按普通文字。"""
    parts = re.split(r'`([^`]+)`', text)
    out = []
    for k, seg in enumerate(parts):
        if seg:
            out.append((seg, k % 2 == 1))
    return out or [('', False)]


def runs(text, tkey=None, bold=False, color=None, size=None):
    """把 **粗体**、`行内代码` 与 <br> 转成 w:r 序列；tkey 为 None 时字体交给段落样式。"""
    base = (tkey or {}).get('fonts', '')
    out = []
    for line_idx, line in enumerate(text.split('<br>')):
        if line_idx:
            out.append('<w:r><w:br/></w:r>')
        for part in re.split(r'(\*\*.+?\*\*)', line):
            if not part:
                continue
            b = bold
            if part.startswith('**') and part.endswith('**') and len(part) > 4:
                part, b = part[2:-2], True
            for seg, is_code in _split_code(part):
                rpr = base
                if is_code:
                    rpr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
                if b:
                    rpr += '<w:b/><w:bCs/>'
                if color:
                    rpr += f'<w:color w:val="{color}"/>'
                if size:
                    rpr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
                rpr = f'<w:rPr>{rpr}</w:rPr>' if rpr else ''
                out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{esc(seg)}</w:t></w:r>')
    if not out:
        out.append('<w:r><w:t xml:space="preserve"></w:t></w:r>')
    return ''.join(out)


# ----------------------------------------------------------------- 块级
def para(style, text, T, extra_ppr='', bold=False):
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/>{extra_ppr}</w:pPr>{runs(text, None, bold)}</w:p>'


def bullet(text, T, ilvl=0, ordered=False):
    num_id = 2 if ordered else 1
    ppr = ('<w:pPr><w:pStyle w:val="ListParagraph"/>'
           f'<w:numPr><w:ilvl w:val="{min(ilvl, 2)}"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr>')
    return f'<w:p>{ppr}{runs(text)}</w:p>'


def hr(T):
    return ('<w:p><w:pPr><w:pStyle w:val="Normal"/>'
            '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="D0D7E5"/></w:pBdr>'
            '<w:ind w:firstLine="0"/></w:pPr></w:p>')


def disp_len(s):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


MIN_COL = 810   # 列宽下限：低于 54px（810twips）表头会竖排折行，见 SOP §6.3
CJK_RE = re.compile(r'[\u2e80-\u9fff\uff00-\uffef\u3000-\u303f]')
TOKEN_TWIPS = 130   # 9.5pt 表格字号下，1 个半角字符 ≈ 95twips；留 130 的抗折行余量
HEAD_TWIPS = 175    # 表头字符略宽松，留出加粗与字距余量


def _col_min(rows, c, cm_h):
    """一列的最小可用宽度：表头不折行 + 最长的不可断串（数字/型号/时段）不折行。

    中文可以在任意字间断行，所以只对"非中文连续串"和表头本身设下限。
    """
    pad = 2 * cm_h
    head_need = disp_len(rows[0][c]) * HEAD_TWIPS + pad
    body_need = 0
    for r in rows[1:]:
        cell = r[c].strip()
        # 中文可以在任意字间断行，但短标签（≤6 个汉字）折行反而难看：按最多 12 个
        # 半角单位设下限，既能容纳短标签，又不会把长描述列顶得过宽。
        body_need = max(body_need, min(disp_len(cell), 12) * TOKEN_TWIPS + pad)
        for tok in CJK_RE.split(cell):
            body_need = max(body_need, disp_len(tok.strip()) * TOKEN_TWIPS + pad)
    return max(MIN_COL, head_need, body_need)


def alloc_widths(weights, total_w, mins=None, min_w=MIN_COL):
    """按内容权重分列宽，且保证：① 总和精确等于 total_w；② 每列不低于 min_w。

    旧实现把窄列抬到下限后，把差额全部丢给最后一列，会出现
    "最后一列被压到 182twips"或"总宽超出内容宽 500twips"两种坏表。
    """
    n = len(weights)
    if not n:
        return []
    mins = list(mins) if mins else [min_w] * n
    if sum(mins) > total_w:                       # 列太多太挤：按比例压缩下限
        k = float(total_w) / sum(mins)
        mins = [max(1, int(m * k)) for m in mins]
    s = float(sum(weights)) or 1.0
    widths = [total_w * w / s for w in weights]

    # 迭代抬升：低于下限的列固定为下限，其余列按权重重新分配剩余宽度
    fixed = {}
    for _ in range(n):
        low = [i for i in range(n) if i not in fixed and widths[i] < mins[i]]
        if not low:
            break
        for i in low:
            fixed[i] = mins[i]
        rest = total_w - sum(fixed.values())
        rem = [i for i in range(n) if i not in fixed]
        if not rem or rest <= 0:
            break
        s2 = float(sum(weights[i] for i in rem)) or 1.0
        for i in rem:
            widths[i] = rest * weights[i] / s2

    out = [int(round(fixed.get(i, widths[i]))) for i in range(n)]
    # 取整余量从最宽的列往回收/补，不集中在最后一列
    diff = total_w - sum(out)
    order = sorted(range(n), key=lambda i: out[i], reverse=True)
    k = 0
    while diff != 0:
        i = order[k % n]
        step = 1 if diff > 0 else -1
        if out[i] + step >= mins[i]:
            out[i] += step
            diff -= step
        k += 1
        if k > 8 * n + 100:                        # 兜底：极端权重下放弃逐格微调
            out[order[0]] += diff
            break
    return out


def table(rows, T, total_w):
    if not rows:
        return ''
    ncol = max(len(r) for r in rows)
    rows = [r + [''] * (ncol - len(r)) for r in rows]
    weights = []
    for c in range(ncol):
        head_len = disp_len(rows[0][c])
        body_max = max(disp_len(r[c]) for r in rows[1:]) if len(rows) > 1 else 0
        weights.append(max(4, min(40, max(head_len, min(body_max, 40)))))
    # 「序号」这类短列强制收窄，避免占用正文宽度
    for c in range(ncol):
        if disp_len(rows[0][c]) <= 6 and all(len(r[c]) <= 3 for r in rows[1:]):
            weights[c] = 1
    cm_h = T['table']['cell_mar'][1]
    mins = [_col_min(rows, c, cm_h) for c in range(ncol)]
    widths = alloc_widths(weights, total_w, mins)

    t = T['table']
    cm_v, cm_h = t['cell_mar']
    border = ('<w:tblBorders>'
              + ''.join(f'<w:{side} w:val="single" w:sz="{t["border_sz"]}" w:space="0" w:color="{t["border"]}"/>'
                        for side in ('top', 'left', 'bottom', 'right'))
              + f'<w:insideH w:val="single" w:sz="{t["border_sz"]}" w:space="0" w:color="{t["inner"]}"/>'
              + f'<w:insideV w:val="single" w:sz="{t["border_sz"]}" w:space="0" w:color="{t["inner"]}"/>'
              + '</w:tblBorders>')

    xml = [f'<w:tbl><w:tblPr><w:tblW w:w="{total_w}" w:type="dxa"/>' + border +
           '<w:tblLayout w:type="fixed"/><w:tblCellMar>'
           f'<w:top w:w="{cm_v}" w:type="dxa"/><w:left w:w="{cm_h}" w:type="dxa"/>'
           f'<w:bottom w:w="{cm_v}" w:type="dxa"/><w:right w:w="{cm_h}" w:type="dxa"/>'
           '</w:tblCellMar></w:tblPr><w:tblGrid>']
    xml += [f'<w:gridCol w:w="{w}"/>' for w in widths]
    xml.append('</w:tblGrid>')

    for ri, row in enumerate(rows):
        head = (ri == 0)
        shade = t['head_fill'] if head else (t['zebra'] if ri % 2 == 0 else None)
        xml.append('<w:tr>')
        if head:
            # 只用 cantSplit + tblHeader（跨页重复表头）。
            # 不要在 trPr 里写 <w:jc>：它是"表行对齐"，会让表头行相对表体整体偏移，
            # Word/WPS 处理不一致，肉眼就是"第一排是歪的"。表头文字居中由
            # TableHead 段落样式的 jc=center 负责。
            xml.append('<w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>')
        for ci, cell in enumerate(row):
            tcpr = f'<w:tcW w:w="{widths[ci]}" w:type="dxa"/>'
            if shade:
                tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
            tcpr += '<w:vAlign w:val="center"/>'
            if head:
                txt = runs(cell, dict(fonts=t['head_fonts']), bold=True,
                           color=t['head_color'], size=t['sz'])
                xml.append(f'<w:tc><w:tcPr>{tcpr}</w:tcPr>'
                           f'<w:p><w:pPr><w:pStyle w:val="TableHead"/></w:pPr>{txt}</w:p></w:tc>')
            else:
                txt = runs(cell, None, size=t['sz'])
                xml.append(f'<w:tc><w:tcPr>{tcpr}</w:tcPr>'
                           f'<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr>{txt}</w:p></w:tc>')
        xml.append('</w:tr>')
    xml.append('</w:tbl>')
    # 表格后只补一个极小的空段落，避免与下一段粘连（Univer 路线这里会留 2 个空段，见 SOP §8-P7）
    xml.append('<w:p><w:pPr><w:pStyle w:val="Normal"/>'
               '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
               '<w:ind w:firstLine="0"/><w:rPr><w:sz w:val="8"/></w:rPr></w:pPr></w:p>')
    return ''.join(xml)


# ----------------------------------------------------------------- 解析
def parse(md, T, total_w):
    lines = md.split('\n')
    body = []
    i, n = 0, len(md.split('\n'))
    heading_style = {1: 'Title', 2: 'Heading1', 3: 'Heading2', 4: 'Heading3'}
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s.startswith('<!--'):
            if 'page-break' in s:
                body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            i += 1
            continue
        if re.fullmatch(r'-{3,}', s):
            body.append(hr(T))
            i += 1
            continue
        if s.startswith('```'):
            # 代码围栏：整段原样进 Code 样式，不做 markdown 解析（否则 # 会被当标题、
            # 行首 - 会被当列表，技术文档会被拆烂）
            i += 1
            while i < n and not lines[i].strip().startswith('```'):
                raw_line = lines[i].replace('\t', '    ')
                body.append('<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr>'
                            '<w:r><w:t xml:space="preserve">' + esc(raw_line or ' ') +
                            '</w:t></w:r></w:p>')
                i += 1
            i += 1                       # 跳过收尾围栏
            continue
        m = re.match(r'^(#{1,6})\s+(.*)$', s)
        if m:
            body.append(para(heading_style.get(len(m.group(1)), 'Heading3'), m.group(2).strip(), T))
            i += 1
            continue
        if s.startswith('|'):
            rows = []
            while i < n and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if not re.fullmatch(r'[\s:\-|]*', '|'.join(cells)):
                    rows.append(cells)
                i += 1
            body.append(table(rows, T, total_w))
            continue
        if s.startswith('>'):
            buf = []
            while i < n and lines[i].strip().startswith('>'):
                t = lines[i].strip()[1:].strip()
                if t:
                    buf.append(t)
                i += 1
            for t in buf:
                body.append(para('Quote', t, T))
            continue
        if CAPTION_RE.match(s) and len(s) <= 40:
            body.append(para('Caption', s, T))
            i += 1
            continue
        m = re.match(r'^(\s*)[-*+]\s+(.*)$', lines[i])
        if m:
            body.append(bullet(m.group(2), T, ilvl=len(m.group(1)) // 2))
            i += 1
            continue
        m = re.match(r'^(\s*)\d+\.\s+(.*)$', lines[i])
        if m:
            body.append(bullet(m.group(2), T, ilvl=len(m.group(1)) // 2, ordered=True))
            i += 1
            continue
        body.append(para('Normal', s, T))
        i += 1
    return ''.join(body)


# ----------------------------------------------------------------- 静态部件
def build_styles(T):
    title, h1, h2, h3, body = T['title'], T['h1'], T['h2'], T['h3'], T['body']
    bar = (lambda c, sz: f'<w:pBdr><w:left w:val="single" w:sz="{sz}" w:space="6" w:color="{c}"/></w:pBdr>')
    quote, cap, tb = T['quote'], T['caption'], T['table']
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {NS_W}>
<w:docDefaults>
  <w:rPrDefault><w:rPr>{body['fonts']}<w:sz w:val="{body['sz']}"/><w:szCs w:val="{body['sz']}"/>
    <w:lang w:val="en-US" w:eastAsia="zh-CN" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>
  <w:pPrDefault><w:pPr><w:spacing w:line="{body['line']}" w:lineRule="auto"/>
    <w:jc w:val="both"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>
  <w:pPr><w:spacing w:before="0" w:after="{body['after']}" w:line="{body['line']}" w:lineRule="auto"/>
    <w:ind w:firstLineChars="200" w:firstLine="{body['first']}"/></w:pPr>
  <w:rPr><w:sz w:val="{body['sz']}"/><w:szCs w:val="{body['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:pBdr><w:bottom w:val="single" w:sz="12" w:space="6" w:color="{title['rule']}"/></w:pBdr>
    <w:spacing w:before="240" w:after="120" w:line="320" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:jc w:val="center"/></w:pPr>
  <w:rPr>{title['fonts']}<w:b/><w:color w:val="{title['color']}"/>
    <w:sz w:val="{title['sz']}"/><w:szCs w:val="{title['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
  <w:pPr><w:keepNext/>{bar(h1['bar'], 24) if h1.get('bar') else ''}
    <w:spacing w:before="{h1['before']}" w:after="{h1['after']}" w:line="{h1['line']}" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:outlineLvl w:val="0"/></w:pPr>
  <w:rPr>{h1['fonts']}<w:b/><w:color w:val="{h1['color']}"/>
    <w:sz w:val="{h1['sz']}"/><w:szCs w:val="{h1['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
  <w:pPr><w:keepNext/><w:spacing w:before="{h2['before']}" w:after="{h2['after']}" w:line="{h2['line']}" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:outlineLvl w:val="1"/></w:pPr>
  <w:rPr>{h2['fonts']}<w:b/><w:color w:val="{h2['color']}"/>
    <w:sz w:val="{h2['sz']}"/><w:szCs w:val="{h2['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
  <w:pPr><w:keepNext/><w:spacing w:before="{h3['before']}" w:after="{h3['after']}" w:line="{h3['line']}" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:outlineLvl w:val="2"/></w:pPr>
  <w:rPr>{h3['fonts']}<w:b/><w:color w:val="{h3['color']}"/>
    <w:sz w:val="{h3['sz']}"/><w:szCs w:val="{h3['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="20" w:after="20" w:line="320" w:lineRule="auto"/>
    <w:ind w:left="{T['list']['left']}" w:hanging="{T['list']['hanging']}" w:firstLine="0"/></w:pPr>
  <w:rPr><w:sz w:val="{body['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="{quote['bar']}"/></w:pBdr>
    <w:shd w:val="clear" w:color="auto" w:fill="{quote['fill']}"/>
    <w:spacing w:before="80" w:after="80" w:line="320" w:lineRule="auto"/>
    <w:ind w:left="360" w:right="240" w:firstLine="0"/></w:pPr>
  <w:rPr>{quote['fonts']}<w:color w:val="{quote['color']}"/>
    <w:sz w:val="{quote['sz']}"/><w:szCs w:val="{quote['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
  <w:pPr><w:keepNext/><w:spacing w:before="120" w:after="45" w:line="288" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:jc w:val="center"/></w:pPr>
  <w:rPr>{cap['fonts']}<w:b/><w:color w:val="{cap['color']}"/>
    <w:sz w:val="{cap['sz']}"/><w:szCs w:val="{cap['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="20" w:after="20" w:line="264" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:jc w:val="left"/></w:pPr>
  <w:rPr><w:sz w:val="{tb['sz']}"/><w:szCs w:val="{tb['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableHead"><w:name w:val="Table Head"/><w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="30" w:after="30" w:line="264" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0"/><w:jc w:val="center"/></w:pPr>
  <w:rPr>{tb['head_fonts']}<w:b/><w:color w:val="{tb['head_color']}"/>
    <w:sz w:val="{tb['sz']}"/><w:szCs w:val="{tb['sz']}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:qFormat/>
  <w:pPr><w:shd w:val="clear" w:color="auto" w:fill="F4F6F9"/>
    <w:spacing w:before="0" w:after="0" w:line="252" w:lineRule="auto"/>
    <w:ind w:firstLineChars="0" w:firstLine="0" w:left="0"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="等线"/>
    <w:sz w:val="17"/><w:szCs w:val="17"/></w:rPr></w:style>
</w:styles>'''


def build_numbering(T):
    bullet = T['list']['bullet']
    left, hang = T['list']['left'], T['list']['hanging']
    lvls = []
    for lv in range(3):
        lvls.append(
            f'<w:lvl w:ilvl="{lv}"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
            f'<w:lvlText w:val="{bullet}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="{left + lv * 420}" w:hanging="{hang}"/></w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:hint="default"/>'
            f'<w:color w:val="{T["list"]["bullet_color"]}"/></w:rPr></w:lvl>')
    nl = ''.join(lvls)
    dl = ''.join(
        f'<w:lvl w:ilvl="{lv}"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        f'<w:lvlText w:val="%{lv + 1}."/><w:lvlJc w:val="left"/>'
        f'<w:pPr><w:ind w:left="{left + lv * 420}" w:hanging="{hang}"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:hint="default"/></w:rPr></w:lvl>'
        for lv in range(3))
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering {NS_W}>
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{nl}</w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{dl}</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''


FOOTER = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr {NS_W} {NS_R}>
<w:p><w:pPr><w:spacing w:before="0" w:after="0"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr>
  <w:r><w:rPr><w:color w:val="8C8C8C"/><w:sz w:val="18"/></w:rPr><w:t xml:space="preserve">— </w:t></w:r>
  <w:r><w:fldChar w:fldCharType="begin"/></w:r>
  <w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
  <w:r><w:fldChar w:fldCharType="separate"/></w:r>
  <w:r><w:rPr><w:color w:val="8C8C8C"/><w:sz w:val="18"/></w:rPr><w:t>1</w:t></w:r>
  <w:r><w:fldChar w:fldCharType="end"/></w:r>
  <w:r><w:rPr><w:color w:val="8C8C8C"/><w:sz w:val="18"/></w:rPr><w:t xml:space="preserve"> —</w:t></w:r>
</w:p></w:ftr>'''

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''

RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''

DOC_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
</Relationships>'''


def sect(T):
    top, bottom, left, right = T['margins']
    return ('<w:sectPr><w:footerReference w:type="default" r:id="rId3"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            f'<w:pgMar w:top="{top}" w:right="{right}" w:bottom="{bottom}" w:left="{left}" '
            'w:header="851" w:footer="992" w:gutter="0"/>'
            '<w:cols w:space="425"/><w:docGrid w:type="lines" w:linePitch="312"/></w:sectPr>')


def main():
    argv = [a for a in sys.argv[1:]]
    theme_name = 'classic'
    if '--theme' in argv:
        idx = argv.index('--theme')
        theme_name = argv[idx + 1]
        del argv[idx:idx + 2]
    if len(argv) < 2:
        print('用法: python md2docx.py in.md out.docx ["标题"] [--theme classic|report]')
        sys.exit(2)
    src, dst = argv[0], argv[1]
    title = argv[2] if len(argv) > 2 else os.path.splitext(os.path.basename(src))[0]
    T = THEMES[theme_name]

    md = open(src, encoding='utf-8').read()
    total_w = 11906 - T['margins'][2] - T['margins'][3]
    body = parse(md, T, total_w)

    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<w:document {NS_W} {NS_R}><w:body>{body}{sect(T)}</w:body></w:document>')

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>{esc(title)}</dc:title><dc:creator>DeepSeek Harness</dc:creator>
<cp:lastModifiedBy>DeepSeek Harness</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>'''

    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Microsoft Office Word</Application><DocSecurity>0</DocSecurity>
<ScaleCrop>false</ScaleCrop><Company></Company><LinksUpToDate>false</LinksUpToDate>
<SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged>
<AppVersion>16.0000</AppVersion></Properties>'''

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', RELS)
        z.writestr('word/document.xml', document)
        z.writestr('word/styles.xml', build_styles(T))
        z.writestr('word/numbering.xml', build_numbering(T))
        z.writestr('word/footer1.xml', FOOTER)
        z.writestr('word/_rels/document.xml.rels', DOC_RELS)
        z.writestr('docProps/core.xml', core)
        z.writestr('docProps/app.xml', app)
    print('written %s（theme=%s，%d 字节）' % (dst, theme_name, os.path.getsize(dst)))


if __name__ == '__main__':
    main()
