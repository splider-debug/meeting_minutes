# 会议资料整理服务 · 使用说明（M1 → M4）

> 已完成：M1 解析+渲染、M2 容器部署、M3 接 DeepSeek 出真纪要、**M4 Web + SSE 进度流**。
> 待办：无（按需求"单页单任务、无历史列表、共享口令、8081 自签"全部落地）。

## 一、两种用法

**A. 网页（推荐日常用）**

```powershell
cd C:\Users\ju\Desktop\会议资料整理\会议纪要服务
.\venv\Scripts\Activate.ps1
uvicorn app.web:app --host 127.0.0.1 --port 8099
```

浏览器开 `http://127.0.0.1:8099` → 输口令 `huiyi2026`（在 `.env` 里改）→ 选文件 → 开始整理。
页面会实时显示：**阶段条（P0—P9）+ 步骤流 + 下载按钮 + 1 小时倒计时**。

**B. 命令行（排错 / 批量）**

```powershell
python run_m1.py "..\261119_…\纪要_11-19 ….docx"
python run_m1.py 某文件.txt --no-llm      # 只出逐字稿+骨架稿，不花钱
```

跑完会在 `runs/<时间戳>_<文件名>/` 下得到：

```
01_逐字转写.md        ← 逐字稿真源（docx 输入会打印 identical N 字符）
02_会议纪要.md        ← 模型产出的正式纪要（md 真源）
03_<主标题>.docx      ← 交付稿（A4 / 页脚页码 / 表 N / 章号连续）
result.zip            ← 上面三件打包
work/plan.json        ← 章节树（排错用）
work/draft/*.md       ← 每节模型输出（单节失败时可直接看）
```

## 二、参数

| 参数 | 作用 |
|---|---|
| `--title "标题"` | 指定主标题（不传则从文件名清洗） |
| `--out 目录` | 指定输出目录（默认 `runs/<时间戳>_<名>`） |
| `--no-llm` | 不调模型，只出逐字稿 + 骨架稿（省钱、排错用） |
| `--slice-only` | 图片输入时只切片不识别 |
| `GUARD_MODE=warn` | 环境变量：守恒校验不通过时只告警不中止（默认 strict） |
| `ENABLE_LLM=0` | 环境变量：等价于 `--no-llm` |

## 三、模型相关

- key 在 `会议纪要服务/.env` 的 `DEEPSEEK_API_KEY`（**该文件已在 `.gitignore`，不会进仓库**）。
- 自检：`python check_llm.py`（打印连通性、用量、余额类错误）。
- 实测单次成本（261119，2 小时 15 分会议）：**14—20 次调用 / 输入约 1.6 万 tok / 输出约 6 千 tok**，按官网价折算 **几分钱**。
- 耗时：P3 约 9 秒 + 每节 1.5—2 秒 × 13—19 节，总计 **35—45 秒**。

## 四、流水线做了什么

| 阶段 | 内容 | 谁做 |
|---|---|---|
| P0 | 扩展名/大小/魔数校验 | 代码 |
| P1 | docx→md（identical）/ 文本严格解码 / 图片切片+OCR | 代码 |
| P2 | 材料画像（字符/行/标题/表格行） | 代码 |
| P3 | **章节树 + 3 张表设计（JSON）** | 模型 |
| P4 | **逐节写正文**（按块号回取原文） | 模型 |
| P5 | 组装成 SOP 版式 md（章号/表号由代码定） | 代码 |
| P6 | **数字守恒 + 结构连续校验** | 代码 |
| P7 | md → docx（复用 `tools/md2docx.py`） | 代码 |
| P8 | `verify_docx` 结构自检 | 代码 |
| P9 | 打包 | 代码 |

**设计底线**：模型只写正文，不碰版式、不碰编号；正文里出现的每个数字都必须能在原文找到，否则 P6 直接中止交付。

## 五、M3 实测（261119，同一个文件跑了 7 次）

| 指标 | 结果 |
|---|---|
| 逐字稿保真 | `identical（9949 字符）`，每次一致 |
| 结构 | 6—7 章 / 13—19 节 / 3 张表（模型每次略有差异，属正常） |
| 数字守恒 | 42 个原文数字全部对得上，**0 幻觉** |
| 结构连续 | 章号/节号/表号连续，`verify_docx` 0 退出 |
| 耗时 | 35—45 秒 |
| 用量 | 输入 1.5—1.9 万 tok / 输出 6—7 千 tok |

示例输出见 `示例/261119/`。

## 六、过程中修掉的 6 个真问题

1. **P3 输出被 4096 token 截断** → JSON 不完整 → 压缩输入（块节选 150 字）+ 强制紧凑输出 + 上限提到 6000。
2. **内部块号泄漏到表格**（`依据块` 列）→ 翻译成原文时间区间（"出处"），无时间的整列删除。
3. **内部块号泄漏到正文**（`（[74] 已确认口径）`）→ 提示词禁止 + 生成后正则清理。
4. **模型写出不成对的 `**`** → 渲染后留字面星号、verify 报"残留标记+正文缺失" → 按段落配平。
5. **元信息混进正文**（"内容由钉钉AI听记生成"、"议题数 5"）→ P3 前剥离元信息块（本次剥掉 12 个）。
6. **AI 洞察被当会议结论** → 单列为「附录：听记工具提炼要点（非参会人结论）」（本次剥出 15 条）。

## 七、接 Web 接口（M4）

| 接口 | 说明 |
|---|---|
| `GET /` | 单页（阶段条 / 步骤流 / 下载） |
| `POST /auth/login` | 共享口令登录，30 天 cookie |
| `POST /api/tasks` | 上传即返回 `task_id`，任务后台跑（不再挂长请求） |
| `GET /api/tasks/{id}/events` | **SSE**：`stage` / `step` / `metric` / `done` / `failed`，支持 `Last-Event-ID` 续传 |
| `GET /api/tasks/{id}` | 状态快照（断线兜底） |
| `GET /files/{id}/{name}` | 单文件下载（1 小时 TTL） |
| `GET /healthz` `/readyz` | 存活 / 就绪探针 |

M4 实测（本地）：页面 200、SSE 收到 20 条阶段 + 29 条步骤、下载 3 个文件 +
`result.zip`、任务快照 `done`、同 IP 第二个任务 `429 RATE_LIMITED`、真实模型任务 **35.9 秒**跑完。

## 八、排错：图片任务报 OCR 初始化失败

Windows 上最常见的一种：

```
失败 [PARSE_ERROR] OCR 引擎初始化失败：PermissionError: [WinError 5] 拒绝访问。: 'C:\Users\ju\.paddlex'
```

**原因**：paddle / paddlex 默认往用户目录写模型与临时文件（`C:\Users\<user>\.paddlex`、
`~\.cache\paddle`、`~\AppData\Local\Temp`），而有些机器上这些路径的创建被安全策略拒绝。

**已修**（`app/config.py`）：服务启动时把"家目录"和全部缓存目录钉到项目内——

| 用途 | 位置 |
|---|---|
| 假家目录（`~` 解析到这里） | `data/home` |
| OCR 模型 | `data/models`（容器里是 `/data/models`） |
| paddle 扩展缓存 | `data/paddle_ext` |
| modelscope / HF 缓存 | `data/modelscope`、`data/hf` |

这样即使 `C:\Users\ju\.paddlex` 建不了，OCR 也能用；容器重启后模型也不丢。

**验证**（在你自己终端，不要在我的沙箱里跑）：

```powershell
python check_env.py --ocr-init     # 应打印：OK PaddleOCR 初始化 3.x 参数可用
```

### 手动下载模型（网络受限或用不了自带下载器时）

模型地址（官方 bos，直链，HEAD 已验证 200）：

| 模型 | 大小 | 地址 |
|---|---|---|
| `PP-OCRv6_medium_det`（检测） | 59.4 MB | `https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_medium_det_infer.tar` |
| `PP-OCRv6_medium_rec`（识别） | 73.3 MB | `https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv6_medium_rec_infer.tar` |

**推荐用脚本下，别手动解压**（脚本用标准库，绕开 paddle 自带下载器的临时目录问题）：

```powershell
python fetch_models.py            # 下 det + rec 到 data/models/official_models/<模型名>/
python fetch_models.py --list     # 只打印地址与目标目录
python fetch_models.py --only rec # 只下识别模型
```

手动放置的目录结构（解压后 `inference.json` 与 `inference.pdiparams` 必须在这一层）：

```
data/models/official_models/PP-OCRv6_medium_det/inference.json
data/models/official_models/PP-OCRv6_medium_det/inference.pdiparams
data/models/official_models/PP-OCRv6_medium_rec/inference.json
data/models/official_models/PP-OCRv6_medium_rec/inference.pdiparams
```

**如果报的是** `RuntimeError: json.exception.parse_error ... empty input`：

**这是 `paddle 3.3.1 + paddlex 3.7.2` 的版本不兼容**（在 Python 3.13 上必然出现，
和缓存、模型都无关——模型齐全也一样报）。paddle 2.6 没有 cp313 轮子，paddle 3.x 最高只有
3.3.1，而 paddlex 3.7 需要更新的推理接口，所以这条路在 3.13 上走不通。

**解决办法：换 OCR 引擎，不再用 paddle**（一次安装，约 50MB，模型内置）：

```powershell
pip install --ignore-requires-python "rapidocr-onnxruntime>=1.4,<1.5"
python check_env.py --ocr-init --ocr-run
```

期望看到：

```
OK   rapidocr(旧包名)      1.4.x
OK   版本组合              RapidOCR（ONNX），不依赖 paddle，无版本冲突风险
OK   引擎就绪              RapidOCR（ONNX）
OK   OCR 初始化            成功（RapidOCR）
OK   样例识别              12 行
```

适配层 `app/parse/ocr_engine.py` 会**优先用 RapidOCR**，没有才回退 paddle，
所以装了它之后 paddle 可以留着不管（也可以 `pip uninstall paddlepaddle paddleocr paddlex` 瘦身）。

`--ignore-requires-python` 是必须的：该包元数据写了 `<3.13`，但实测在 3.13 上可用。

## 九、下一步（可选）

- 结构锁定：把满意的 `plan.json` 存成模板，后续同类型会议复用，避免章节划分每次都变；
- 断点续跑：P4 单节失败时只重跑该节（代码里已有 key 结构，接 UI 即可）；
- 图片链路联调：需在能出网的机器上确认一次 OCR（本机沙箱跑不了）。

