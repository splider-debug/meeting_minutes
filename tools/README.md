# tools/ —— 会议资料整理工具链（Python 3 标准库，零依赖）

> 配套文档：上级目录《会议资料整理SOP.md》（唯一作业手册，需求清单/流程/版式/踩坑/验收都在里面）
> 环境：Windows + Python 3.13（3.8+ 均可）；只用 `zipfile` / `xml.etree` / `re`，**不需要 pip 安装任何包**。
> 最近全绿回归：2026-11-19，`preflight.py` 9/9 通过（8 个作业脚本 + 内置回归项）。

## 0. 开工前自检（新会话第一步）

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
python C:\Users\ju\Desktop\会议资料整理\tools\preflight.py
```

看到 `结果：9 项通过 / 0 项失败` 就说明工具链没坏，直接进 SOP 阶段 1。
手里已有本次新原件时，先单独体检它：

```powershell
python ...\tools\preflight.py "新原件.docx"     # 转写 identical？渲染能过 verify？
```

预检只读原件，中间产物写 `_preflight\` 并自动删除；不带参数跑的是内置回归（下节基线）。

## 1. 九个脚本（8 个作业脚本 + 1 个预检）

| 脚本 | 方向 | 作用 | 出口码 |
|---|---|---|---|
| **`preflight.py`** | — | **一键预检**：不带参数跑内置回归（9 项）；带一个 docx 参数则抽查该文件能否保真转写并渲染 | 0 就绪 / 1 有失败项 |
| `docx_dump.py` | docx → 屏幕 | **摸底**：页面/边距、内容宽、字号×加粗分布、标题候选、表格规格 | 0 |
| `docx2md.py` | docx → md | **逐字转写**：结构还原 + 归一化逐字符一致性校验 | 0 通过 / 1 不一致 |
| `md2docx.py` | md → docx | **渲染交付稿**：双主题、页脚页码、表题、嵌套列表 | 0 |
| `verify_docx.py` | docx → 结论 | **结构自检**：XML 良构、引用完整、子元素顺序、文字覆盖率 | 0 通过 / 1 有问题 |
| `docx_report.py` | docx → 屏幕 | 轻量体检：页脚/分节/样式集中度（快速对比两个版本） | 0 |
| `outline_docx.py` | docx → 屏幕 | 打印标题树（认 outlineLvl，也认"一、/（一）/1."直接格式） | 0 |
| `format_text.py` | txt → md | **产线 A**：AI 听记原文（UTF-16LE）按原结构重排；只加结构，不改字 | 0 |
| `verify_text.py` | txt+md → 结论 | **产线 A 校验**：原文每一行是否都落进 md | 0 全覆盖 / 1 有缺行 |

`preflight.py` 的硬基线（改动工具或样本后才需同步）：261118 用户修改版 = **41 标题 / 4 表 / 7489 字符 identical**；261111 文本.txt = **314 行全覆盖**。

## 2. 标准串联（PowerShell）

```powershell
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
$T = 'C:\Users\ju\Desktop\会议资料整理\tools'

# ⓪ 开工前预检（9 项全绿再往下走）
python "$T\preflight.py"

# ① 摸底：先看清对方文档长什么样，再决定层级映射
python "$T\docx_dump.py" 原件.docx --text 40

# ② 逐字转写 + 自证
python "$T\docx2md.py" 原件.docx 逐字稿.md --report

# ③ md → 交付稿 Word
python "$T\md2docx.py" 交付稿.md 交付稿.docx "黑龙江冰凌监测项目会议资料整理" --theme report

# ④ 结构自检（必须 0 退出）
python "$T\verify_docx.py" 交付稿.md 交付稿.docx

# ⑤ 目视：标题树 + 页数（WPS COM，见 SOP §5 阶段 5）
python "$T\outline_docx.py" 交付稿.docx
```

**闭环校验**（改完 md 再产出时，确认没有引入偏差）：
`docx2md.py 交付稿.docx 回读.md` → 应 `一致性校验: 通过 identical`。

## 3. 主题

| 主题 | 适用 | 标题 | 正文 | 表格 |
|---|---|---|---|---|
| `classic`（默认） | 内部分享、屏幕阅读 | 微软雅黑，蓝 #1F3864 | 宋体 10.5pt / 1.42 行距 | 深蓝表头白字、#C9D3E0 边框 |
| `report` | 正式汇报、公文风、给领导看 | 黑体 22/15/12.5/10.5pt，#1F3864 | 宋体 10.5pt / 1.45 行距 | 浅蓝表头 #E8EEF7 深蓝字、#8EA9C8 边框、隔行 #F5F8FC |

两套都写 `— 页码 —` 页脚（PAGE 域，打开即显示真实页码）。

### 3.1 表格列宽怎么算（改样式时别动坏）

- 列宽由 `alloc_widths()` 分配：先按内容长度算权重，再保证**列宽总和精确等于内容宽**（9026），余量从最宽的列回收，不再全丢给最后一列。
- 每列另有下限 `_col_min()`：取"表头字符数×175twips"与"最长不可断串（时段 `13:24—13:30`、型号 `4-20mA`、`12V/24V`）×130twips"的较大值，最低 810twips。中文可在任意字间断行，所以不按整格字数算。
- 表头行 `tblHeader` 跨页自动重复，行内 `cantSplit` 不被拆开；长表跨页属正常。
- 表头行**不要写 `<w:trPr><w:jc>`**：那是"表行对齐"，会让整行相对表体偏移（表现是"第一排是歪的"）。表头文字居中靠 `TableHead` 样式的 `jc=center`。
- 支持 ``` 代码围栏（进 `Code` 样式，浅灰底等宽，不做 markdown 解析）与行内 `` `代码` ``（等宽字体）；技术类 md 也能直接出版式正常的 docx。

## 4. 已验证案例

**2026-11-19 全量回归（`preflight.py`，9/9 通过）**

| 检查项 | 结果 |
|---|---|
| 环境 | Python 3.13.5；脚本 8/8 在位；工作区可定位 |
| 261118 `..._用户修改.docx` → docx2md | 41 标题 / 4 表 / **7489 字符 identical** |
| 该 md → md2docx(report) → verify_docx | 渲染成功；`verify` **0 退出**；A4 / 208 段 / 4 表 |
| outline_docx | 标题 41 个、层级与设计一致（章号「一、二、四、五」＝原样保留的已知断档） |
| 261111 `文本.txt` → format_text → verify_text | 原文 **314 行全覆盖，未匹配 0 行** |
| WPS COM（`KWPS.Application`） | 该 docx 报 **10 页 / 4 表**，与记录一致 |

**历史回归（2026-11-18）**

| 输入 | 结果 |
|---|---|
| 261118 `..._用户修改.docx`（Word 直接格式，无 pStyle） | 41 标题 / 4 表 / 7489 字符完全一致 |
| 261111 `公司战略转型与目标复盘.docx`（md2docx 产物，含 Quote/页脚） | 47 标题 / 71 列表 / 19 引用 / 11325 字符完全一致 |
| 261118 `...与空天地融合应用方案.docx`（无标题段、outline 偏移） | 11 标题，自动归一到 1 级起 / 1119 字符一致 |
| `b.md` → `md2docx --theme report` → `verify_docx` | 通过；WPS 打开 338 段 / 2 表 / 18 页（与原件一致） |
| `deliverable.docx` → `docx2md` → `md2docx` 往返 | 样式分布与原件一致（Title/Heading1/Heading2/ListParagraph/Quote 计数全同） |

**新案例：261119（2026-11-19 下午，含自动编号的听记 docx）**

| 步骤 | 结果 |
|---|---|
| `纪要_11-19 …docx` → `docx2md` | 40 标题 / 204 列表 / **9949 字符 identical**（修复自动编号误判后） |
| 手写纪要 → `md2docx(report)` | 371 段 / **8 表**；A4；页脚 PAGE 域 |
| `verify_docx` | **0 退出**；8 张表列宽总和均 9026、最窄列 810 |
| WPS 报页数 | **12 页 / 8 表**；导出 PDF 逐页目视通过 |
| 交付稿 → `docx2md` 闭环回读 | identical（7372 字符） |

## 5. 环境注意（本机沙箱已踩过）

- **不要用 `python -c "..."`**，也不要 `python x.py | Select-Object` 或 `python x.py > log.txt`（管道/重定向会被沙箱拒绝，报"拒绝访问"或 `StandardOutputEncoding is only supported when standard output is redirected`）。
  **正确做法：直接 `python x.py ...`，让命令自己打印，需要筛选时再由人看输出**；只有 `preflight.py` 例外地用"进程内调用 + 捕获"，因为它不启子进程。
- 中文输出乱码时先设 `$env:PYTHONIOENCODING='utf-8'`、`$env:PYTHONUTF8='1'`。
- 路径含中文/空格时一律加引号；PowerShell 里处理 `[...]` 路径用 `-LiteralPath`。
- 这些脚本**只读** docx、只写你指定的 md/docx，不会动原件；`preflight.py` 只写临时目录 `_preflight\` 且退出前删除。
