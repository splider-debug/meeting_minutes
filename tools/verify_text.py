# -*- coding: utf-8 -*-
"""校验：原文每一行是否都完整出现在生成的 markdown 中。

用法: python verify_text.py <src.txt> <out.md>
出口码: 0 全覆盖 / 1 有原文行没落到 md（或在 preflight 里被当作失败项）
"""
import sys

from format_text import load, TIME_RE, NUM_RE

SKIP = {'待办', '大纲', '议题数', '听记时长', '添加待办',
        'AI生成可能有误，请注意核实细节', '🎙️', '”', '“', '"'}


def check(src, dst):
    """返回 (checked, missing)；missing 是原文里没在 md 中出现的行。"""
    lines = load(src)
    md = open(dst, encoding='utf-8').read()
    missing = []
    checked = 0
    for l in lines:
        if not l or l in SKIP or NUM_RE.match(l) or TIME_RE.match(l):
            continue
        checked += 1
        if l in md:
            continue
        # 允许去掉"已确认：/待确认："前缀后匹配（正文中作为列表项加粗前缀）
        for pre in ('已确认：', '待确认：'):
            if l.startswith(pre) and l[len(pre):] in md:
                break
        else:
            missing.append(l)
    return checked, missing


def main():
    src, dst = sys.argv[1], sys.argv[2]
    checked, missing = check(src, dst)
    print(f'原文有效行 {checked} 行，未匹配 {len(missing)} 行')
    for m in missing:
        print('  MISSING:', m)
    if missing:
        sys.exit(1)


if __name__ == '__main__':
    main()
