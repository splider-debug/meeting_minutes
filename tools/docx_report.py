# -*- coding: utf-8 -*-
"""
docx 版式体检（轻量版）：不解析全文，只报告"骨架"。
适合快速确认：A4 没变、页脚在不在、样式引用是否集中在预期的那几个。

用法: python docx_report.py a.docx b.docx ...
"""
import re
import sys
import zipfile


def report(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    d = z.read('word/document.xml').decode('utf-8')
    styles = z.read('word/styles.xml').decode('utf-8') if 'word/styles.xml' in names else ''

    # 注意：Word/WPS 保存的段落带 w14:paraId 等属性，不能用 <w:p> 精确匹配
    n_para = len(re.findall(r'<w:p[ >]', d))
    n_tbl = len(re.findall(r'<w:tbl[ >]', d))
    n_num = len(re.findall(r'<w:numPr[ >]', d))
    sect = re.search(r'<w:sectPr.*?</w:sectPr>', d, re.S)
    counts = {}
    for m in re.finditer(r'<w:pStyle w:val="([^"]+)"/>', d):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1

    print('== %s' % path)
    print('  部件 %d 个；页脚 %s' % (len(names), [n for n in names if n.startswith('word/footer')] or '无'))
    print('  段落 %d｜表格 %d｜编号段 %d' % (n_para, n_tbl, n_num))
    if sect:
        pg = re.search(r'<w:pgSz[^>]*>', sect.group(0))
        mar = re.search(r'<w:pgMar[^>]*>', sect.group(0))
        print('  pgSz: %s' % (pg.group(0) if pg else None))
        print('  pgMar: %s' % ((mar.group(0)[:100] + '...') if mar else None))
        print('  footerReference: %s' % ('有' if 'footerReference' in sect.group(0) else '无'))
    else:
        print('  sectPr: 缺失（段落级分节可能导致页面尺寸异常）')
    if counts:
        print('  段落样式: ' + ', '.join('%s×%d' % (k, v) for k, v in
                                     sorted(counts.items(), key=lambda x: -x[1])))
    else:
        print('  段落样式: 全部直接格式（无 pStyle）——这类文档改版式时要按字号/加粗定位')
    print('  定义样式 %d 个' % len(re.findall(r'w:styleId="', styles)))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python docx_report.py a.docx [b.docx ...]')
        sys.exit(2)
    for p in sys.argv[1:]:
        report(p)
