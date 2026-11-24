# -*- coding: utf-8 -*-
"""环境自检：python 版本、依赖导入、版本组合、tools 脚本可用性。

用法（在服务目录下）：
    venv\\Scripts\\python.exe check_env.py
    venv\\Scripts\\python.exe check_env.py --ocr-init    # 额外做一次 PaddleOCR 初始化（会下载/加载模型，慢）
"""
import importlib
import os
import sys

# 关键：必须先加载 app.config。
# 它会把 HOME/USERPROFILE 与 paddle/paddlex/modelscope 的缓存目录钉到项目内，
# 否则 paddle 会去写 C:\Users\<user>\.paddlex —— 某些机器上这个路径创建被拒（WinError 5）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from app import config as _app_config      # noqa: F401  仅为触发副作用
    _cfginfo = {"home": os.environ.get("HOME"),
                "models": _app_config.MODEL_DIR,
                "cache": os.environ.get("PADDLE_PDX_CACHE_HOME")}
except Exception as _e:                        # 服务代码缺失时仍能自检依赖
    _cfginfo = {"error": "%s: %s" % (type(_e).__name__, _e)}

# 在受限环境（如沙箱）里，pip/paddle 往系统临时目录写会被拒；--local-cache 改到项目内
if '--local-cache' in sys.argv:
    _cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_cache')
    os.makedirs(_cache, exist_ok=True)
    os.environ['USERPROFILE'] = _cache
    os.environ['HOME'] = _cache
    os.environ['XDG_CACHE_HOME'] = os.path.join(_cache, '.cache')
    os.environ['PADDLE_PDX_CACHE_HOME'] = os.path.join(_cache, 'paddlex')
    os.environ['PADDLEOCR_HOME'] = os.path.join(_cache, 'paddleocr')

OK, BAD, WARN = 'OK  ', 'FAIL', 'WARN'

results = []


def note(level, name, detail=''):
    results.append((level, name, detail))
    print('%s %-22s %s' % (level, name, detail))


def try_import(mod_name, label=None):
    label = label or mod_name
    try:
        m = importlib.import_module(mod_name)
    except Exception as e:
        note(BAD, label, '%s: %s' % (type(e).__name__, str(e)[:160]))
        return None
    ver = getattr(m, '__version__', '') or '?'
    note(OK, label, ver)
    return m


print('=' * 78)
print('Python %s' % sys.version.replace('\n', ' '))
print('executable: %s' % sys.executable)
print('缓存布局: %s' % _cfginfo)
print('=' * 78)

note(OK, 'python', '%d.%d.%d' % sys.version_info[:3])
if sys.version_info[:2] >= (3, 13):
    note(WARN, 'python 版本', '3.13：paddle 只能用 3.x 线（2.6 无 cp313 轮子），属预期')

print('\n--- 核心依赖 ---')
for mod, label in (('fastapi', 'fastapi'), ('starlette', 'starlette'),
                   ('uvicorn', 'uvicorn'), ('jinja2', 'jinja2'),
                   ('multipart', 'python-multipart'), ('httpx', 'httpx'),
                   ('argon2', 'argon2-cffi'), ('PIL', 'pillow')):
    try_import(mod, label)

print('\n--- OCR 依赖 ---')
rapid = None
for mod, label in (('rapidocr_onnxruntime', 'rapidocr(旧包名)'),
                   ('rapidocr', 'rapidocr(新包名)')):
    try:
        m = importlib.import_module(mod)
        rapid = mod
        note(OK, label, getattr(m, '__version__', '?') or '?')
        break
    except Exception as e:
        note(WARN, label, '未安装（%s）' % type(e).__name__)
if rapid is None:
    print('     提示：Python 3.13 上推荐用 RapidOCR ——  '
          'pip install --ignore-requires-python "rapidocr-onnxruntime>=1.4,<1.5"')

paddle = paddleocr = None
try:
    paddle = importlib.import_module('paddle')
    note(OK, 'paddlepaddle', paddle.__version__)
except Exception as e:
    note(WARN, 'paddlepaddle', '不可用（%s）' % str(e)[:80])
try:
    paddleocr = importlib.import_module('paddleocr')
    note(OK, 'paddleocr', getattr(paddleocr, '__version__', '?'))
except Exception as e:
    note(WARN, 'paddleocr', '不可用（%s）' % str(e)[:80])

if rapid is None and paddleocr is None:
    note(BAD, 'OCR 引擎', '一个都没装：图片任务将返回 OCR_UNAVAILABLE')
elif rapid:
    note(OK, '版本组合', 'RapidOCR（ONNX），不依赖 paddle，无版本冲突风险')
elif paddle is not None:
    major = str(paddle.__version__).split('.')[0]
    note(OK, '版本组合', 'paddle %s.x 线 → 走适配层的 paddle%s 分支'
         % (major, major))

print('\n--- tools/ 脚本可用性（服务要复用它们） ---')
here = os.path.dirname(os.path.abspath(__file__))
tools = os.path.join(os.path.dirname(here), 'tools')
if not os.path.isdir(tools):
    tools = os.path.join(here, 'app', 'tools')
print('    tools 目录：%s' % tools)
if os.path.isdir(tools):
    sys.path.insert(0, tools)
    for mod in ('md2docx', 'verify_docx', 'docx2md', 'outline_docx'):
        try_import(mod, 'tools/' + mod)
else:
    note(BAD, 'tools 目录', '找不到，无法复用渲染层')

if '--ocr-init' in sys.argv:
    print('\n--- OCR 初始化实测（走的是服务真实用的适配层）---')
    try:
        from app.parse.ocr_engine import kind_label, make_ocr
        engine, kind = make_ocr()
        note(OK, '引擎就绪', kind_label(kind))
        note(OK, 'OCR 初始化', '成功（%s）' % type(engine).__name__)
        if '--ocr-run' in sys.argv:
            img = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'one_slice.png')
            if os.path.isfile(img):
                from app.parse.ocr_engine import ocr_lines
                lines = ocr_lines(engine, kind, img)
                note(OK, '样例识别', '%d 行' % len(lines))
                for t, s, _y, _x, _h in lines[:3]:
                    print('        %.3f  %s' % (s, t[:40]))
            else:
                note(WARN, '样例识别', '缺少 data/one_slice.png，跳过')
    except ImportError as e:
        note(BAD, 'OCR 初始化', '未安装可用引擎：%s' % e)
    except Exception as e:
        note(BAD, 'OCR 初始化', '%s: %s' % (type(e).__name__, str(e)[:200]))

bad = [r for r in results if r[0] == BAD]
warn = [r for r in results if r[0] == WARN]
print('\n' + '=' * 78)
print('结果：%d 项通过 / %d 警告 / %d 失败' % (len(results) - len(bad) - len(warn), len(warn), len(bad)))
for _, name, detail in bad:
    print('  FAIL  %s  %s' % (name, detail))
if not bad:
    print('核心链路可用。')
sys.exit(1 if bad else 0)
