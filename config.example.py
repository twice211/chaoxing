# -*- coding: utf-8 -*-
"""
config.example.py —— 用户配置模板

使用方法：
    1) 复制本文件为同目录下的 user_config.py
    2) 修改 user_config.py 中的账号相关、AI 相关配置
    3) 程序运行时由 config.py 自动读取 user_config.py（未找到则回退读取本模板）

安全说明：本文件只提供“读取/展示/辅助”类开关，不提供任何绕过登录、验证码、
监考、考试时长、提交限制的配置项；此类能力在代码里被硬性禁止（见 config.SAFETY）。
"""

# ==================== 一、浏览器与会话 ====================
# 学习通入口（一般无需修改）
CHAOXING_LOGIN_URL = "https://passport2.chaoxing.com/login?newversion=true&refer=https://i.chaoxing.com"
CHAOXING_HOME_URL = "https://i.chaoxing.com"
COURSE_LIST_URL = "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu"

# 浏览器用户数据目录：登录状态保存在这里，下次启动免重复登录
USER_DATA_DIR = "data/browser_profile"
BROWSER_CHANNEL = "chromium"     # chromium / msedge / chrome
HEADLESS = False                 # 学习需要看到画面，必须为 False
SLOW_MO_MS = 0                   # 调试用放慢毫秒
WINDOW_SIZE = {"width": 1440, "height": 900}
BROWSER_CDP_PORT = 0             # 0=关闭；设为如 9222 会给浏览器开本机调试端口(仅 127.0.0.1)，便于外部工具读话题/协作填入；不用时改回 0

# 登录/验证码/身份验证全部由用户本人完成；程序只负责“等待你登录好”
LOGIN_WAIT_TIMEOUT_SEC = 900     # 最长等待 15 分钟
LOGIN_POLL_SEC = 3

# ==================== 二、学习与刷题行为约束 ====================
# 视频播放速率：只允许 1.0 倍（正常播放）。写死为 1.0，改大无效（见 config.py 校验）
VIDEO_PLAYBACK_RATE = 1.0
VIDEO_POLL_SEC = 10              # 进度轮询间隔
VIDEO_STALL_TIMEOUT_SEC = 120    # 多少秒进度不动判定为卡住
READ_MIN_SECONDS = 45            # 阅读/文档任务点：最低真实停留秒数（覆盖自动估算下限）
READ_MAX_SECONDS = 240           # 阅读/文档任务点：停留上限秒数
READ_CHARS_PER_SEC = 12          # 阅读自动估时速度：正文每多少字算 1 秒（need=字数/该值，再夹到上下限）
MAX_ITEMS_PER_RUN = 200          # 单次运行最多处理多少个学习项，防死循环
MAX_RETRY_PER_ITEM = 3           # 单个学习项失败重试次数
ACTION_MIN_INTERVAL_SEC = 1.2    # 相邻页面操作最小间隔，避免高频请求给服务器压力
PAGE_LOAD_TIMEOUT_MS = 60000

# 随堂弹题：出现时把题目与 AI 解析打印到终端（只读显示；绝不自动点选/提交/跳过）
POPUP_DETECT = True
POPUP_AI = True
POPUP_MIN_INTERVAL_SEC = 2.0   # 弹题探测间隔（秒）；调小更容易抓到转瞬即逝的随堂题

# 提交控制：永远由用户本人点击“提交答案”
AUTO_SUBMIT = False              # 保留字段：程序强制视为 False，不可打开

# ==================== 三、AI 服务（OpenAI 兼容接口） ====================
# 支持任意 OpenAI 兼容服务：OpenAI / DeepSeek / 豆包方舟 / Kimi / 智谱 / 通义 / Ollama 等
AI_BASE_URL = "https://api.openai.com/v1"
AI_API_KEY = ""                  # 留空则读取环境变量 OPENAI_API_KEY / AI_API_KEY
AI_MODEL = "gpt-4o-mini"
AI_TEMPERATURE = 0.2
AI_MAX_TOKENS = 2048
AI_TIMEOUT_SEC = 90
AI_MAX_RETRY = 3
AI_RETRY_BACKOFF = 2.0
AI_ENABLE = True                 # 设为 False 时全部功能降级为“只做检索、不做 AI 分析”

# ==================== 四、知识库 ====================
KB_DOWNLOAD_DIR = "data/downloads"      # 课程文档/PPT/PDF 下载目录
KB_MAX_DOC_MB = 60                       # 单文件超过此大小跳过解析
KB_CHUNK_SIZE = 900                      # 检索分块长度（字符）
KB_TOP_K = 6                             # 默认返回条数
KB_BUILD_ON_STUDY = True                 # 学习过程中顺手把文档资料入库

# ==================== 五、开卷考试 AI 辅助（三重开关，缺一不可） ====================
# 1) 只有当你确认“本课程期末考试明确允许开卷 + 允许 AI 辅助”时才改为 True
EXAM_MODE_ALLOWED = False
# 2) 考试规定原文（可选，但强烈建议填写）：程序会在其中检测“禁止AI/闭卷”等字样，
#    一旦检测到禁止性表述，自动关闭考试 AI 模式
EXAM_RULE_TEXT = ""
# 3) 每次启动考试模式还需在终端输入课程名 + “我已阅读并遵守考试规定”进行二次确认
EXAM_ACK_PHRASE = "我已阅读并遵守考试规定"
# 侧边栏模式：tkinter（独立小窗，不改动学习通页面）/ console（纯终端）
EXAM_UI = "tkinter"
EXAM_ALLOW_SCROLL = True        # 考试 AI 模式仅允许“翻页滚动”；练习/演示写操作见下方 PRACTICE_MODE
EXAM_AI_MAX_PER_MIN = 6         # 考试期间 AI 调用频率上限，避免异常请求

# ==================== 五-B、练习/演示模式（本产品不涉及真实监考考试） ====================
# 用途：允许程序对“你自己的练习/演示页面”执行作答类写操作（勾选/填写/点击提交）。
# 安全红线：即便 PRACTICE_MODE=True，只要目标 URL 命中 REAL_EXAM_DOMAINS 里的真实
# 考试/学习通域名，写操作仍被硬性拒绝（判定见 exam/guard.py::allow_page_write）。
PRACTICE_MODE = False           # 总开关：允许对非真实考试页面执行作答类写操作
PRACTICE_ALLOW_SUBMIT = False   # 额外开关：是否允许真正点击“提交答案”（默认关，防误交）
PRACTICE_FORCE_ANSWER = False   # 第三个开关：即使 AI 标注“需要人工确认”也按 AI 答案硬填（默认关=安全）
PRACTICE_RETRY_ON_WRONG = False # 第四个开关：视频随堂题答错时，自动换下一个没试过的选项重交直到答对
PRACTICE_SUBMIT_ACTION = "submit"  # 答完后点什么：submit=「提交/交卷」，save=只「暂时保存」不交卷
PRACTICE_SKIP_ANSWERED = True   # 自动作答时跳过“已作答”的题（不重做、不取消已勾选）；关闭则逐题重填
PRACTICE_ALLOW_DISCUSS_POST = False  # 讨论总开关：仅当“成绩里讨论计分”且本开关=True 才自动发帖/回复（默认关，公开且不可逆）
PRACTICE_ALLOW_DISCUSS_SUBMIT = False  # 讨论自动提交：填入后由程序点“发表/回复”（默认关；关着=只填不点、你亲自发布）
DISCUSS_MAX = 5                  # 每轮最多处理几条讨论动作（防刷屏）；发帖/回复条数由成绩里的讨论规则（如发帖+1/回复+2/满分100）凑分自动规划
DISCUSS_AI_PARALLEL = 4          # 生成讨论草稿的 AI 并行路数（1=顺序最稳，4≈快3倍；遇 429 限频就调小）
REAL_EXAM_DOMAINS = [           # 真实学习通/超星主域名（其子域名一并纳入“按路径判定”的锁）
    "chaoxing.com", "xuexitong.com", "xxt365.com", "cx.cn",
]
# 在上面的域名上采用“只锁考试”的黑名单：URL 命中以下片段视为考试/监考页 → 一律只读；
# 其余（视频小节 / 作业 / 练习）在 PRACTICE_MODE 下允许自动作答。非这些域名的页面不受限。
EXAM_URL_PATTERNS = [           # 锁死写操作的“考试/监考”URL 片段（小写子串匹配）
    "/exam", "examtest", "exam-ans", "examans", "testpaper", "doexam", "exam_answer",
    "试考", "监考", "kaoshi", "chaptersummaryexam",
]

# ==================== 六、日志与数据 ====================
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
DB_PATH = "data/study_helper.db"
EXPORT_DIR = "data/exports"