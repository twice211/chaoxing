import { useEffect, useRef, useState } from "react";
import { Button, Empty, Panel } from "../components";
import { eventText, resultEvent } from "../state";
import type { BackendEvent, RunCommand } from "../types";
export function Grades({
  events,
  run,
  disabled,
  hasCourse,
}: {
  events: BackendEvent[];
  run: RunCommand;
  disabled: boolean;
  hasCourse: boolean;
}) {
  const result = [...events]
    .reverse()
    .find((event) => event.level === "grades");
  return (
    <Panel
      title="课程成绩"
      description="从平台只读获取成绩，也可以查看上一次保存在本地的结果。"
    >
      <div className="button-row">
        <Button
          tone="primary"
          icon="refresh"
          disabled={disabled || !hasCourse}
          onClick={() => void run("grades", { save: true })}
        >
          读取最新成绩
        </Button>
        <Button
          disabled={disabled || !hasCourse}
          onClick={() => void run("grades", { save: false })}
        >
          查看本地成绩
        </Button>
      </div>
      {result ? (
        <pre className="result-text">{eventText(result)}</pre>
      ) : (
        <Empty icon="grades" title="还没有成绩结果">
          选择课程，然后读取最新成绩或查看本地记录。
        </Empty>
      )}
    </Panel>
  );
}
export function Search({
  events,
  courseId,
  run,
  disabled,
}: {
  events: BackendEvent[];
  courseId: number | null;
  run: RunCommand;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const request = useResultRequest(events, "search_results", courseId, run);
  async function search() {
    if (!query.trim()) {
      request.setError("请输入要搜索的内容。");
      return;
    }
    await request.begin("search", { text: query.trim() });
  }
  return (
    <Panel title="资料搜索" description="检索当前课程的本地知识库。">
      <form
        className="search-form"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <input
          aria-label="搜索关键词"
          placeholder="输入概念、题目或关键词"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button
          type="submit"
          tone="primary"
          icon="search"
          disabled={disabled || request.loading}
        >
          {request.loading ? "正在搜索…" : "搜索资料"}
        </Button>
      </form>
      {request.error && (
        <p className="form-error" role="alert">
          {request.error}
        </p>
      )}
      {request.notice && (
        <p className="quiet-note" role="status">
          {request.notice}
        </p>
      )}
      {request.loading && (
        <p className="quiet-note" role="status">
          正在检索课程资料，已有结果会保留到本次搜索完成。
        </p>
      )}
      {request.result ? (
        <pre className="result-text">{request.result}</pre>
      ) : (
        <Empty
          icon="search"
          title={
            request.loading
              ? "等待搜索结果"
              : request.result === ""
                ? "没有匹配的资料"
                : "从一个关键词开始"
          }
        >
          {request.result === ""
            ? "尝试其他关键词，或检查当前课程是否已有入库资料。"
            : "已入库的课程资料将作为搜索来源；操作结果也会显示在运行记录中。"}
        </Empty>
      )}
    </Panel>
  );
}
export function WrongQuestions({
  events,
  courseId,
  run,
  disabled,
}: {
  events: BackendEvent[];
  courseId: number | null;
  run: RunCommand;
  disabled: boolean;
}) {
  const request = useResultRequest(events, "wrong_results", courseId, run);
  return (
    <Panel
      title="错题回顾"
      description="查看当前课程保存的错题、参考答案和错误次数。"
      action={
        <Button
          icon="refresh"
          disabled={disabled || request.loading}
          onClick={() => void request.begin("wrong_list")}
        >
          {request.loading ? "正在读取…" : "读取错题"}
        </Button>
      }
    >
      {request.error && (
        <p className="form-error" role="alert">
          {request.error}
        </p>
      )}
      {request.notice && (
        <p className="quiet-note" role="status">
          {request.notice}
        </p>
      )}
      {request.loading && (
        <p className="quiet-note" role="status">
          正在读取本地错题，已有记录将保留。
        </p>
      )}
      {request.result ? (
        <pre className="result-text">{request.result}</pre>
      ) : (
        <Empty
          icon="wrong"
          title={
            request.loading
              ? "等待读取结果"
              : request.result === ""
                ? "当前课程还没有错题"
                : "先读取本地错题"
          }
        >
          {request.result === ""
            ? "学习过程中保存的错题将在这里显示。"
            : "学习过程中保存的错题会汇集在这里，方便再次检查。"}
        </Empty>
      )}
    </Panel>
  );
}

function useResultRequest(
  events: BackendEvent[],
  level: "search_results" | "wrong_results",
  courseId: number | null,
  run: RunCommand,
) {
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (!requestId) return;
    const latest = resultEvent(events, level, { courseId, requestId });
    if (latest) {
      setResult(eventText(latest));
      pending.current = false;
      setLoading(false);
      setError("");
      setNotice("");
      setRequestId(null);
      return;
    }
    const terminal = events.find(
      (event) =>
        event.level === "request_finished" && event.course_id === courseId &&
        event.request_id === requestId && typeof event.payload !== "string" &&
        event.payload.request_id === requestId &&
        event.payload.action === (level === "search_results" ? "search" : "wrong_list"),
    );
    if (!terminal || typeof terminal.payload === "string") return;
    pending.current = false;
    setRequestId(null);
    setLoading(false);
    if (terminal.payload.status === "cancelled")
      setNotice("任务已取消，已保留上次结果。");
    else if (terminal.payload.status !== "succeeded")
      setError(eventText(terminal) || "操作未完成，请查看运行记录。上次结果已保留。");
  }, [events, level, courseId, requestId]);
  async function begin(
    action: "search" | "wrong_list",
    params: Record<string, unknown> = {},
  ) {
    if (pending.current) return;
    pending.current = true;
    setRequestId(null);
    setLoading(true);
    setError("");
    setNotice("");
    const accepted = await run(action, params, (id) => {
      if (mounted.current) setRequestId(id);
    });
    if (!mounted.current) return;
    if (!accepted) {
      pending.current = false;
      setLoading(false);
      setError("请求未被接受，请查看错误提示后重试。上次结果已保留。");
    }
  }
  return { result, loading, error, notice, setError, begin };
}
