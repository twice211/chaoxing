# 学习通课程学习 + 刷题 + 错题整理 + 开卷期末 AI 辅助工具

> 一个基于 **Python 3.11 + Playwright + SQLite** 的个人学习助手：
> 自动完成课程学习（视频/文档/PPT）、识别并整理练习题、建立课程知识库，
> 并在**课程明确允许开卷 + 允许 AI 辅助**的期末考试中提供“只读侧边栏”AI 辅助。
>
📖 **面向日常使用的操作手册**：`docs/使用说明.md`、`docs/使用说明.pdf`（reportlab 版，由已安装的 `pdf` 技能规范生成：`python tools/make_pdf_skill.py`）（Word 打印版可直接打开）；重新生成：`python tools/make_docs.py`。
> 本 README 侧重架构、开发与排障；日常操作请按《使用说明》的顺序执行。
> ⚠️ **使用前必读**：本工具只做“读取 + 分析 + 提示”。它不会、也不能：
> 自动登录、识别/绕过验证码、修改服务器数据、伪造学习或考试记录、修改考试倒计时、
> 绕过监考/切屏检测、自动勾选答案或点击“提交”。**所有答案由你本人判断并亲自提交。**
> 请严格遵守学校、教师与学习通当次考试的具体规定；考试规定一旦禁止 AI，本工具会自动关闭考试模式。

---

## 一、整体架构

### 1. 设计目标与约束

| 需求 | 实现位置 | 关键做法 |
|---|---|---|
| 登录/验证码由本人完成 | `browser/driver.py` | 持久化用户目录 + 轮询登录态，**只等待不代填**；`HEADLESS` 被强制为 `False` |
| 自动读取课程与目录 | `course/crawler.py` | URL 特征 → 文本特征 → 候选选择器，三级识别，改版不易失效 |
| 识别视频/文档/PPT/测验/练习 | `course/crawler.py::classify_kind` | `ananas/video`→视频，`/doc/`→文档，`/ppt/`→课件，`/exam/`→测验，`/work/`+“练习”→练习题 |
| 正常播放并自动进入下一项 | `video/player.py`、`course/study.py` | 强制 `playbackRate<=1.0`；播放结束后回到目录取下一条未完成项 |
| 真实进度 + 断点续学 | `database/store.py` | `items(progress/watch_sec/done)` + `study_log` + `resume` 游标 |
| 异常自动重试、不死循环 | `utils/retry.py` | 指数退避重试 + `LoopGuard`（步数上限 + 无进展检测） |
| 五题型题目提取 | `browser/js.py` + `questions/extractor.py` | 浏览器内 JS 解析 DOM（含已勾选项），失败自动退回 BeautifulSoup |
| AI 分析/解析/知识点 | `ai/responder.py` + `ai/prompts.py` | 结构化输出 → 逐节解析 → **引用编号校验** |
| 题库/错题库/重复题/强化题 | `database/schema.sql`、`questions/wrongbook.py` | `questions.fp` 唯一约束去重 + `exists_similar` 近似去重 + AI 生成同类题 |
| 课程知识库与搜索 | `knowledge_base/*` | 资料→分块→公式/定义→关键词；纯 Python BM25（中文二元组/jieba 可选） |
| 开卷考试 AI 辅助（只读侧边栏） | `exam/*`、`ui/sidebar.py` | 三重合规闸门 + 只读快照 + 独立小窗；**考试页面写操作被代码级拒绝** |
| 练习/演示自动作答（opt-in，默认关闭） | `exam/guard.py::allow_page_write`、`questions/autofill.py`、`ui/panel.py` | 开启 `PRACTICE_MODE` 后仅在**非真实考试域名**自动作答/提交；`REAL_EXAM_DOMAINS` 硬锁真实考试页 |
| 成绩查看（只读） | `course/grades.py`、`course/crawler.py::fetch_grades`、`database` `grades` 表 | `python main.py grades` 读取课程总评与逐项得分明细（考核权重/讨论规则/如何加分），整批缓存本地 `grades` 表；纯只读，不写服务器 |
| 阅读/文档任务点（可设时长） | `course/reading.py`、`video/player.py`、`ui/workbench.py` | `python main.py read` 或工作台「阅读本节(计时)」：默认按正文字数估时、可设“最低停留秒数”覆盖；真实停留后**平台标注才算完成**(不伪造)；协作式可“停止” |
| 计分讨论·发表/回复双模块（默认关） | `course/discussion.py`、`exam/snapshot.py`（受闸）、`database` `discussions` 表 | 解析成绩里的讨论计分规则（如发帖+1/回复+2/满分100），两类任务**各成一个模块**（发表=围绕课程提问；回复=约150字回帖），共用同一份凑分规划、够目标分即停。工作台「讨论」页两模块各有 生成草稿/自动完成/填入下一条；CLI `discuss --kind post|reply|all`，`--fill` 只填入（你点发表），`--submit` 程序自动点发表/回复。需 `PRACTICE_MODE` + `PRACTICE_ALLOW_DISCUSS_POST`（自动提交再加 `PRACTICE_ALLOW_DISCUSS_SUBMIT`，均默认 False）+ 非考试页；内容指纹去重、每轮限量（`DISCUSS_MAX`） |

讨论发布流程：

- 半自动：点「生成草稿」→「填入下一条」→ 在浏览器核对并亲自发表/回复 → 点该模块「确认已发布」，才计入完成数并进入下一条。「填入下一条」始终只填入；放弃未提交草稿时可点「放弃草稿」，然后在浏览器清空或关闭编辑框。CLI `discuss --fill` 发布成功输入 `y`；`s` 放弃，`q` 保留草稿并退出，`--pick 0` 顺序处理全部草稿。
- 自动：开启既有讨论权限开关后点「自动完成」（CLI `discuss --submit`）。先核对本轮课程、条数、每条目标话题和完整内容，明确确认后逐条提交；程序只有捕获新出现的成功提示或编辑器外的正文回显才记为 `posted`，旧提示和草稿自身不会计入。连续回复时会返回讨论列表并核对下一条详情页地址；找不到目标即停，不会在旧话题上继续填写。
- 话题列表上的「已回复」和本地内容指纹都会参与去重。已确认发布的讨论按实际确认时间记账；即使平台成绩刷新后仍未更新，也不会把这些本地确认记录遗忘而重新规划。分数仍是估算，成绩规则或平台计分异常时请以页面实际显示为准。
- 失败恢复：打不开表单、填入失败或发布结果无法核验时停止本轮。已点击但未核验的记录为 `pending_verify`，不计完成数，也不自动重试；请在页面核验并用对应模块「确认已发布」处理。核对确实未发布时点「确认未发布」解除待核验状态，再手动重试。重启后唯一的待核验记录仍可确认。取消任务保留当前进度。
- 回归检查：`python -m unittest tests.test_discussion_publish -v` 使用本地模拟 HTML 验证发布、失败、取消与半自动确认，不访问学习通。

### 2. 数据流

```
                ┌──────────── 学习通网页（Playwright 持久化会话，登录由你完成）
                │
   ┌────────────┴─────────────┐
   │  course/crawler          │  课程 · 章节目录 · 任务类型 · 平台完成标记
   └────────────┬─────────────┘
                │ items/chapters
   ┌────────────▼─────────────┐        ┌────────────────────┐
   │  course/study（主循环）   │──失败──▶│ utils/retry（退避+护栏）│
   │  video/player（1.0 倍速） │        └────────────────────┘
   └────────────┬─────────────┘
                │ 页面正文 / 文档下载
   ┌────────────▼─────────────┐        ┌────────────────────┐
   │ knowledge_base/builder   │──解析──▶│  pdf/docx/pptx/txt │
   │ knowledge_base/retriever │  BM25  │  公式·定义·关键词   │
   └────────────┬─────────────┘        └────────────────────┘
                │ 证据片段（可引用编号 [资料1]/[题目1]/[错题1]）
   ┌────────────▼─────────────┐
   │ questions/*  ·  exam/*    │──▶ ai/client（OpenAI 兼容，自动重试）
   │  题目提取 → AI 分析 → 校验 │        │
   │  错题 → 强化题            │◀───────┘ ai/responder：引用不存在的资料 → 强制降级“需要人工确认”
   └────────────┬─────────────┘
                │
   ┌────────────▼─────────────┐
   │ database/store (SQLite)  │  courses·chapters·items·study_log·questions
   │ + logs/ + data/dumps     │  ·wrong_book·kb_docs·kb_chunks·ai_calls·exam_audit
   └──────────────────────────┘
```

### 3. 目录结构

```
study_helper/
├── main.py                  # 命令行入口（login/courses/sync/study/practice/kb/search/wrong/exam/grades/read/discuss/status/dump/selftest/btest）
├── config.py                # 配置加载 + 不可关闭的安全红线 SAFETY
├── config.example.py        # 全部配置项模板（含中文注释）
├── user_config.py           # 你的个人配置（已自动生成，填 AI 密钥即可）
├── requirements.txt
├── browser/
│   ├── driver.py            # Playwright 持久化会话、等待本人登录、导出页面 HTML
│   ├── actions.py           # 多候选选择器等待、重试跳转、节流、跨 iframe 查找
│   └── js.py                # 页面内只读 JS 探针（链接采集/视频状态/题目提取/正文抓取）
├── course/
│   ├── selectors.py         # ★ 页面元素选择器集中管理（可被 user_config 覆盖）
│   ├── models.py            # Course / Chapter / StudyItem / Catalog
│   ├── crawler.py           # 课程列表、章节目录、任务类型与完成状态识别
│   └── study.py             # 自动学习主循环（断点续学、重试、进度落库）
├── video/player.py          # 1.0 倍速播放监控、停滞检测、文档真实停留
├── questions/
│   ├── models.py            # Question 数据结构、题型/答案规范化、指纹
│   ├── extractor.py         # JS 提取 + BeautifulSoup 兜底、判分结果只读导入
│   ├── practice.py          # 刷题辅助流程（AI 分析 + 人工作答 + 错题整理）
│   └── wrongbook.py         # 错题库：记录/统计/导出/生成强化题/错题重练
├── knowledge_base/
│   ├── extractors.py        # PDF/Word/PPT/MD 解析、切块、公式与定义识别、关键词
│   ├── builder.py           # 资料下载与入库、页面正文入库、本地目录入库
│   └── retriever.py         # BM25 检索 + 自然语言搜索解析（章节限定/公式用法）
├── ai/
│   ├── client.py            # OpenAI 兼容接口，重试/超时/调用台账
│   ├── prompts.py           # 全部提示词（练习/考试/强化题/知识点/判题）
│   └── responder.py         # 结构化回答 + 资料依据校验 + 可信度降级
├── exam/
│   ├── guard.py             # 考试 AI 模式三重闸门 + 规则扫描 + 禁止项自动关闭 + 审计
│   ├── snapshot.py          # 考试页面只读快照（题号/题目/选项/题型/倒计时/当前题定位）
│   ├── worker.py            # 浏览器独立线程（让侧边栏 GUI 与 Playwright 并存）
│   └── assistant.py         # 考试辅助编排 + 终端界面 + 提交前确认清单
├── database/
│   ├── schema.sql           # 全部表结构
│   └── store.py             # SQLite 封装（线程安全写、断点续学、题库/错题/知识库）
├── ui/
│   ├── report.py            # 题目/AI 回答/依据/相似题 卡片渲染（终端与 GUI 共用）
│   └── sidebar.py           # tkinter 考试侧边栏（置顶、只读、不改动学习通页面）
├── utils/                   # 日志 / 重试与限速与防死循环 / 文本与指纹 / 时间 / 控制台
└── tests/
    ├── selftest.py          # 29 项离线逻辑自检（不联网、不开浏览器）
    └── browser_selftest.py  # 12 项页面结构自检（本地 HTML 样例 + 真实 Chromium）
```

---

### 文档生成工具（两个版本可选）

| 命令 | 产物 | 特点 |
|---|---|---|
| `python tools/make_pdf_skill.py` | `docs/使用说明.pdf` | 按 **pdf 技能**规范用 reportlab 生成：嵌入微软雅黑、页眉页脚页码、表格配色；`--preview N` 自动渲染 PNG 做视觉校验 |
| `python tools/make_docs.py` | `docs/使用说明_浏览器打印版.pdf` | 用 Playwright 无头 Chromium 打印：CSS 排版，改样式更直观 |

```powershell
python tools\make_pdf_skill.py --md docs\使用说明.md --out docs\使用说明.pdf --preview 2
python tools\make_docs.py --md docs\使用说明.md --out docs\使用说明_浏览器打印版.pdf
```

---

## 二、安装

### 1. 环境要求
- Windows 10/11（也适用 macOS/Linux）、**Python 3.11+**
- 能上网并访问学习通网页版
- 一个 OpenAI 兼容的大模型 API Key（不配置也能用：只做资料整理/检索/题库，不生成 AI 答案）

### 2. 安装步骤（PowerShell）

```powershell
# ① 检查 Python 版本（要求 >= 3.11）
python --version

# ② 安装依赖
python -m pip install -r requirements.txt

# ③ 下载 Playwright 浏览器内核（只需一次，约 130MB）
python -m playwright install chromium

# ④ 自检：确认代码与数据库正常（不开浏览器、不联网）
python main.py selftest        # 期望：结果：29/29 通过

# ⑤ 页面结构自检（本地 HTML 样例 + 真实 Chromium，不访问学习通）
python main.py btest           # 期望：结果：12/12 通过
```

生成 PDF 文档所需依赖（已装好；换机器时执行）：
```powershell
python -m pip install -r requirements-docs.txt
```

可选（更准确的中文分词，检索效果更好）：
```powershell
python -m pip install -r requirements-optional.txt
```

### 3. 配置 AI

编辑 `user_config.py`（已自动生成）：

```python
AI_API_KEY = "sk-xxxxxxxx"                       # 你的密钥
AI_BASE_URL = "https://api.deepseek.com/v1"      # 服务商接口地址
AI_MODEL = "deepseek-chat"                       # 模型名
```

或用环境变量（推荐，避免密钥写进文件）：
```powershell
$env:AI_API_KEY = "sk-xxxxxxxx"
$env:AI_BASE_URL = "https://api.deepseek.com/v1"
$env:AI_MODEL = "deepseek-chat"
```

常见服务商配置示例：

| 服务商 | AI_BASE_URL | AI_MODEL 示例 |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 月之暗面 Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-32k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| 阿里通义（兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:14b`（密钥随便填） |

> 未配置密钥时程序不会报错退出，而是自动降级：照常提取题目、检索本地资料，只在 AI 栏位写明“AI 未启用”，**不会编造答案**。

---

## 三、快速开始（第一次使用，按顺序执行）

```powershell
python main.py login                 # ① 打开浏览器：你本人完成登录 + 验证码 + 身份验证（保存在 data/browser_profile）
python main.py courses               # ② 同步“我的课程”列表
python main.py sync --course 电路     # ③ 同步该课程的章节目录与学习项（识别视频/文档/PPT/测验/练习）
python main.py status                # ④ 查看进度统计
python main.py study --course 电路    # ⑤ 自动学习未完成内容（视频 1.0 倍速真实播放；文档/PPT 真实停留）
python main.py kb build --course 电路 # ⑥ 把课件/文档抓进本地知识库（可反复执行，增量）
python main.py search 戴维南定理 --ai # ⑦ 统一搜索：资料 + 章节 + 历史题 + 错题 +（可选）AI 综合
python main.py practice --course 电路 # ⑧ 刷题辅助：AI 给答案/解析/知识点，你在页面上自己作答并提交
python main.py wrong list            # ⑨ 错题库；wrong drill --limit 10 重练，wrong export --fmt md 导出
```

日常循环建议：`study`（挂机看视频，保持窗口前台）→ 结束后 `kb build` → 遇到练习题 `practice` → 判分后 `wrong drill`。

---

## 四、各模块详解

### 1. 课程学习模块（`study`）

```powershell
python main.py study --course 电路 --limit 20 --kinds video,document,ppt
python main.py study --course 电路 --practice      # 遇到测验/练习顺带进入刷题辅助
python main.py study --course 电路 --no-kb         # 学习时不构建知识库
python main.py study --course 电路 --all           # 忽略完成标记，重做全部条目
```

行为说明：
- **自动进入未完成内容**：只处理 `items.done = 0`；按 `id` 顺序，并从上次位置继续（`resume` 游标）。
- **视频**：定位 `<video>`（支持 iframe 内）→ 强制 `playbackRate = 1.0` → 每 10 秒轮询真实进度并写库 → 播放结束（`ended` 或进度 ≥98.5%）后进入下一项。
- **停滞处理**：连续 `VIDEO_STALL_TIMEOUT_SEC` 无进展时，最多尝试 3 次恢复播放；仍失败则记录“待人工”。若因你切走窗口导致暂停，程序只提示“请把窗口切回前台”，**不伪造前台状态、不绕过挂机检测**。
- **文档/PPT**：真实停留阅读（按正文字数决定时长），随后**以平台完成标记为准**；标记读不到时要求你人工确认，程序绝不自行判定完成。
- **重试**：页面打不开按 1.5s/3s/6s… 指数退避重试（`MAX_RETRY_PER_ITEM`），超过上限则记录失败并继续下一项，不会卡死。
- **防死循环**：`LoopGuard(max_steps, stall_limit)` 双重保护；`Ctrl+C` 随时中断，进度已落库。

### 2. 刷题辅助模块（`practice`）

```powershell
python main.py practice --course 电路 --url "把浏览器里练习页的地址粘贴到这里"
python main.py practice --course 电路 --limit 20
```

- 自动识别 **单选题 / 多选题 / 判断题 / 填空题 / 简答题**（依据：输入控件类型 + 选项数量 + 题干“（单选题）”等文字 + 容器 class）。
- 每题输出（`ui/report.py` 统一格式）：
  ```
  【答案】 B
  【解析】 叠加定理只适用于线性电路……
  【知识点】叠加定理；线性电路
  【资料依据】[资料1]《第2章讲义》第5页（formula）：……
  【可信程度】高 / 中 / 需要人工确认
  ```
- **重复题检测**：`questions.fp` 唯一约束（题号/标点/选项顺序无关）+ `exists_similar` 近似匹配（阈值 0.93）。
- **错题库自动整理**：练习后你自行提交，回到“查看成绩”页再次运行 `practice` 即可只读导入判分结果（对/错标记 + 参考答案），错题自动进入 `wrong_book`。
- **练习/演示自动作答（opt-in，默认关闭）**：把 AI 答案自动填入页面、（可选）自动提交，用于演示/自测。默认 `PRACTICE_MODE=False`、`PRACTICE_ALLOW_SUBMIT=False`，开机纯只读；开启后仅在**非真实考试域名**生效。命令行：
  ```powershell
  python main.py practice --course 电路 --auto-answer                 # 自动作答，不提交
  python main.py practice --course 电路 --auto-answer --auto-submit   # 作答后自动提交
  ```
  小窗：「练习」分页勾选「练习/演示模式（自动作答）」「允许自动提交」，再点「自动作答本页并提交」；看视频时的随堂弹题在同模式下自动作答/提交。
  判定见 `exam/guard.py::allow_page_write`（三级：`PRACTICE_MODE` 开 → 目标页 URL 不命中 `REAL_EXAM_DOMAINS` →（自动提交还需）`PRACTICE_ALLOW_SUBMIT` 开）；任一不满足即拒绝，某题定位不到作答控件则跳过、**绝不误点**。
- **强化练习**：`wrong similar --id <题目id> --n 2` 让 AI 出同知识点、同题型的新题；`wrong drill` 循环重练（答对 2 次自动标记“已掌握”）。

### 3. 错题库

```powershell
python main.py wrong list --all
python main.py wrong stats                     # 按知识点统计薄弱项
python main.py wrong drill --limit 10
python main.py wrong export --fmt md           # md/json/csv 三选一到 data/exports/
python main.py wrong similar --id 12 --n 2
```

### 4. 课程知识库与搜索

```powershell
python main.py kb build --course 电路 --limit 50      # 下载文档/PPT + 抓页面正文入库
python main.py kb local --dir D:\我的笔记 --course 电路  # 把本地资料也纳入知识库
python main.py kb docs                                # 查看已入库资料
python main.py kb search "第二章 等效电路" --top 8
python main.py search 搜索：戴维南定理
python main.py search 搜索：第二章电路分析
python main.py search 搜索：这个公式怎么用 --ai
```

- 资料 → 章节 → 分块（带“第3页 / 第12张幻灯片”定位）→ 关键词 → **公式/定义/例题**分类 → 与题目知识点关联。
- 搜索意图解析：识别 `第X章/单元` 做章节限定；`怎么用/步骤/应用` → 优先公式与定义；`是什么/定义/区别` → 优先定义类内容。
- 检索为纯 Python BM25（中文 jieba 可选，未装则字符+二元组），完全离线可用。

### 5. 开卷期末考试 AI 辅助（`exam`）

**三重开关，缺一不可：**
1. `user_config.py` 中 `EXAM_MODE_ALLOWED = True`（你确认本场考试允许开卷且允许 AI）；
2. 建议填写 `EXAM_RULE_TEXT = "本课程期末考试为开卷考试，允许使用 AI 辅助"`（程序会扫描规则文本，出现“闭卷/禁止 AI”等表述会**自动关闭**考试模式）；
3. 运行时**逐字输入确认口令**（默认 `我已阅读并遵守考试规定`）并填写课程名。

> 如果考试页面和 `EXAM_RULE_TEXT` 里都没有读到明确许可，程序不会“猜你被允许”，而是会让你
> **粘贴教师/课程通知中允许开卷并使用 AI 的原文**；该文本必须命中“开卷/允许使用 AI”等表述才放行，
> 并作为 `manual_grant` 事件写入 `exam_audit` 表以便追溯。提供任何依据都不能绕过“禁止 AI/闭卷”的检测。

```powershell
python main.py exam --course 电路              # 默认弹出 tkinter 侧边栏（置顶、只读）
python main.py exam --course 电路 --console    # 纯终端版
python main.py exam --course 电路 --gui        # 强制图形界面
```

侧边栏/终端界面区块（与需求一致）：
```
【当前题目】 题号 3 ｜ 判断题 ｜ 题干… ｜ 选项…（含图片时会明确提示“AI 未看到图片”）
【AI 分析】 逐条解析
【答案】    A / B / C / D 或具体答案
【课程依据】 [资料1]《第2章讲义》第5页：……（本地检索；无依据时原样输出“课程资料中没有找到直接依据”）
【相关知识点】 戴维南定理；等效电阻
【相似题】   历史题/错题（含错误次数）
【下一题】   仅滚动页面一屏（不点击、不填写）
【提交前确认】T 生成清单：未答题卡号 / AI 判“需人工确认”题号 / 含图片题号 / 剩余时间（只读）
```

操作键：`A` 读当前题、`O` 整卷概览、`D` AI 分析本题、`G 7` 按题号分析、`N/P` 上下滚动、`S 关键词` 快速搜索、`K` 刷新状态、`T` 提交前清单、`Q` 退出。

主观题输出结构（提示词硬性要求，可直接誊抄到答题纸）：
```
【答案】
① 核心结论：……
② 解题过程/理论依据：……
③ 必要公式：Uoc = 端口开路电压，Req = Uoc / Isc
④ 最终答案：R0 = 4Ω
【知识点】【资料依据】【可信程度】
```
计算题必须展示关键计算步骤与单位，不允许只给结果。

**防编造是代码级保证（`ai/responder.py`）**：
- 只允许引用本次提示中真实存在的编号 `[资料i]/[题目i]/[错题i]`；
- 一旦 AI 引用了不存在的来源（例如“教材 P128”“[资料7]”），程序判定为**无效引用**，把【资料依据】改写为“课程资料中没有找到直接依据”，并把【可信程度】强制降为**需要人工确认**；
- 缺少可信度、答案为空、题干含图片而 AI 看不到时，一律提示人工检查。

---

## 五、数据库速查（`data/study_helper.db`）

| 表 | 作用 |
|---|---|
| `courses` / `chapters` / `items` | 课程、章节树、学习项（含 `done/progress/watch_sec/attempts/last_error`） |
| `study_log` | 每一步真实学习动作流水（start/progress/finish/retry/fail/skip/manual_login） |
| `questions` | 题库：题目→选项→答案→解析→知识点→章节→错误次数（`fp` 唯一约束去重） |
| `wrong_book` | 错题库（`wrong_count`、原因、是否已掌握） |
| `sessions` / `answers` | 练习与考试会话、逐题作答记录（`submitted_by` 恒为 `user`） |
| `kb_docs` / `kb_chunks` | 资料与知识块（含页码/幻灯片定位、公式/定义分类、关键词） |
| `grades` | 课程成绩总评与逐项得分明细（只读抓取缓存，每次整批替换该课程历史） |
| `ai_calls` / `search_log` / `exam_audit` | AI 调用台账、搜索历史、考试模式行为审计（只读留痕） |

```powershell
# 直接用 SQLite 查看
python -c "import sqlite3;[print(r) for r in sqlite3.connect('data/study_helper.db').execute('SELECT title,kind,done,progress FROM items LIMIT 15')]"
```

---

## 六、排障与选择器适配

| 现象 | 处理办法 |
|---|---|
| 提示“没有识别到题目” | 先确认已进入答题页；再运行 `python main.py dump --url <页面地址>`，从 `data/dumps/*.html` 里找稳定 class，写入 `user_config.py` 的 `SELECTOR_OVERRIDES`，例如 `SELECTOR_OVERRIDES = {"question_root": ["div.newQuestion"], "question_stem": [".title"]}` |
| 目录/课程抓不到 | 同样用 `dump`，补 `course_card`、`catalog_section`、`catalog_group_title` |
| 视频不动 / 学到一半暂停 | 把浏览器窗口切回前台并保持（平台会暂停后台播放，本工具不绕过）；再次运行 `study` 会从断点继续 |
| AI 报 429/超时 | 已自动指数退避重试（`AI_MAX_RETRY`）；仍失败可降低并发/更换模型，或临时 `AI_ENABLE = False` 用纯本地检索 |
| 终端中文乱码 | 程序启动时会自动切换控制台到 UTF-8；仍乱码可执行 `chcp 65001` 或用 Windows Terminal |
| 想重置某门课进度 | `python main.py study --course X --all` 重做；或直接删 `data/study_helper.db`（会清空题库与知识库，慎用） |
| 改了代码不确定是否坏 | `python main.py selftest && python main.py btest`（selftest 29 项 / btest 12 项）|

---

## 七、安全与合规对照表（对应需求第七章）

| 要求 | 实现方式 |
|---|---|
| 仅考试明确允许 AI 时启用 | `EXAM_MODE_ALLOWED` + `EXAM_RULE_TEXT` 规则扫描 + 运行时逐字确认口令（`exam/guard.py`） |
| 不绕过验证码 | 代码中无任何验证码识别/提交逻辑；`wait_for_manual_login` 只轮询登录状态 |
| 不绕过登录验证 | 不代填账号密码；`SAFETY.allow_login_bypass = False`；强制可见窗口（`HEADLESS=False`） |
| 不破解/修改服务器数据 | 页面以只读为主（练习/演示模式的自动作答也只在你自己的答题表单内勾选/提交，不调用任何服务器写接口）；资料为只读 GET 下载；`SAFETY.allow_server_write = False` |
| 不伪造考试/学习记录 | 完成状态以平台标记或真实播放进度为准；读不到时要求人工确认；`study_log`/`exam_audit` 留痕 |
| 不修改考试倒计时 | `exam/snapshot.py::countdown` 只读文本解析展示；无任何写 DOM 能力 |
| 不绕过考试限制 | `ExamSnapshot.__getattr__` 对裸 `click/fill/submit/check/type/press...` 一律 `PermissionError` 并写审计；新增的 `apply_practice_actions/submit_practice` 只放行于练习模式、且真实考试域名仍被拒绝 |
| 真实考试页硬锁（防滥用） | `exam/guard.py::allow_page_write`：在 `REAL_EXAM_DOMAINS`（`chaoxing.com`/`xuexitong.com`/…）域名上采用“**只锁考试**”黑名单——URL 命中 `EXAM_URL_PATTERNS`（`/exam`、`examTest`、`exam-ans`、`testPaper`、`doExam`、监考/试考…）一律只读；其余（视频小节/作业/练习）在 `PRACTICE_MODE` 下允许自动作答。非这些域名（本地/`localhost`/演示）不受限。要最保守就把 `PRACTICE_MODE` 保持关闭 |
| 提交前必须本人确认 | **考试（`exam`）流程无自动提交**：`AUTO_SUBMIT` 由 `_enforce_safety` 强制为 `False`；提供 `T 提交前确认清单` 供你逐题核对（`SAFETY` 的 `allow_*` 为声明性标志，真实写能力只由上面的练习模式闸门 + 域名硬锁控制） |
| 不利用漏洞突破教师规则 | 无隐藏接口；只使用页面已有内容 |
| 检测到禁止 AI 自动关闭 | `scan_rules`/`runtime_check` 命中“闭卷/禁止 AI/不得查阅资料”→ 立即关闭并提示 |

---

### 学习通真实结构适配（重要）

不同课程的小节链接形式不同，本工具已覆盖三种：

| 结构 | 识别方式 |
|---|---|
| 小节有真实 `href`（含 `/knowledge/`） | 直接跳转 |
| 小节是 `onclick="toOld(courseId, jobId, clazzid, 0)"`（挂在 `div.chapter_item` 上） | 解析 `chapter_unit → chapter_item`，学习时真实点击该元素或调用页面 `toOld()` |
| 章节内容是异步加载的 iframe | 点“章节”标签后轮询等待渲染 |

排障链路（**不需要反复登录**）：

```powershell
python main.py capture --course 课程名        # 一次会话把门户页/章节页/小节页存成文件
python main.py sync --course 课程名 --from-capture data\capture\<时间戳>_chapter_frame01.html
```

登录态说明：学习通多为**会话级 Cookie**，进程退出即失效。本工具在登录成功后把 Cookie
快照到 `data\cookies.json`，并在每次启动时回灌，因此 `go`/`watch` 这类**单进程连续执行**
只需登录一次；把命令拆成多条独立进程则可能反复要求登录（所以推荐 `go`）。

---

## 八、命令速查

```
go         ★日常首选：登录一次 → 同步章节 → 自动学习（同一进程，不重复登录）
ask        直接把题目贴给 AI：答案+解析+知识点+资料依据+可信度
exercises  进入作业/章节测验列表 → 勾选 → 提取题目 → AI 解析
login      打开浏览器并等待你本人完成登录
watch      自动发现课程（浏览器全程保持打开）
pick       按课程名自动进入该课程并入库（匹配多门时必须你勾选）
capture    抓取真实页面现场到 data/capture（排障用，可离线再解析）
courses    同步我的课程列表
sync       同步某门课程的章节目录与学习项        --course --keep --from-capture <html>
study      自动学习未完成内容                    --course --limit --kinds --practice --no-kb --all --keep
practice   刷题辅助（默认本人作答；练习模式可自动作答/提交） --course --url --chapter --limit --auto-answer --auto-submit
kb         build|local|search|docs  知识库管理    --course --dir --limit --top
search     统一搜索（资料/章节/题目/错题）        <关键词...> --course --top --ai
wrong      list|stats|export|drill|similar 错题库  --course --limit --fmt md|json|csv --id --n
exam       开卷考试 AI 辅助（只读侧边栏）          --course --url --gui --console
grades     只读查看课程成绩与逐项明细（默认抓取并缓存） [课程] --cache --headless
read       阅读/文档任务点：估时停留(平台标注才算完成) [课程] --item --min-seconds
discuss    计分讨论：按规则凑分规划发帖/回复；--fill 填入(你点发表)；--submit 自动填入并提交
status     进度与统计
dump       导出页面 HTML 便于适配选择器            --url --tag
selftest   离线逻辑自检（29 项）
btest      离线页面结构自检（12 项，需 Chromium）
```

---

## 九、给二次开发者的提示

1. **考试页必须保持只读**：真实考试（`exam` 流程、或 URL 命中 `REAL_EXAM_DOMAINS`）绝不写入。练习/演示的自动作答/提交是**受闸门约束的 opt-in 能力**（`PRACTICE_MODE` + 域名硬锁 + `PRACTICE_ALLOW_SUBMIT`）；新增任何“自动勾选/提交”都必须经过 `exam/guard.py::allow_page_write` 并保留 `chaoxing/xuexitong` 硬锁。也不要为了刷课注入 `visibilitychange` 等伪造事件——这会越过学习合规边界。
2. 新增识别能力时：先扩展 `course/selectors.py` 的候选列表，再改业务代码；`user_config.SELECTOR_OVERRIDES` 是留给使用者的兼容补丁口。
3. 页面交互一律走 `browser/actions.py`（自带节流、重试、多候选选择器）。
4. 新表/字段改 `database/schema.sql`（`CREATE TABLE IF NOT EXISTS` 保证旧库自动补齐）。
5. 任何新输出格式请先加进 `ui/report.py`，保证终端与侧边栏一致；AI 相关提示词集中在 `ai/prompts.py`。
6. 修改后请运行 `selftest` 与 `btest`；新增能力时建议同步补一个用例（可参考 `tests/browser_selftest.py` 的本地 fixture 写法，不需要联网）。

---

## 十、当前已验证范围（诚实说明）

本项目自带 41 项自动化测试，均已通过：

- `python main.py selftest`（29 项）：SQLite 数据层、题库去重与答案规范化、5 种题型的 HTML 解析、
  知识库入库与 BM25 检索（含“第X章/公式怎么用”意图解析）、AI 输出的引用校验与可信度降级、
  考试合规闸门（含“配置未开启/规则禁止/写操作拒绝/运行中自动关闭”）、配置安全红线、
  练习模式写闸门（模式/真实考试域名/提交三级判定）、答案→写操作映射（单选/多选/判断/多空填空）、
  重试与防死循环、界面卡片渲染、提示词契约、小节工作台协作式调度（分步不独占/停止秒断不杀线程/
  忙时拒绝抢跑）、成绩与学习记录解析、考核权重与加分建议、成绩入库读取、讨论规则解析与凑分规划（含四道门）、
  讨论去重计数、
  阅读估时、退出登录清 Cookie 快照、登录检测兜底（漏识别仍记住/取消不误登出）、退出登录清空课程数据（保留题库）、判断题选项映射（A/对→第0项）。
- `python main.py btest`（12 项）：用本地 HTML 模拟学习通页面 + 真实 Chromium，验证
  目录抓取与任务类型识别、题目 JS 提取器、考试页只读快照（倒计时只读、当前题定位、
  **页面未被改动**、裸写方法一律拒绝）、禁止项自动关闭、播放器定位与倍速红线、
  页面正文入库与检索、AI 不可用降级、考试辅助全链路（含提交前清单）、自动学习主循环与进度落库、
  真实 `toOld()` 章节结构解析、随堂弹题探测与 AI 解析入库、弹题严格判真（导航文字不入库）。

需要你首次接入真实课程时自行确认的部分：

1. **学习通线上页面的选择器可能需要校准**（平台前端会改版）。若某类内容抓不到，先执行
   `python main.py dump --url <页面地址>`，把 `data/dumps/*.html` 里稳定的 class/id 写进
   `user_config.py` 的 `SELECTOR_OVERRIDES` 即可，无需改动业务代码。
2. **平台“任务点完成”的判定口径**：视频以播放器真实结束/进度为准，文档以平台标记为准；
   标记读不到时程序会请你人工确认，**绝不自行判定完成**。
3. **考试模式**请在你确认“本场考试允许开卷 + 允许 AI”之后再开启，并保留 `exam_audit` 记录。
