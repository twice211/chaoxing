# 设计:阅读计时 / 成绩查看 / 计分讨论自动参与

日期:2026-09-26 · 分支:master · 状态:待评审(方案 B,独立入口)

## 1. 目标

在小节工作台(`ui/workbench.py`)新增三个**彼此独立**的功能入口,并保留等价 CLI:

1. **过阅读章节(可设时长)**:对文档/PPT/阅读类小节真实停留计时,达到平台要求后按其标注判定完成。用户可设"最低停留秒数"覆盖自动估算。
2. **查看分数 + 明细**:只读抓取课程总评成绩与逐项得分明细,存入本地 `grades` 表留历史,工作台「✎ 成绩」标签展示。
3. **参与计分讨论**:仅对**被算进成绩/分数的讨论任务**(由功能2 识别)用 AI 自动**发帖 + 回答**;默认关,三道安全门。

## 2. 非目标

- **不**接入现有「逐节连做」(`_chain_gen`);本方案为方案 B,三功能各自独立按钮/入口。
- **不**做全课程扫楼式发帖——只处理成绩里计分的讨论任务。
- **不**动考试页:考试页仍由 `exam/guard` 硬锁只读。
- **不**动 SAFETY 四个红线标志;不新增"绕过监考/破验证码"能力;`allow_captcha_solve` 保持 False。
- **不**伪造完成状态:阅读/测验完成以平台标注或用户确认为准(沿用 `platform_says_done` 风格)。

## 3. 复用的现有设施

- **协作式调度**:`Scheduler._start_task/_step_task/_wait`、`task_stop`(取消任务)与 `stop_event`(关线程)已分离;三功能的耗时循环都以生成器任务实现,可随时"停止"。
- **写闸门**:讨论的 fill+提交必须走单一写闸口。`exam/snapshot.py` 现有 `apply_practice_actions`(选项点击/勾选)与 `_assert_writable`;新增 `apply_text_actions(page, spec)`(在 textarea 填文本 + 点发布),同样经 `_assert_writable`,复用 `browser/__getattr__` 对裸 click/fill/submit 的拒绝。
- **AI 引擎**:`ai/responder.AnswerEngine`,新增"讨论发言"人格提示词。
- **选择器**:`course/selectors.py::SELECTORS` 多候选 + `user_config.SELECTOR_OVERRIDES` 覆盖;新键归到 `grade_*` 与 `discussion_*`。
- **可编辑配置**:`utils/settings_io.py::EDITABLE` 增键即出现在设置窗口。

## 4. 功能详细设计

### 4.1 功能1:阅读计时(可设时长)

- 现状:`video/player.py:463 VideoWatcher.read_document` 阻塞、时长写死。
- 新增 `Scheduler.do_read(item_id)`:打开该小节 → 取正文长度 → `need = max(READ_MIN_SECONDS, min(READ_MAX_SECONDS, text_len / READ_CHARS_PER_SEC))` → 协作式生成器 `_read_doc_gen` 每 `_wait` 一拍累计停留,写 `update_progress`;结束看 `platform_says_done(page)` 决定 `finish_item`,否则输出"需人工确认"。
- 完成判定与 `read_document` 一致(不伪造)。`platform_says_done` 复用现有实现。
- UI(视频标签内新增一行):「阅读本节(计时)」按钮 + 「最低停留秒数」输入(默认取 `READ_MIN_SECONDS`)。
- CLI:`python main.py read --item <id>`(或复用 study 的文档分支,不强制)。
- 关键选择器:`READ_DONE_FLAG`(平台"已完成"标记),放进 `SELECTORS` 可覆盖。

### 4.2 功能2:成绩(只读)

- `course/crawler.py` 新增:
  - `fetch_course_grade(page) -> dict`:课程总评(如"你的成绩:85.5"及各模块权重/得分),解析文本+DOM。
  - `fetch_grade_detail(page) -> list[dict]`:逐项明细行(name/kind/score/full_score/weight),供"讨论是否计分"判定与展示。
- 新表 `grades(course_id,name,kind,score,full_score,weight,ts)`;`Store.upsert_grades(course_id, rows)`、`Store.list_grades(course_id)`。
- `Scheduler.do_grades()`:打开成绩页 → 抓取 → 落库 → 输出 `("grades", payload)`;工作台第三标签 **「✎ 成绩」**(只读表 + "刷新成绩"按钮)。
- `grades` 表加两列便于功能3 使用:`need_post INTEGER`、`need_reply INTEGER`(成绩细则若写明"发帖N/回复M"则填,否则空)。
- 明细里含"讨论"且带分值/要求的行 → 供功能3 的 `graded_discussion_targets` 消费(**注意:讨论本体在独立讨论区,不在目录任务点**,见 4.3)。
- CLI:`python main.py grades [--open]`。
- 纯只读,`allow_page_write` 不涉及。

## 4.3 功能3:计分讨论自动参与(写,默认关)

> **定位修正(2026-09-26):** 阅读是章节目录里的"任务点"(按小节处理,见 4.1);**讨论是课程里另一块独立区域**(课程讨论/讨论板),不嵌在章节任务点里。故功能3 在**独立讨论区**内操作,不依赖目录 `item.kind==discussion`。

- 新模块 `course/discussion.py::DiscussionRunner`:
  - `open_discussion_board(page, course)`:从课程门户进入独立的"讨论"区域(`crawler.click_course_tab("讨论")` 已有通用顶部标签点击可复用)或直达讨论 URL。
  - `graded_discussion_targets(grade_rows) -> list[target]`:**以成绩明细/细则为准**——从功能2 抓到的 `grades` 里挑"讨论"相关计分项,解析出其**要求**(如"需发帖 N 条 / 回复 M 条,值 X 分")与对应讨论话题定位。**不自动碰成绩里没有的普通讨论**(Q-D1 定案)。
  - `plan_discuss_actions(target, posts, done) -> list[action]`:纯函数,**"完成分数即可"**——按 target 要求的发帖/回复条数,结合本话题已完成数(`discussions` 表)与现有帖子,算出还差几条、分别是"发帖"还是"回答帖子k";够分即返回空列表(不超发)。整体再受 `DISCUSS_MAX` 每轮上限兜底(Q-D2 定案 A)。
  - `_discuss_gen(targets)`:协作式生成器,逐话题:进入讨论区打开话题 → 抽取帖子 → `plan_discuss_actions` → 需要时 `engine.answer`/新讨论人格生成文本 → **在填/发前输出** `⚠ 将以你的账号公开到学习通讨论区:<标题>` → `snapshot.apply_text_actions` → 记 `discussions` 表(内容指纹去重 + 计数,供"够分即停")。
- 新表 `discussions(course_id,item_id,topic_key,kind(post/reply),fp,status,ts)`;`fp` = 内容指纹,`Store.mark_discussed(fp)`/`is_discussed(fp)` 防重复。
- **三道门**(全过才写):
  1. `PRACTICE_MODE` 开;
  2. `PRACTICE_ALLOW_DISCUSS_POST` 开(**默认关**,新键);
  3. `guard.allow_page_write(url, want_submit=False)` 通过(讨论页非考试路径 → 通过;命中 `EXAM_URL_PATTERNS` → 拒绝)。
  - 另外 `DISCUSS_MAX`(每轮上限,默认 5)控制发帖量,防刷屏。
- 内容:`AnswerEngine` 新增 `mode="discussion"`,提示词以课程名+章节/话题标题+帖子上下文为条件,要求"自然、简洁、像真实学生发言,不编造事实"。needs_human 或空答案则**跳过该条**(不水贴、不空发)。
- UI(章节检测标签或新「讨论」区):「同步成绩」→「参与计分讨论」按钮 + 「允许自动发帖/回复」勾选 + 每轮上限输入。停止键复用 `stop_task`。
- CLI:`python main.py discuss [--max 5]`(需 `PRACTICE_MODE` + `PRACTICE_ALLOW_DISCUSS_POST`)。

## 5. 配置新增(settings_io.EDITABLE + config.example.py)

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `READ_MIN_SECONDS` | int | 30 | 阅读最低停留秒数(覆盖自动估算下限) |
| `READ_MAX_SECONDS` | int | 240 | 阅读停留上限 |
| `READ_CHARS_PER_SEC` | int | 12 | 自动估算速度(字符/秒) |
| `PRACTICE_ALLOW_DISCUSS_POST` | bool | **False** | 允许对计分讨论自动发帖/回复 |
| `DISCUSS_MAX` | int | 5 | 每轮讨论处理上限 |

## 6. 选择器新增(SELECTORS,均多候选 + 可覆盖)

- `grade_overview` / `grade_detail_row` / `grade_name` / `grade_score` / `grade_full` / `grade_weight`
- `discussion_topic_list` / `discussion_post_item` / `discussion_reply_box` / `discussion_reply_submit` / `discussion_new_entry` / `discussion_post_title` / `discussion_post_body` / `discussion_post_submit`
- `READ_DONE_FLAG`

## 7. 测试计划(离线 selftest,纯逻辑)

1. 阅读时长计算:`text_len`/上下限/覆盖值 → `need` 正确。
2. 成绩解析:样例 HTML/文本 → 总评 + 明细行断言。
3. 讨论目标解析 + 规划:样例成绩明细(含"讨论 发帖2/回复3 值5分")→ `graded_discussion_targets` 解析出要求;`plan_discuss_actions` 按"已完成数/够分即停"给出正确动作(发帖/回答/空),并受 `DISCUSS_MAX` 截断。
4. 讨论三道门:仿现有 `t_practice_lock`,验证 `PRACTICE_ALLOW_DISCUSS_POST=False` 时拒绝、考试 URL 命中时拒绝。
5. 协作式调度回归项(已存在)覆盖停止/忙时拒绝抢跑对新任务同样生效。

浏览器真实提交不进 selftest,留给 `btest` / `dump` 手动定位选择器 + demo 页(`docs/demo/discussion_demo.html`)。

## 8. 改动文件清单

- `config.example.py`、`user_config.py`(注释块)、`utils/settings_io.py`(EDITABLE)
- `course/selectors.py`(grade/discussion/read 选择器)
- `course/crawler.py`(成绩抓取)
- `course/discussion.py`(新)
- `exam/snapshot.py`(`apply_text_actions` 受闸文本写入)
- `video/player.py`(或 `ui/workbench.py` 内 `_read_doc_gen`,协作式阅读)
- `ai/responder.py`(讨论人格 `mode="discussion"`)
- `ui/workbench.py`(成绩标签、三功能任务/按钮/勾选、`out` 新增 `grades` 事件、`_NEEDS_IDLE` 纳入 `read/grades/discuss`)
- `main.py`(CLI:`read`/`grades`/`discuss`)
- `database/schema.sql`、`database/store.py`(`grades`、`discussions` 表 + 方法)
- `tests/selftest.py`(新自检项)、`docs/demo/discussion_demo.html`
- `README.md`(§4/§7/§9 同步;测试计数 selftest 14→18、btest 视新增)

## 9. 风险与对策

- **页面改版**:讨论/成绩选择器易变 → 全走多候选 + `SELECTOR_OVERRIDES` + `dump` 调试;定位不到则安全跳过(不空发、不乱点)。
- **公开不可逆**:AI 以你身份发言可能不当 → 默认关 + 仅计分任务 + `DISCUSS_MAX` 限量 + needs_human/空答案跳过 + 发前明示提示行。
- **与播放/作答互斥**:三任务都走 `_start_task`(自动停 session、清空堆队列、忙时拒绝抢跑),复用既有护栏。
- **防刷**:内容指纹 + `discussions` 记录,同话题/同内容不重发。

## 10. 决策记录(已定)

- **Q-D1 计分讨论来源 = 成绩明细/细则**:只处理功能2 抓到的成绩里"讨论"计分项,**不碰成绩里没有的普通讨论**。细则若写明发帖/回复条数与分值,解析进 `grades.need_post/need_reply`。
- **Q-D2 动作 = A,且"完成分数即可"**:按细则要求做到够分就停(发帖/回复按需,`plan_discuss_actions` 够分返回空),`DISCUSS_MAX` 每轮兜底上限。
- **草稿态**:讨论**不加**"暂存不发布"选项(讨论区通常无暂存)——要么按三道门自动发布、要么不动。若之后要"生成草稿但不发",另立小改。
