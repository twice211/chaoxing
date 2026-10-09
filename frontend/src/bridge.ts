import { useCallback, useEffect, useRef, useState } from "react";
import { appendEvents, eventText } from "./state";
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
  const [eventError, setEventError] = useState<BackendEvent | null>(null);
  const [offline, setOffline] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [confirmation, setConfirmation] = useState<BatchConfirmation | null>(
    null,
  );
  const mounted = useRef(true);
  const currentCourse = useRef<number | null>(null);
  useEffect(() => {
    mounted.current = true;
    const session = createPollingSession(() => window.pywebview?.api, {
      onSnapshot: (next) => {
        if (currentCourse.current !== next.course_id) setConfirmation(null);
        currentCourse.current = next.course_id;
        setSnapshot(next);
      },
      onError: setError,
      onOffline: setOffline,
      onConnected: () => setError(""),
      onEvents: (incoming) => {
        setEvents((previous) => appendEvents(previous, incoming));
        for (const event of incoming) {
          if (
            event.level === "discuss_confirm" &&
            event.course_id != null &&
            event.course_id === currentCourse.current &&
            typeof event.payload !== "string"
          ) {
            const { token, text, kind } = event.payload;
            if (typeof token === "number" && typeof text === "string")
              setConfirmation({
                token,
                text,
                course_id: event.course_id,
                ...(typeof kind === "string" ? { kind } : {}),
              });
          }
          if (
            event.level === "err" &&
            (event.course_id == null || event.course_id === currentCourse.current)
          )
            setEventError(event);
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
  const run: RunCommand = useCallback(async (action: Action, params = {}, onAccepted) => {
    const api = window.pywebview?.api;
    if (!api) {
      setError("请在桌面应用中使用此功能。");
      return false;
    }
    setDispatching(true);
    try {
      const result = await api.command(action, params);
      if (!result.ok) throw new Error(result.error);
      if (mounted.current) onAccepted?.(result.request_id);
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
    error: error || (
      eventError && (eventError.course_id == null || eventError.course_id === snapshot?.course_id)
        ? eventText(eventError)
        : ""
    ),
    offline,
    dispatching,
    confirmation,
    run,
    clearError: () => {
      setError("");
      setEventError(null);
    },
    setConfirmation,
  };
}
