# Discussion (计分讨论自动参与) Implementation Plan — Plan 3 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对**成绩里计分的讨论模块**(本例占 5%),在工作台/CLI 用 AI 自动**发帖 + 回复**达标;默认关、三道安全门、去重、限量,绝不灌水。

**Architecture:** 纯逻辑 `course/discussion.py`(`graded_discussion_targets`/`plan_discuss_actions`/`fingerprint` 可离线自检)决定“还差几条、发还是回”;写入统一走 `exam/snapshot.py` 新增的受闸 `apply_text_actions`(经 `_assert_writable`,与自动作答同一道锁);真实抓取/发帖用 `SELECTORS` 多候选 + demo 页测,真机选择器用一次“讨论页导出”对齐(同成绩适配套路)。全程协作式生成器,可 `stop_task` 秒停。

**Tech Stack:** Python 3.11、Playwright(sync)、SQLite、Tkinter;AI 复用 `ai/responder.AnswerEngine`。测试沿用离线自检 + 本地 demo(不向真实学习通发帖)。

**Spec:** `docs/superpowers/specs/2026-09-26-read-grade-discussion-design.md`(§4.3、§5、§6、§10)

## Global Constraints

- 全中文文案/日志;注释讲“为什么”。
- **三道门全过才写**(否则只读/跳过并说明):① `PRACTICE_MODE` 开;② 新键 `PRACTICE_ALLOW_DISCUSS_POST` 开(**默认 False**);③ `exam/guard.allow_page_write(url, want_submit=False)` 通过(讨论页非考试 URL → 通过;命中考试 URL → 拒绝)。
- 只处理**成绩里计分**的讨论(`grades` 里 `kind=='discussion'` 且 `weight>0`);**不碰**成绩里没有的普通讨论。
- “完成分数即可”:达到目标参与量就停,不灌水;`DISCUSS_MAX` 每轮上限、`DISCUSS_POST_TARGET`/`DISCUSS_REPLY_TARGET` 默认小值(2/5)。
- 每条发帖/回复前,输出流打一行 `⚠ 将以你的账号公开到学习通讨论区：<标题>`;发后记 `discussions`(内容指纹去重)。
- AI 不启用 / needs_human / 空答案 → **跳过该条**(不空发、不灌水)。
- **不新增服务器写接口**:仅用现有页面表单(填文本 + 点发布按钮),且都过 `_assert_writable`。SAFETY 四红线不动,`allow_captcha_solve` 保持 False。
- 耗时循环走协作式生成器(`Scheduler._start_task`),停止用 `task_stop`;讨论任务纳入 `_NEEDS_IDLE`。

---

## File Structure

- Create `course/discussion.py` — 纯逻辑:`graded_discussion_targets`、`plan_discuss_actions`、`content_fp`;及 `DiscussionRunner`(抓取话题 + 编排发帖/回复,浏览器操作)。
- Modify `exam/snapshot.py` — 新增受闸 `apply_text_actions(page, spec)`(fill textarea + click 发布,均过 `_assert_writable`)。
- Modify `database/schema.sql` + `database/store.py` — 新增 `discussions` 表 + `mark_discussed`/`is_discussed`/`discuss_done_counts`。
- Modify `course/selectors.py` — 新增 `disc_*` 选择器(话题列表/标题/正文/发帖按钮/回复框/回复提交)。
- Modify `utils/settings_io.py`(EDITABLE)+ `config.example.py` — 新增 `PRACTICE_ALLOW_DISCUSS_POST`/`DISCUSS_MAX`/`DISCUSS_POST_TARGET`/`DISCUSS_REPLY_TARGET`。
- Modify `ai/responder.py`(或 `ai/prompts.py`)— 讨论发言人格 `mode="discussion"`。
- Modify `ui/workbench.py` — 「讨论」按钮/勾选 + `Scheduler.do_discuss` 协作任务;`_NEEDS_IDLE` 加 `discuss`。
- Modify `main.py` — CLI `discuss [--dry-run]`。
- Create `docs/demo/discussion_demo.html` — 发帖/回复表单本地样例,供离线结构测试。
- Modify `tests/selftest.py` — 新增纯逻辑/装配/去重离线自检项;`tests/browser_selftest.py` — demo 页结构自检项。

---

## Task 1: 讨论目标解析 + 动作规划(纯函数,可离线测)

**Files:** Create `course/discussion.py`;Test `tests/selftest.py`(`t_discuss_plan`)。

**Interfaces(Produces):**
- `content_fp(text: str) -> str` — 去重指纹(复用 `utils.text.fingerprint`)。
- `graded_discussion_targets(grade_rows: list[dict], weight_min: float = 0.0) -> list[dict]` — 从成绩行挑 `kind=='discussion'` 且 `weight>weight_min`,返回 `[{"name","weight","rule"}]`。
- `plan_discuss_actions(target_done: dict, post_target: int, reply_target: int, topics: list[dict], max_this_round: int) -> list[dict]`
  - `target_done = {"new_posts":int,"replies":int}`;`topics=[{"key":str,"replied":bool,...}]`。
  - 返回动作 `[{"type":"new_post","fp":str}]` 或 `[{"type":"reply","topic_key":str,"fp":str}]`,**够目标即空**;先补发帖再补回复;`len(actions) <= max_this_round`;回复只挑未回复过的话题。

- [ ] Step 1: 写失败自检 `t_discuss_plan`(喂 grades 行含 `{"name":"考核·讨论","kind":"discussion","weight":5}`,断言 target 命中;plan:done 0/目标 2-5/topics 3 → 期望先 2 发帖再补回复,受 max 截断;够目标→[])。注册 `check("讨论目标与规划", t_discuss_plan)`。
- [ ] Step 2: 跑 `python main.py selftest` 确认失败(ImportError `course.discussion`)。
- [ ] Step 3: 实现纯函数(discussion.py 顶部只放纯逻辑;`DiscussionRunner` 占位后续任务补)。
- [ ] Step 4: 跑自检确认通过(19→20 项)。
- [ ] Step 5: 提交。

## Task 2: discussions 表 + Store 去重/计数

**Files:** Modify `database/schema.sql`、`database/store.py`;Test `tests/selftest.py`(`t_discuss_store`)。

**Interfaces(Produces):**
- 表 `discussions(id, course_id, kind, topic_key, fp, title, status, url, created_at)`(UNIQUE(course_id, fp))。
- `Store.mark_discussed(course_id, kind, topic_key, fp, title, url, status='posted') -> bool`(重复 fp 返回 False 不写)。
- `Store.is_discussed(course_id, fp) -> bool`。
- `Store.discuss_done_counts(course_id) -> dict`(`{"new_posts":int,"replies":int}`,按 kind 统计 status='posted')。
- `Store.list_discussions(course_id, limit=100)`。

- [ ] Step 1: 写失败自检(标记→计数→重复 fp 被拒)。注册 `check("讨论去重与计数", t_discuss_store)`。
- [ ] Step 2: 跑确认失败。
- [ ] Step 3: 建表(幂等)+ 方法(参考 grades 的 exec/exec_many/rows_to_dicts)。
- [ ] Step 4: 跑确认通过(20→21)。
- [ ] Step 5: 提交。

## Task 3: 受闸文本写入 apply_text_actions

**Files:** Modify `exam/snapshot.py`;Test `tests/selftest.py`(`t_text_write_guard`:断言考试页/非练习模式被 `PermissionError` 拒)。

**Interfaces:**
- `ExamSnapshot.apply_text_actions(page, spec) -> dict` — `spec={"fill":[{"selector_candidates","text"}], "click":[{"selector_candidates","force"}]}`;每个 fill 走 `_assert_writable` 再 `el.fill`,click 走受闸路径(沿用 `submit_practice` 里定位按钮 + 点击的既有受闸风格)。返回 `{"ok":bool,"filled":int,"clicked":int,"reason":str}`。

- [ ] Step 1: 写失败自检(构造 snapshot + 假 page 记录 fill/click;练习模式关闭或非考试允许域时 `_assert_writable` 抛 PermissionError→ok False 且不触发点击)。注册 `check("讨论文本写入受闸", t_text_write_guard)`。
- [ ] Step 2: 跑确认失败。
- [ ] Step 3: 实现(复用现有 `_assert_writable`/`_find_first_in_frames`;不新增绕过)。
- [ ] Step 4: 跑确认通过(21→22)。
- [ ] Step 5: 提交。

## Task 4: 讨论选择器 + demo 页 + DiscussionRunner(抓取/编排)

**Files:** Modify `course/selectors.py`;Create `docs/demo/discussion_demo.html`;Modify `course/discussion.py`(加 `DiscussionRunner`);Test `tests/browser_selftest.py`(demo 结构自检)。

**Interfaces:**
- `SELECTORS` 新增:`disc_topic_list`、`disc_topic_title`、`disc_new_post_btn`、`disc_post_title_input`、`disc_post_body`、`disc_post_submit`、`disc_reply_box`、`disc_reply_submit`(均多候选,可覆盖)。
- `DiscussionRunner.__init__(browser, cfg, store, crawler, engine, snap, guard)`;
  - `open_board(page, course)` → `_open_nav_tab(page, ("讨论",))` 或直达 `groupweb.chaoxing.com/course/topic/topicList`;
  - `list_topics(page) -> list[dict]`(key/title/replied);
  - `_gen_text(page, kind, topic) -> str`(engine mode=discussion;空/needs_human→"");
  - `do_discuss_gen(course) -> Any` 协作式生成器(下面 Task6 挂 Scheduler):逐门→open_board→list_topics→targets(grades)→done_counts→plan_discuss_actions→对每个动作:AI 生成文本→`⚠…公开…`→`apply_text_actions`→`mark_discussed`→`yield None`/`yield from _wait(小)`。

- [ ] Step 1: 写 demo 页 + `browser_selftest` 断言(在本地 demo 里能按选择器定位到发帖标题/正文/发布、回复框/提交、话题列表项)。跑确认失败。
- [ ] Step 2: 加 `disc_*` 选择器使 demo 命中 → 通过。
- [ ] Step 3: 实现 `DiscussionRunner`(抓取 + 编排,真实页选择器待 Task7 对齐)。
- [ ] Step 4: 全量自检 + btest 通过。
- [ ] Step 5: 提交。

## Task 5: 配置项 + AI 讨论人格 + 三道门接入

**Files:** Modify `utils/settings_io.py`(EDITABLE)、`config.example.py`、`ai/responder.py`/`ai/prompts.py`。

**Interfaces:** 新键 `PRACTICE_ALLOW_DISCUSS_POST`(bool,默认False)、`DISCUSS_MAX`(int,默认5)、`DISCUSS_POST_TARGET`(int,默认2)、`DISCUSS_REPLY_TARGET`(int,默认5);`AnswerEngine.answer(..., mode="discussion")` 人格:“以课程+话题为背景,写一段自然、简短、像真实学生的讨论发言,不编造事实、不复读标题”。`DiscussionRunner` 在动作前调用一个 `self._allowed(page)`(= PRACTICE_MODE and PRACTICE_ALLOW_DISCUSS_POST and guard.allow_page_write(url, want_submit=False))。

- [ ] Step 1: 加配置 + 人格 + 门(纯接线,无新增可测断言则并入下一自检)。
- [ ] Step 2: `python main.py selftest` 确认不破坏(≥22 项)。
- [ ] Step 3: 提交。

## Task 6: 工作台讨论入口 + do_discuss 协作任务

**Files:** Modify `ui/workbench.py`。

**Interfaces:** `_NEEDS_IDLE` 加 `"discuss"`;`Scheduler.do_discuss(save=True, dry_run=False)`:`_ensure_ai/_ensure_crawler`→建 `DiscussionRunner`→`_start_task(runner.do_discuss_gen(course, dry_run))`;UI 在章节检测/新区域加「讨论」按钮(“仅预览草稿/执行发帖回复”)+ 勾选 `允许自动发帖回复(PRACTICE_ALLOW_DISCUSS_POST)` + 每轮上限输入;`dry_run` 时只生成草稿输出、不调用写入。

- [ ] Step 1: 加 `do_discuss` + UI 控件 + 事件渲染(复用 output 流;草稿用 `("raw", …)`)。
- [ ] Step 2: `python -c "import ui.workbench"` + 全量 selftest 通过。
- [ ] Step 3: 提交。

## Task 7: CLI discuss + README + 真机讨论页对齐

**Files:** Modify `main.py`(cmd_discuss)、`README.md`;**对齐检查点**:`python main.py dump --url <讨论区地址>` 导出真实讨论页,据实补 `disc_*` 选择器。

**Interfaces:** `python main.py discuss [--dry-run] [--course 关键词]`:默认 `--dry-run` 只预览草稿(不写);显式去掉 dry-run 且满足三道门才真实发布。

- [ ] Step 1: 实现 `cmd_discuss`(默认 dry-run;真实发布需 `PRACTICE_ALLOW_DISCUSS_POST` 且二次确认 `confirm()`)。
- [ ] Step 2: README 增“讨论(默认关/仅计分/需确认)”条目 + 安全对照 + 计数(+2~4 自检)。
- [ ] Step 3: 真机:用户跑 `discuss --dry-run` → 若草稿正常、`disc_*` 未命中则 dump 讨论页 → 适配选择器 → 再 `discuss --dry-run` → 用户确认后才允许真实发布(小量)。
- [ ] Step 4: 全量 selftest 全绿后提交。

---

## Self-Review(作者核对)

1. Spec 覆盖:§4.3 三道门/仅计分/够分即停/去重/公开提示、`apply_text_actions`、`discussions` 表、CLI、demo、selftest 均有任务。✓
2. 占位符:无 TBD;纯函数与表/门可离线测,真实发帖留 Task7“dry-run→dump→适配”闭环(与成绩适配同套路)。✓
3. 类型一致:`target_done{new_posts,replies}` 与 `discuss_done_counts` 返回、`plan_discuss_actions` 入出、`mark_discussed(fp)` 去重键一致;`allow_page_write(url, want_submit=False)` 签名一致。✓

> 安全底线:真实发帖不可逆 → 默认 dry-run + 默认关 + 二次确认 + 限量 + 仅计分讨论;开发期只用本地 demo 测,不向真实学习通发内容。
