# -*- coding: utf-8 -*-
"""一键预检：新会话开工前跑一次，约 1 分钟确认工具链完好。

用法:
  python tools\\preflight.py                  # 内置样本回归（硬基线，见下）
  python tools\\preflight.py 新原件.docx       # 抽查某个新文件能否保真转写 + 渲染

内置基线（2026-11-19 实测值，不要随手改）:
  261118 ..._用户修改.docx  → 41 标题 / 4 表 / 7489 字符 identical
  261111 文本.txt           → 原文 314 行全覆盖（0 未匹配）

出口码: 0 全绿 / 1 有失败项（失败项会打印具体是哪一步）
原则: 只读原件；所有中间产物写到 <工作区>\\_preflight\\，结束后删除。
"""
import contextlib
import io
import os
import shutil
import sys
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import docx2md          # noqa: E402
import format_text      # noqa: E402
import md2docx          # noqa: E402
import outline_docx     # noqa: E402
import verify_docx      # noqa: E402
import verify_text      # noqa: E402

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
TMP = os.path.join(ROOT, '_preflight')

# ---- 回归基线（改了工具或换了样本才动这里）----
BASE_DOCX = os.path.join(
    ROOT, '261118_黑龙江冰凌监测项目会议资料整理',
    '黑龙江冰凌监测项目会议资料整理_用户修改.docx')
BASE_DOCX_CHARS = 7489
BASE_DOCX_HEADINGS = 41
BASE_DOCX_TABLES = 4
BASE_TXT = os.path.join(
    ROOT, '261111_公司战略转型与目标复盘', '文本.txt')

SCRIPTS = ['docx_dump.py', 'docx2md.py', 'md2docx.py', 'verify_docx.py',
           'docx_report.py', 'outline_docx.py', 'format_text.py',
           'verify_text.py']

results = []   # (ok: bool, label: str, detail: str)


def record(ok, label, detail=''):
    results.append((bool(ok), label, detail))
    print('  %s %s%s' % ('PASS' if ok else 'FAIL', label,
                         ('　— ' + detail) if detail else ''))
    return ok


def section(title):
    print('\n== %s ==' % title)


def run_main(fn, argv):
    """在进程内调用某脚本的 main()，捕获其 stdout 与出口码（不启子进程，避开沙箱管道限制）。"""
    old_argv = sys.argv
    buf = io.StringIO()
    code = 0
    try:
        sys.argv = ['script'] + list(argv)
        with contextlib.redirect_stdout(buf):
            fn()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:                       # 脚本内部异常也算失败项
        code = 1
        buf.write('\n[异常] %s: %s' % (type(e).__name__, e))
    finally:
        sys.argv = old_argv
    return code, buf.getvalue()


def count_docx(doc):
    body = doc.find(W + 'body')
    return len(list(body.iter(W + 'tbl')))


def md_headings(md):
    return sum(1 for line in md.split('\n') if line.startswith('#'))


def check_docx2md(path, tag, exp_chars=None, exp_head=None, exp_tbl=None):
    """产线 B 核心回归：docx → md 必须 identical，且标题/表格数与记录一致。"""
    if not os.path.isfile(path):
        return record(False, '%s：样本不存在' % tag, path)
    if not zipfile.is_zipfile(path):
        return record(False, '%s：不是 docx（不是 zip 包）' % tag, path)
    try:
        md, doc = docx2md.convert(path, False, False, False)
        ok, msg = docx2md.verify(doc, md)
    except Exception as e:
        return record(False, '%s：转写异常' % tag, '%s: %s' % (type(e).__name__, e))
    n_head, n_tbl = md_headings(md), count_docx(doc)
    chars = int(msg.split('（')[1].split(' ')[0]) if '（' in msg else -1

    good = ok
    if exp_chars is not None and chars != exp_chars:
        good = False
        msg += '（基线 %d 字符，实测 %d）' % (exp_chars, chars)
    if exp_head is not None and n_head != exp_head:
        good = False
        msg += '（基线 %d 标题，实测 %d）' % (exp_head, n_head)
    if exp_tbl is not None and n_tbl != exp_tbl:
        good = False
        msg += '（基线 %d 表，实测 %d）' % (exp_tbl, n_tbl)
    return record(good, '%s：逐字转写' % tag,
                  '%s ｜ 标题 %d ｜ 表 %d' % (msg, n_head, n_tbl))


def check_render(md_path, tag):
    """产线 A/B 共用出口：md → docx → verify_docx → outline_docx。"""
    if not os.path.isfile(md_path):
        return record(False, '%s：没有 md 可渲染' % tag)
    os.makedirs(TMP, exist_ok=True)
    out = os.path.join(TMP, 'render.docx')
    code, detail = run_main(md2docx.main, [md_path, out, '预检交付稿', '--theme', 'report'])
    if not record(code == 0 and os.path.isfile(out), '%s：md2docx 渲染' % tag,
                  detail.strip().splitlines()[-1] if detail.strip() else ''):
        return False
    code, detail = run_main(verify_docx.main, [md_path, out])
    ok = record(code == 0, '%s：verify_docx 自检' % tag,
                detail.strip().splitlines()[-1] if detail.strip() else '')
    code, detail = run_main(outline_docx.main, [out])
    tail = [l for l in detail.strip().splitlines() if l.strip()]
    ok = record(code == 0, '%s：outline_docx 标题树' % tag,
                tail[-1].strip() if tail else '') and ok
    return ok


def check_pipeline_a():
    """产线 A 回归：听记 txt → md，原文逐行必须全覆盖。"""
    if not os.path.isfile(BASE_TXT):
        return record(False, '产线 A：样本不存在', BASE_TXT)
    os.makedirs(TMP, exist_ok=True)
    out = os.path.join(TMP, 'a.md')
    code, detail = run_main(format_text.main, [BASE_TXT, out])
    if not record(code == 0 and os.path.isfile(out), '产线 A：format_text 重排'):
        return False
    checked, missing = verify_text.check(BASE_TXT, out)
    return record(not missing, '产线 A：verify_text 行覆盖',
                  '原文 %d 行，未匹配 %d 行' % (checked, len(missing)))


def check_scripts():
    missing = [s for s in SCRIPTS if not os.path.isfile(os.path.join(TOOLS, s))]
    return record(not missing, '作业脚本齐全 %d/%d' % (len(SCRIPTS) - len(missing), len(SCRIPTS)),
                  ('缺 ' + ','.join(missing)) if missing else '')


def check_env():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 8)
    record(ok, 'Python %d.%d.%d' % sys.version_info[:3],
           '' if ok else '需要 3.8+')
    record(os.path.isdir(TOOLS) and os.path.isdir(ROOT), '工作区可定位',
           '根目录 %s' % ROOT)


def wps_hint():
    print('\n== 页数验收（不在本脚本内跑，复制到 PowerShell）==')
    print('''  $wps = New-Object -ComObject KWPS.Application
  $doc = $wps.Documents.Open("<docx 绝对路径>", $false, $true)
  $doc.ComputeStatistics(2)   # 页数；再用 $doc.Tables.Count 复核表数
  $doc.Close(0); $wps.Quit()''')


def mode_regression():
    section('环境')
    check_env()
    check_scripts()

    section('产线 B（docx → md 逐字转写）')
    check_docx2md(BASE_DOCX, '261118 用户修改版',
                  BASE_DOCX_CHARS, BASE_DOCX_HEADINGS, BASE_DOCX_TABLES)

    section('渲染与自检（md → docx → verify）')
    md_path = os.path.join(TMP, 'b.md')
    if os.path.isfile(BASE_DOCX):
        os.makedirs(TMP, exist_ok=True)
        md, doc = docx2md.convert(BASE_DOCX, False, False, False)
        with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(md)
        check_render(md_path, '261118')
    else:
        record(False, '渲染：缺少样本')

    section('产线 A（听记 txt → md）')
    check_pipeline_a()

    wps_hint()


def mode_single(path):
    path = os.path.abspath(path)
    section('目标文件')
    if not record(os.path.isfile(path), '文件存在', path):
        return
    print('  %s（%.1f KB）' % (path, os.path.getsize(path) / 1024.0))
    if not record(zipfile.is_zipfile(path), '是 docx 包（zip）',
                  '' if zipfile.is_zipfile(path) else '不是 docx，本工具链不处理该格式'):
        return

    section('产线 B：能否保真转写')
    check_docx2md(path, os.path.basename(path))

    section('渲染与自检：转出的 md 能否变成合格 docx')
    os.makedirs(TMP, exist_ok=True)
    md_path = os.path.join(TMP, 'one.md')
    try:
        md, _doc = docx2md.convert(path, False, False, False)
    except Exception as e:
        record(False, '新文件：无法转出 md', '%s: %s' % (type(e).__name__, e))
        return
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(md)
    check_render(md_path, '新文件')

    section('下一步提示')
    print('  identical 通过 → 该文件可作逐字稿真源；不通过 → 先 docx_dump 看结构，')
    print('  改 docx2md 的层级映射，不要手改 md 去凑校验。')
    wps_hint()


def main():
    single = len(sys.argv) > 1
    print('会议资料整理工具链 · 一键预检')
    try:
        if single:
            mode_single(sys.argv[1])
        else:
            mode_regression()
    finally:
        if os.path.isdir(TMP):
            shutil.rmtree(TMP, ignore_errors=True)

    bad = [r for r in results if not r[0]]
    print('\n' + '=' * 60)
    print('结果：%d 项通过 / %d 项失败' % (len(results) - len(bad), len(bad)))
    if bad:
        for _, label, detail in bad:
            print('  FAIL  %s  %s' % (label, detail))
        print('未就绪：先修上列失败项，再开新任务。')
        sys.exit(1)
    if single:
        print('该文件已通过转写与渲染自检，可以直接作为本任务的逐字稿来源。')
    else:
        print('工具链就绪：可直接按《会议资料整理SOP.md》开工。')
        print('约定：md 是唯一真源；逐字稿 identical；交付稿 verify 0 退出；要页码走 md2docx。')


if __name__ == '__main__':
    main()
