import { useState } from "react";
import { useDesktop } from "./bridge";
import { Button, Empty, Icon, Modal } from "./components";
import { eventText, progressPercent } from "./state";
import { Study } from "./pages/Study";
import { Exercises } from "./pages/Exercises";
import { Discussions } from "./pages/Discussions";
import { Grades, Search, WrongQuestions } from "./pages/Results";
import { Settings } from "./pages/Settings";

const navigation = [
  {
    id: "study",
    label: "课程学习",
    icon: "study",
    description: "将学习落在每一个小节。",
  },
  {
    id: "exercise",
    label: "章节练习",
    icon: "exercise",
    description: "解析、检查，再继续下一节。",
  },
  {
    id: "grades",
    label: "课程成绩",
    icon: "grades",
    description: "查看课程考核与已保存的成绩。",
  },
  {
    id: "discuss",
    label: "课程讨论",
    icon: "discuss",
    description: "先核对内容，再确认发布。",
  },
  {
    id: "search",
    label: "资料搜索",
    icon: "search",
    description: "从课程资料中寻找答案。",
  },
  {
    id: "wrong",
    label: "错题回顾",
    icon: "wrong",
    description: "记录问题，把薄弱处弄清楚。",
  },
  {
    id: "settings",
    label: "设置",
    icon: "settings",
    description: "让工作台按你的方式运行。",
  },
] as const;
type Page = (typeof navigation)[number]["id"];

export function App() {
  const desktop = useDesktop();
  const { snapshot, events, run } = desktop;
  const [page, setPage] = useState<Page>("study");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [logOpen, setLogOpen] = useState(true);
  const nav = navigation.find((item) => item.id === page) ?? navigation[0];
  const selected = snapshot?.sections.find((item) => item.id === selectedId);
  const course = snapshot?.courses.find(
    (item) => item.id === snapshot.course_id,
  );
  const disabled =
    !snapshot?.ready ||
    Boolean(snapshot?.busy) ||
    snapshot?.login_pending === true ||
    Boolean(snapshot?.initialization_error) ||
    desktop.dispatching;
  const catalogVisible = page === "study" || page === "exercise";
  const sections =
    snapshot?.sections.filter((section) =>
      section.title.toLowerCase().includes(catalogSearch.toLowerCase()),
    ) ?? [];
  const logs = events.filter(
    (event) =>
      ![
        "courses",
        "course",
        "sections",
        "row",
        "item",
        "progress",
        "focus",
        "discuss_confirm",
      ].includes(event.level),
  );
  const batch = desktop.confirmation;
  async function respond(approved: boolean) {
    if (!batch) return;
    if (await run("discuss_auto_confirm", { token: batch.token, approved }))
      desktop.setConfirmation(null);
  }
  return (
    <div className="app-shell">
      <aside className="navigation">
        <div className="brand">
          <div className="brand-symbol">
            <Icon name="study" size={25} />
          </div>
          <div>
            <strong>学习工作台</strong>
            <span>学习通助手</span>
          </div>
        </div>
        <nav aria-label="主导航">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "selected" : ""}`}
              aria-current={page === item.id ? "page" : undefined}
              aria-label={item.label}
              onClick={() => setPage(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {page === item.id && <span className="nav-marker" />}
            </button>
          ))}
        </nav>
        <div className="navigation-footer">
          <div className="connection-line">
            <span
              className={`status-dot ${snapshot?.ready && !snapshot.initialization_error ? "green" : ""}`}
            />
            <span>
              {desktop.offline
                ? "浏览器预览"
                : snapshot?.initialization_error
                  ? "初始化失败"
                  : snapshot?.ready
                    ? "本地服务已连接"
                    : "正在连接桌面"}
            </span>
          </div>
          <p>
            课程与学习记录
            <br />
            保存在本地工作区
          </p>
        </div>
      </aside>
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            学习工作台 <span>/</span> <strong>{nav.label}</strong>
          </div>
          <div className="topbar-actions">
            <span
              className={`task-status ${snapshot?.busy || snapshot?.login_pending ? "running" : ""}`}
            >
              {snapshot?.login_pending
                ? "等待登录"
                : snapshot?.busy
                  ? "任务运行中"
                  : desktop.offline
                    ? "界面预览"
                    : snapshot?.ready
                      ? "就绪"
                      : "正在连接"}
            </span>
            <Button
              tone="quiet"
              disabled={!snapshot?.ready}
              onClick={() => void run("cancel")}
            >
              取消当前任务
            </Button>
            <Button
              icon="login"
              disabled={disabled}
              onClick={() => void run("login")}
            >
              登录
            </Button>
          </div>
        </header>
        <div className="main-content">
          <div className="page-heading">
            <div>
              <h1>{nav.label}</h1>
              <p>{nav.description}</p>
            </div>
            <div className="context-note">
              <span>{course?.name ?? "尚未选择课程"}</span>
              <span>
                {snapshot?.stats.total
                  ? `已完成 ${snapshot.stats.finished} / ${snapshot.stats.total} 小节`
                  : "从一门课程开始"}
              </span>
            </div>
          </div>
          {desktop.offline && (
            <div className="inline-notice">
              <strong>请在桌面应用中使用</strong>
              <span>
                当前是界面预览。启动 Python 桌面入口后，可连接本地课程与浏览器。
              </span>
            </div>
          )}
          {(desktop.error || snapshot?.initialization_error) && (
            <div className="error-banner" role="alert">
              <span>{desktop.error || snapshot?.initialization_error}</span>
              <button
                className="icon-button"
                aria-label="收起错误提示"
                onClick={desktop.clearError}
              >
                <Icon name="close" size={18} />
              </button>
            </div>
          )}
          <div className="course-toolbar">
            <label htmlFor="course-select">当前课程</label>
            <select
              id="course-select"
              value={snapshot?.course_id ?? ""}
              disabled={disabled}
              onChange={(event) => {
                const id = Number(event.target.value);
                if (id) {
                  setSelectedId(null);
                  void run("select_course", { course_id: id });
                }
              }}
            >
              <option value="" disabled>
                请选择课程
              </option>
              {snapshot?.courses.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <Button
              icon="refresh"
              disabled={disabled}
              onClick={() => void run("courses")}
            >
              读取课程
            </Button>
            {catalogVisible && (
              <Button
                disabled={disabled || !snapshot?.course_id}
                onClick={() => void run("catalog")}
              >
                读取目录
              </Button>
            )}
          </div>
          <div className={`workspace ${catalogVisible ? "with-catalog" : ""}`}>
            {catalogVisible && (
              <aside className="catalog panel">
                <div className="panel-head">
                  <div>
                    <h2>课程目录</h2>
                    <p>{snapshot?.sections.length ?? 0} 个小节</p>
                  </div>
                  <Icon name="study" size={19} />
                </div>
                <div className="catalog-filter">
                  <Icon name="search" size={16} />
                  <input
                    aria-label="筛选小节"
                    placeholder="查找小节"
                    value={catalogSearch}
                    onChange={(event) => setCatalogSearch(event.target.value)}
                  />
                </div>
                <div className="section-list">
                  {sections.length ? (
                    sections.map((item, index) => (
                      <button
                        className={`section-row ${selected?.id === item.id ? "selected" : ""}`}
                        aria-pressed={selected?.id === item.id}
                        key={item.id}
                        disabled={disabled}
                        onClick={() => {
                          setSelectedId(item.id);
                          void run("open_item", { item_id: item.id });
                        }}
                      >
                        <span
                          className={`section-number ${item.done ? "completed" : ""}`}
                        >
                          {item.done ? (
                            <Icon name="check" size={15} />
                          ) : (
                            String(index + 1).padStart(2, "0")
                          )}
                        </span>
                        <span className="section-details">
                          <strong>{item.title}</strong>
                          <small>
                            {item.kind}
                            <span>
                              {item.done
                                ? "已完成"
                                : item.progress > 0
                                  ? `${Math.round(progressPercent(item.progress))}%`
                                  : "未开始"}
                            </span>
                          </small>
                        </span>
                        <Icon name="arrow" size={14} />
                      </button>
                    ))
                  ) : (
                    <Empty
                      title={catalogSearch ? "没有匹配的小节" : "目录还未读取"}
                    >
                      {catalogSearch
                        ? "尝试其他标题关键词。"
                        : "选择课程后点击「读取目录」。"}
                    </Empty>
                  )}
                </div>
                <div className="catalog-footer">
                  <span className="status-dot green" />
                  学习进度自动保存在本地
                </div>
              </aside>
            )}
            <div className="workspace-pages">
              <div hidden={page !== "study"}>
                <Study
                  snapshot={snapshot}
                  selected={selected}
                  run={run}
                  disabled={disabled}
                  events={events}
                />
              </div>
              <div hidden={page !== "exercise"}>
                <Exercises
                  snapshot={snapshot}
                  selected={selected}
                  run={run}
                  disabled={disabled}
                />
              </div>
              <div hidden={page !== "grades"}>
                <Grades
                  events={events}
                  run={run}
                  disabled={disabled}
                  hasCourse={Boolean(snapshot?.course_id)}
                />
              </div>
              <div hidden={page !== "discuss"}>
                <Discussions
                  snapshot={snapshot}
                  events={events}
                  run={run}
                  disabled={disabled}
                />
              </div>
              <div hidden={page !== "search"}>
                <Search events={events} run={run} disabled={disabled} />
              </div>
              <div hidden={page !== "wrong"}>
                <WrongQuestions events={events} run={run} disabled={disabled} />
              </div>
              <div hidden={page !== "settings"}>
                <Settings
                  snapshot={snapshot}
                  events={events}
                  run={run}
                  disabled={disabled}
                />
              </div>
            </div>
          </div>
          <section className="activity panel">
            <button
              className="activity-heading"
              aria-expanded={logOpen}
              onClick={() => setLogOpen(!logOpen)}
            >
              <span>
                <span className="activity-indicator" />
                运行记录
              </span>
              <span className="muted">
                {logOpen ? "收起" : "展开"} <Icon name="arrow" size={14} />
              </span>
            </button>
            {logOpen && (
              <div className="activity-log" role="log" aria-label="运行记录">
                {logs.length ? (
                  logs.slice(-60).map((event, index) => (
                    <div
                      className={`log-row ${event.level}`}
                      key={event.sequence ?? index}
                    >
                      <span className="log-label">
                        {(
                          {
                            err: "错误",
                            warn: "提示",
                            ok: "完成",
                            info: "信息",
                            raw: "结果",
                            playstatus: "播放",
                          } as Record<string, string>
                        )[event.level] ?? "记录"}
                      </span>
                      <span>{eventText(event)}</span>
                    </div>
                  ))
                ) : (
                  <p className="muted">
                    操作反馈将显示在这里。登录并读取课程，开始本次学习。
                  </p>
                )}
              </div>
            )}
          </section>
        </div>
      </main>
      {batch && (
        <Modal
          title="确认本轮讨论发布"
          onClose={() => void respond(false)}
          footer={
            <>
              <Button
                disabled={desktop.dispatching}
                onClick={() => void respond(false)}
              >
                取消本轮
              </Button>
              <Button
                tone="primary"
                disabled={desktop.dispatching}
                onClick={() => void respond(true)}
              >
                确认发布本轮
              </Button>
            </>
          }
        >
          <p className="quiet-note">
            请核对以下完整草稿。确认后将按已保存的讨论设置执行本轮操作。
          </p>
          <pre className="confirmation-text">{batch.text}</pre>
        </Modal>
      )}
    </div>
  );
}
