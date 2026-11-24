# -*- coding: utf-8 -*-
"""
把 AI 听记原文（UTF-16LE）按原有结构重排为 Markdown。
原则：正文文字逐字取自原文，不增、不删、不改写；只增加标题层级、列表与表格等结构。
用法: python format_text.py <src.txt> <out.md>
"""
import re
import sys

TIME_RE = re.compile(r'^\d{2}:\d{2}\s*[–—\-]\s*\d{2}:\d{2}$')
NUM_RE = re.compile(r'^\d+$')
PUNCT_END = ('。', '”', '！', '？', '……', '…')


class W:
    """极简 markdown 写出器：自动管理列表与段落之间的空行。"""

    def __init__(self):
        self.lines = []
        self.after_list = False

    def _sep(self):
        if self.lines and self.lines[-1] != '':
            self.lines.append('')

    def blank(self):
        self.lines.append('')
        self.after_list = False

    def h(self, level, text):
        self._sep()
        self.lines.append('#' * level + ' ' + text)
        self.lines.append('')
        self.after_list = False

    def para(self, text):
        if self.after_list:
            self.lines.append('')
            self.after_list = False
        self.lines.append(text)
        self.lines.append('')

    def bold(self, text):
        if self.after_list:
            self.lines.append('')
            self.after_list = False
        self.lines.append('**' + text + '**')
        self.lines.append('')

    def bullet(self, text):
        self.lines.append('- ' + text)
        self.after_list = True

    def end_list(self):
        if self.after_list:
            self.lines.append('')
            self.after_list = False

    def quote(self, blocks):
        """blocks: [(head|None, text)]"""
        self.end_list()
        self._sep()
        for k, (head, text) in enumerate(blocks):
            if k:
                self.lines.append('>')
            if head:
                self.lines.append('> ' + head)
                self.lines.append('>')
            self.lines.append('> ' + text)
        self.lines.append('')

    def table(self, rows, header=True):
        self.end_list()
        self._sep()
        ncol = max(len(r) for r in rows)
        rows = [r + [''] * (ncol - len(r)) for r in rows]
        self.lines.append('| ' + ' | '.join(rows[0]) + ' |')
        self.lines.append('|' + '---|' * ncol)
        for r in rows[1:]:
            self.lines.append('| ' + ' | '.join(r) + ' |')
        self.lines.append('')

    def raw(self, text):
        self.lines.append(text)

    def out(self):
        txt = '\n'.join(self.lines)
        txt = re.sub(r'\n{3,}', '\n\n', txt).strip() + '\n'
        return txt


def load(path):
    with open(path, 'r', encoding='utf-16', newline='') as f:
        raw = f.read()
    # 原文行尾为 \r\r\n，先统一为 \n
    raw = raw.replace('\r\r\n', '\n').replace('\r\n', '\n').replace('\r', '\n')
    raw = raw.lstrip('\ufeff')
    return [l.strip().replace('\u3000', ' ').strip() for l in raw.splitlines()]


def is_body(line):
    return line.endswith(PUNCT_END) or line.endswith('。')


def render_section(w, num, title, content):
    w.h(3, f'{num}. {title}')

    if '关键决策' in title:
        for line in content:
            if not line:
                continue
            if is_body(line):
                if line.startswith('已确认：'):
                    w.bullet('**已确认**：' + line[len('已确认：'):])
                elif line.startswith('待确认：'):
                    w.bullet('**待确认**：' + line[len('待确认：'):])
                else:
                    w.bullet(line)
            else:
                w.end_list()
                w.bold(line)
        w.end_list()
        return

    if '核心发言' in title:
        i = 0
        n = len(content)
        while i < n:
            line = content[i]
            if not line:
                i += 1
                continue
            if line in ('🎙️', '🎙'):
                sp = content[i + 1] if i + 1 < n else ''
                qt = content[i + 2] if i + 2 < n else ''
                # 跳过可能的二次 🎙️
                w.quote([(f'🎙️ **{sp}**', qt)])
                i += 3
                continue
            if line in ('”', '“', '"', '＂'):
                qt = content[i + 1] if i + 1 < n else ''
                att = ''
                step = 2
                if i + 2 < n and content[i + 2].startswith('——'):
                    att = content[i + 2]
                    step = 3
                blocks = [(None, qt)]
                if att:
                    blocks.append((None, att))
                w.quote(blocks)
                i += step
                continue
            if not is_body(line):
                w.end_list()
                w.bold(line)
            else:
                w.bullet(line)
            i += 1
        w.end_list()
        return

    if '洞察' in title:
        for line in content:
            if line:
                w.bullet(line)
        w.end_list()
        return

    # 普通内容段：无句末标点的行视为小标题
    for line in content:
        if not line:
            continue
        if is_body(line):
            w.para(line)
        else:
            w.end_list()
            w.bold(line)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    lines = load(src)

    # ---------- 头部元信息 ----------
    title = lines[1]
    dt = lines[2]
    theme = lines[3]
    i_num = lines.index('议题数')
    tags = [x for x in lines[4:i_num] if x]
    n_topics = lines[i_num + 1]
    dur = lines[i_num + 3]
    i_out = lines.index('大纲')

    outline = []
    j = i_out + 1
    while j < len(lines) and not TIME_RE.match(lines[j]):
        if lines[j]:
            outline.append((lines[j], lines[j + 1] if j + 1 < len(lines) else ''))
            j += 2
        else:
            j += 1

    w = W()
    w.h(1, title)
    w.para(f'> 战略会议 · {dt} · 议题 {n_topics} 项 · 听记时长 {dur}')
    w.quote([(None, '**整理说明**：本文为会议听记原文的结构化整理。正文文字、数字、时间戳与摘要均逐字取自原文，'
                    '未作改写、增补或润色；仅增加标题层级、列表、表格等结构，并将原文各小节的序号并入标题。'
                    '原文中的界面元素（如"添加待办"按钮）不属于正文内容，未列入。'
                    '原文中疑似转写误差的用词（如"闭嘴控""ADC"等）保持原样，核实清单见'
                    '《公司战略转型与目标复盘-核心信息提炼》。')])

    w.h(2, '一、会议基本信息')
    w.table([
        ['项目', '原文内容'],
        ['会议类型', lines[0]],
        ['会议标题', title],
        ['时间', dt],
        ['主题', theme],
        ['标签', ' · '.join(tags)],
        ['议题数', n_topics],
        ['听记时长', dur],
    ])

    w.h(2, '二、会议大纲')
    w.table([['议题', '时段']] + [[a, b] for a, b in outline])

    # ---------- 正文：按时间块 ----------
    k = j

    block_no = 0
    while k < len(lines):
        if not TIME_RE.match(lines[k]):
            k += 1
            continue
        block_no += 1
        tspan = lines[k]
        k += 1
        btitle = lines[k]
        k += 1
        summary = []
        while (k < len(lines) and not NUM_RE.match(lines[k]) and not TIME_RE.match(lines[k])
               and lines[k] != '待办'):
            if lines[k]:
                summary.append(lines[k])
            k += 1

        w.h(2, f'{tspan}｜{btitle}')
        for s in summary:
            w.para(s)

        # 各小节
        while k < len(lines) and NUM_RE.match(lines[k]):
            num = lines[k]
            k += 1
            stitle = lines[k]
            k += 1
            content = []
            while (k < len(lines) and not NUM_RE.match(lines[k]) and not TIME_RE.match(lines[k])
                   and lines[k] != '待办'):
                if lines[k]:
                    content.append(lines[k])
                k += 1
            render_section(w, num, stitle, content)

    # ---------- 待办 ----------
    if '待办' in lines:
        idx = len(lines) - 1 - lines[::-1].index('待办')
        rest = [x for x in lines[idx + 1:] if x]
        w.h(2, '三、待办')
        for x in rest:
            if x in ('添加待办',):
                continue
            if x.startswith('AI生成') or x.startswith('AI 生成'):
                continue
            w.bullet(x)
        w.end_list()
        w.blank()
        w.raw('> 原文末尾提示：AI生成可能有误，请注意核实细节。')

    with open(dst, 'w', encoding='utf-8') as f:
        f.write(w.out())
    print('written', dst)


if __name__ == '__main__':
    main()
