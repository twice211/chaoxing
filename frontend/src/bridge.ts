import { useCallback, useEffect, useRef, useState } from "react";
import { appendEvents } from "./state";
import { createPollingSession } from "./polling";
import type {
  Action,
  BackendEvent,
  BatchConfirmation,
  RunCommand,
  Snapshot,
} from "./types";

export function useDesktop() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<BackendEvent[]>([]);
  const [error, setError] = useState("");
  const [offline, setOffline] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [confirmation, setConfirmation] = useState<BatchConfirmation | null>(
    null,
  );
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const session = createPollingSession(() => window.pywebview?.api, {
      onSnapshot: setSnapshot,
      onError: setError,
      onOffline: setOffline,
      onConnected: () => setError(""),
      onEvents: (incoming) => {
        setEvents((previous) => appendEvents(previous, incoming));
        for (const event of incoming) {
          if (
            event.level === "discuss_confirm" &&
            typeof event.payload !== "string"
          ) {
            const { token, text, kind } = event.payload;
            if (typeof token === "number" && typeof text === "string")
              setConfirmation({
                token,
                text,
                ...(typeof kind === "string" ? { kind } : {}),
              });
          }
          if (event.level === "err" || event.level === "alert")
            setError(
              typeof event.payload === "string"
                ? event.payload
                : "操作未完成，请查看运行记录。",
            );
        }
      },
    });
    const connect = () => void session.start();
    connect();
    window.addEventListener("pywebviewready", connect);
    return () => {
      mounted.current = false;
      session.stop();
      window.removeEventListener("pywebviewready", connect);
    };
  }, []);
  const run: RunCommand = useCallback(async (action: Action, params = {}) => {
    const api = window.pywebview?.api;
    if (!api) {
      setError("请在桌面应用中使用此功能。");
      return false;
    }
    setDispatching(true);
    try {
      const result = await api.command(action, params);
      if (!result.ok) throw new Error(result.error);
      if (action === "cancel" && mounted.current)
        setEvents((previous) =>
          appendEvents(previous, [
            { level: "request_cancelled", payload: "已请求取消当前任务。" },
          ]),
        );
      return true;
    } catch (err) {
      if (mounted.current)
        setError(err instanceof Error ? err.message : "发送操作失败");
      return false;
    } finally {
      if (mounted.current) setDispatching(false);
    }
  }, []);
  return {
    snapshot,
    events,
    error,
    offline,
    dispatching,
    confirmation,
    run,
    clearError: () => setError(""),
    setConfirmation,
  };
}
