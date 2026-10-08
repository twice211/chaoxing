-- ============================================================
-- study_helper SQLite 结构
-- 说明：全部数据仅存在本机；不写入学习通服务器的任何内容。
-- ============================================================
PRAGMA foreign_keys = ON;

-- 课程
CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_key    TEXT NOT NULL UNIQUE,      -- courseId 或 courseId_cpi 组合
    name          TEXT NOT NULL,
    url           TEXT,
    cpi           TEXT,
    clazzid       TEXT,
    teacher       TEXT,
    class_name    TEXT,
    progress_text TEXT,                       -- 页面显示的学习进度（只读抓取）
    synced_at     TEXT,
    updated_at    TEXT
);

-- 章节（目录树，扁平存储 + parent_key 表达层级）
CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    chap_key   TEXT NOT NULL,                -- 章节稳定标识
    no         TEXT,                          -- 显示编号：第一章 / 2.3
    title      TEXT NOT NULL,
    parent_key TEXT,
    level      INTEGER DEFAULT 1,
    order_idx  INTEGER DEFAULT 0,
    UNIQUE (course_id, chap_key)
);

-- 学习项（视频/文档/PPT/章节测验/练习题）
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    item_key     TEXT NOT NULL,               -- url 的 sha1，稳定去重
    title        TEXT,
    kind         TEXT NOT NULL,               -- video|document|ppt|exam|work|练习|other
    url          TEXT,
    chapter_key  TEXT,
    done         INTEGER DEFAULT 0,           -- 0 未完成 / 1 已完成
    progress     REAL DEFAULT 0,              -- 0~1 真实观看/阅读进度
    watch_sec    REAL DEFAULT 0,              -- 累计真实学习秒数
    attempts     INTEGER DEFAULT 0,           -- 进入次数（含失败重试）
    last_error   TEXT,
    first_seen   TEXT,
    last_seen    TEXT,
    finished_at  TEXT,
    UNIQUE (course_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_items_done ON items(course_id, done);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);

-- 学习流水（每一步真实动作，用于断点续学与审计）
CREATE TABLE IF NOT EXISTS study_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER,
    item_id   INTEGER,
    ts        TEXT,
    event     TEXT,      -- start|progress|finish|retry|fail|skip|manual_login
    detail    TEXT,
    seconds   REAL
);
CREATE INDEX IF NOT EXISTS idx_study_log_ts ON study_log(ts);

-- 题库：题目 → 选项 → 答案 → 解析 → 知识点 → 章节 → 错题次数
CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id      INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    fp             TEXT NOT NULL UNIQUE,      -- 题目指纹（去重复题）
    kind           TEXT,                      -- single|multi|judge|blank|short|unknown
    stem           TEXT NOT NULL,
    options_json   TEXT,                      -- ["A. xx","B. yy"]
    answer         TEXT,                      -- 标准/用户确认答案
    answer_source  TEXT,                      -- manual|ai|platform（平台判题结果）
    analysis       TEXT,
    knowledge      TEXT,                      -- 知识点（多个用；分隔）
    chapter_key    TEXT,
    section_url    TEXT,
    seen_count     INTEGER DEFAULT 1,
    wrong_count    INTEGER DEFAULT 0,
    correct_count  INTEGER DEFAULT 0,
    difficulty     TEXT,                      -- 易|中|难
    is_ai_similar  INTEGER DEFAULT 0,         -- 是否为针对错题生成的强化题
    origin_qid     INTEGER,                   -- 强化题源自哪道错题
    created_at     TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_course ON questions(course_id);
CREATE INDEX IF NOT EXISTS idx_q_wrong ON questions(wrong_count);

-- 练习/考试会话
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER,
    mode        TEXT NOT NULL,                -- practice|exam|wrongbook
    title       TEXT,
    url         TEXT,
    started_at  TEXT,
    ended_at    TEXT,
    total       INTEGER DEFAULT 0,
    ai_called   INTEGER DEFAULT 0,
    note        TEXT
);

-- 每次作答记录（提交前由用户确认，程序不代提交）
CREATE TABLE IF NOT EXISTS answers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER,
    question_id  INTEGER,
    qno          TEXT,
    user_answer  TEXT,
    ai_answer    TEXT,
    confidence   TEXT,                        -- 高|中|需人工确认
    is_correct   INTEGER,                     -- 平台判题后回填（只读抓取）
    submitted_by TEXT DEFAULT 'user',         -- 恒为 user：程序不自动提交
    updated_at   TEXT
);

-- 错题库
CREATE TABLE IF NOT EXISTS wrong_book (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id  INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    course_id    INTEGER,
    chapter_key  TEXT,
    wrong_count  INTEGER DEFAULT 1,
    last_wrong_at TEXT,
    reason       TEXT,                        -- 概念不清|计算错误|审题失误|记忆模糊
    note         TEXT,
    resolved     INTEGER DEFAULT 0,           -- 连续答对后标记掌握
    UNIQUE (question_id)
);

-- 知识库：资料 → 章节 → 分块 → 知识点/公式/定义
CREATE TABLE IF NOT EXISTS kb_docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER,
    chapter_key TEXT,
    title       TEXT,
    doc_type    TEXT,                          -- pdf|docx|pptx|txt|md|html|video_note
    path        TEXT,
    url         TEXT,
    size        INTEGER,
    chars       INTEGER,
    status      TEXT DEFAULT 'pending',        -- pending|done|failed|skipped
    error       TEXT,
    ingested_at TEXT,
    UNIQUE (course_id, path)
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL REFERENCES kb_docs(id) ON DELETE CASCADE,
    course_id   INTEGER,
    chapter_key TEXT,
    seq         INTEGER,
    loc         TEXT,                          -- 第3页 / 第12张幻灯片
    kind        TEXT DEFAULT 'text',           -- text|formula|definition|example
    text        TEXT NOT NULL,
    terms       TEXT,                          -- 空格分隔的关键词（用于快速过滤）
    UNIQUE (doc_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_chunks_course ON kb_chunks(course_id);

-- AI 调用台账（次数/耗时/失败，便于排查“请求失败可重试”）
CREATE TABLE IF NOT EXISTS ai_calls (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT,
    purpose  TEXT,
    model    TEXT,
    status   TEXT,                             -- ok|error|retry
    ms       REAL,
    est_chars INTEGER,
    error    TEXT
);

-- 搜索历史（考试期间的本地检索记录）
CREATE TABLE IF NOT EXISTS search_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT,
    scope TEXT,                                -- kb|question|wrong|exam
    query TEXT,
    hits  INTEGER
);

-- 考试模式审计：证明“只读、不改动页面、不代提交”
CREATE TABLE IF NOT EXISTS exam_audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ts       TEXT,
    event    TEXT,      -- enable|read_question|ai_answer|scroll|disable|refused_write
    detail   TEXT
);

-- 成绩明细(只读抓取;每次同步整批替换该课程历史)
CREATE TABLE IF NOT EXISTS grades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    kind       TEXT DEFAULT 'other',          -- exam|work|practice|discussion|video|doc|other
    score      REAL,                          -- 抓不到为 NULL(不臆造)
    full       REAL,
    weight     REAL,                          -- 占比/权重(若页面给出)
    overview   TEXT,                          -- 课程总评(冗余存每行,便于单查)
    note       TEXT,                          -- 得分说明/考核规则(如讨论加分规则)
    synced_at  TEXT,
    credit_base_score REAL,                   -- 首次观测分数；本地已确认内容从此基线累计
    credit_base_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_grades_course ON grades(course_id);

-- 讨论参与记录(发帖/回复;内容指纹去重,防灌水;仅计分讨论)
CREATE TABLE IF NOT EXISTS discussions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                 -- new_post|reply
    topic_key  TEXT DEFAULT '',               -- 回复对应的话题标识
    fp         TEXT NOT NULL,                 -- 内容指纹(去重)
    title      TEXT,                          -- 话题/帖子标题(便于查看)
    status     TEXT DEFAULT 'posted',         -- posted|draft|failed
    url        TEXT,
    created_at TEXT,
    posted_at  TEXT,                          -- 实际发布/人工确认时间；草稿时为空
    UNIQUE (course_id, fp)
);
CREATE INDEX IF NOT EXISTS idx_disc_course ON discussions(course_id, kind);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
