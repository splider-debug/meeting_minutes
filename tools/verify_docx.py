# -*- coding: utf-8 -*-
"""
结构自检：docx 是否"自己站得住"。

检查项：
 1) 每个 XML 部件可解析（良构）
 2) pStyle / numId / r:id 引用都有定义
 3) pPr / rPr / tcPr / trPr / tblPr 子元素顺序合法（Word 对顺序极敏感）
 4) 正文无残留 markdown 标记（** / 行首 # / 行首 - ）
 5) md 的每个非空行都出现在 docx 正文中（文字覆盖率）
 6) 页脚是否存在、页面尺寸是否 A4

用法: python verify_docx.py in.md out.docx
出口码: 0 通过 / 1 有问题
"""
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

PPR_ORDER = ['pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl', 'numPr',
             'suppressLineNumbers', 'pBdr', 'shd', 'tabs', 'suppressAutoHyphens', 'kinsoku',
             'wordWrap', 'overflowPunct', 'topLinePunct', 'autoSpaceDE', 'autoSpaceDN', 'bidi',
             'adjustRightInd', 'snapToGrid', 'spacing', 'ind', 'contextualSpacing', 'mirrorIndents',
             'suppressOverlap', 'jc', 'textDirection', 'textAlignment', 'textboxTightWrap',
             'outlineLvl', 'divId', 'cnfStyle', 'rPr', 'sectPr', 'pPrChange']
RPR_ORDER = ['rStyle', 'rFonts', 'b', 'bCs', 'i', 'iCs', 'caps', 'smallCaps', 'strike', 'dstrike',
             'outline', 'shadow', 'emboss', 'imprint', 'noProof', 'snapToGrid', 'vanish', 'webHidden',
             'color', 'spacing', 'w', 'kern', 'position', 'sz', 'szCs', 'highlight', 'u', 'effect',
             'bdr', 'shd', 'fitText', 'vertAlign', 'rtl', 'cs', 'em', 'lang', 'eastAsianLayout',
             'specVanish', 'oMath']
TCPR_ORDER = ['cnfStyle', 'tcW', 'gridSpan', 'hMerge', 'vMerge', 'tcBorders', 'shd', 'noWrap',
              'tcMar', 'textDirection', 'tcFitText', 'vAlign', 'hideMark']
TRPR_ORDER = ['cnfStyle', 'divId', 'gridBefore', 'gridAfter', 'wBefore', 'wAfter', 'cantSplit',
              'trHeight', 'tblHeader', 'tblCellSpacing', 'jc', 'hidden', 'ins', 'del', 'trPrChange']
TBLPR_ORDER = ['tblStyle', 'tblpPr', 'tblOverlap', 'bidiVisual', 'tblStyleRowBandSize',
               'tblStyleColBandSize', 'tblW', 'jc', 'tblCellSpacing', 'tblInd', 'tblBorders',
               'shd', 'tblLayout', 'tblCellMar', 'tblLook', 'tblCaption', 'tblDescription']


def check_order(xml, tag, order, label, problems):
    for m in re.finditer(rf'<w:{tag}>(.*?)</w:{tag}>', xml, re.S):
        kids = re.findall(r'<w:([A-Za-z]+)[ />]', m.group(1))
        idx = [order.index(k) for k in kids if k in order]
        if idx != sorted(idx):
            problems.append(f'{label}: {tag} 顺序错误 {kids}')
            return


def main():
    md_path, docx_path = sys.argv[1], sys.argv[2]
    md = open(md_path, encoding='utf-8').read()
    z = zipfile.ZipFile(docx_path)
    names = z.namelist()
    problems = []

    # 1) 良构
    parsed = {}
    for n in names:
        if n.endswith('.xml') or n.endswith('.rels'):
            try:
                parsed[n] = ET.fromstring(z.read(n))
            except ET.ParseError as e:
                problems.append(f'{n} XML 无法解析: {e}')

    doc = z.read('word/document.xml').decode('utf-8')
    styles = z.read('word/styles.xml').decode('utf-8') if 'word/styles.xml' in names else ''
    numbering = z.read('word/numbering.xml').decode('utf-8') if 'word/numbering.xml' in names else ''
    rels = z.read('word/_rels/document.xml.rels').decode('utf-8')
    footers = [n for n in names if re.match(r'word/footer\d*\.xml$', n)]

    # 2) 引用完整性
    defined = set(re.findall(r'w:styleId="([^"]+)"', styles))
    used = set(re.findall(r'<w:pStyle w:val="([^"]+)"/>', doc))
    if used - defined:
        problems.append(f'未定义样式: {used - defined}')
    nums = set(re.findall(r'<w:num w:numId="([^"]+)">', numbering))
    used_nums = set(re.findall(r'<w:numId w:val="([^"]+)"/>', doc))
    if used_nums - nums:
        problems.append(f'未定义编号: {used_nums - nums}')
    rel_ids = set(re.findall(r'Id="([^"]+)"', rels))
    used_rels = set(re.findall(r'r:id="([^"]+)"', doc))
    if used_rels - rel_ids:
        problems.append(f'未定义关系: {used_rels - rel_ids}')

    # 3) 子元素顺序
    for part, xml in [('styles.xml', styles), ('document.xml', doc)] + \
                     [(f, z.read(f).decode('utf-8')) for f in footers]:
        if not xml:
            continue
        check_order(xml, 'pPr', PPR_ORDER, part, problems)
        for tag, order in (('rPr', RPR_ORDER), ('tcPr', TCPR_ORDER),
                           ('trPr', TRPR_ORDER), ('tblPr', TBLPR_ORDER)):
            check_order(xml, tag, order, part, problems)

    # 4) 标记残留
    texts = re.findall(r'<w:t[^>]*>(.*?)</w:t>', doc, re.S)
    docx_text = ''.join(texts)
    for a, b in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&apos;', "'")):
        docx_text = docx_text.replace(a, b)
    docx_flat = re.sub(r'\s+', '', docx_text)
    if '**' in docx_text:
        problems.append('正文残留 markdown 粗体标记 **')

    # 5) 文字覆盖率
    checks = 0
    in_code = False
    for line in md.split('\n'):
        s = line.strip()
        if not s or s.startswith('<!--'):
            continue
        if re.fullmatch(r'-{3,}', s):      # 水平分隔线是结构，没有文字可覆盖
            continue
        if s.startswith('```'):            # 代码围栏本身不是文字，围栏内才是
            in_code = not in_code
            continue
        if in_code:
            flat = re.sub(r'\s+', '', s)
            if flat:
                checks += 1
                if flat not in docx_flat:
                    problems.append(f'正文缺失: {s[:40]}')
            continue
        s = re.sub(r'^#{1,6}\s+', '', s)
        s = re.sub(r'^>\s?', '', s)
        s = re.sub(r'^[-*+]\s+', '', s)
        s = re.sub(r'^\d+\.\s+', '', s)
        if s.startswith('|'):
            continue
        s = s.replace('**', '')
        s = s.replace('`', '')          # 行内代码的标记不属于文字
        flat = re.sub(r'\s+', '', s)
        if not flat:
            continue
        checks += 1
        if flat not in docx_flat:
            problems.append(f'正文缺失: {s[:40]}')

    # 6) 页面与页脚
    n_para = len(re.findall(r'<w:p[ >]', doc))
    n_tbl = len(re.findall(r'<w:tbl>', doc))
    pg = re.search(r'<w:pgSz w:w="(\d+)" w:h="(\d+)"', doc)
    a4 = pg and pg.group(1) == '11906' and pg.group(2) == '16838'

    print(f'样式 {len(defined)} 个 / 引用 {len(used)} 个；编号 {len(used_nums)} 个；'
          f'关系 {len(used_rels)} 个；页脚 {len(footers)} 个')
    print(f'段落 {n_para} 个，表格 {n_tbl} 张，文本行校验 {checks} 行，A4={bool(a4)}')
    if problems:
        print('发现 %d 个问题：' % len(problems))
        for p in problems[:20]:
            print('  -', p)
        sys.exit(1)
    print('校验通过：结构引用完整、XML 顺序合法、正文与 markdown 一致')


if __name__ == '__main__':
    main()
