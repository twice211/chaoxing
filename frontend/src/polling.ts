import type { BackendEvent, DesktopAPI, Snapshot } from "./types.ts";

interface PollingCallbacks {
  onSnapshot: (snapshot: Snapshot) => void;
  onEvents: (events: BackendEvent[]) => void;
  onError: (message: string) => void;
  onOffline: (offline: boolean) => void;
  onConnected: () => void;
}

export function createPollingSession(
  getAPI: () => Pick<DesktopAPI, "bootstrap" | "poll"> | undefined,
  callbacks: PollingCallbacks,
) {
  let stopped = false;
  let connected = false;
  let inFlight = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function schedule(operation: () => Promise<void>, delay: number) {
    if (stopped) return;
    clearTimeout(timer);
    timer = setTimeout(() => void operation(), delay);
  }
  async function poll() {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const api = getAPI();
      if (!api) throw new Error("桌面连接已断开，请重新打开应用。");
      const result = await api.poll();
      if (!result.ok) throw new Error(result.error);
      if (!stopped) {
        callbacks.onSnapshot(result.data.snapshot);
        callbacks.onEvents(result.data.events);
      }
    } catch (err) {
      if (!stopped)
        callbacks.onError(
          err instanceof Error ? err.message : "获取运行状态失败",
        );
    } finally {
      inFlight = false;
      schedule(poll, 500);
    }
  }
  async function start() {
    if (stopped || connected || inFlight) return;
    clearTimeout(timer);
    const api = getAPI();
    if (!api) {
      callbacks.onOffline(true);
      schedule(start, 500);
      return;
    }
    inFlight = true;
    callbacks.onOffline(false);
    try {
      const result = await api.bootstrap();
      if (!result.ok) throw new Error(result.error);
      if (stopped) return;
      connected = true;
      callbacks.onSnapshot(result.data);
      callbacks.onConnected();
      schedule(poll, 500);
    } catch (err) {
      if (!stopped)
        callbacks.onError(
          err instanceof Error ? err.message : "无法连接桌面服务",
        );
      schedule(start, 1500);
    } finally {
      inFlight = false;
    }
  }
  return {
    start,
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
  };
}
