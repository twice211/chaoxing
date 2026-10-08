export type Value = string | number | boolean;
export interface SettingField {
  key: string;
  kind: "bool" | "int" | "float" | "str" | "secret" | "text";
  label: string;
  group: string;
}
export interface Section {
  id: number;
  title: string;
  kind: string;
  progress: number;
  done: boolean;
  chapter_id: number | null;
}
export interface Snapshot {
  ready: boolean;
  busy: boolean;
  login_pending: boolean;
  login_state: "unknown" | "waiting" | "signed_in" | "signed_out";
  initialization_error: string;
  course_id: number | null;
  courses: { id: number; name: string }[];
  sections: Section[];
  stats: { total: number; finished: number };
  ai: {
    configured: boolean;
    enabled: boolean;
    model: string;
    base_url: string;
  };
  settings: {
    values: Record<string, Value>;
    fields: SettingField[];
    groups: string[];
  };
}
export interface BatchConfirmation {
  token: number;
  text: string;
  kind?: string;
}
export interface BackendEvent {
  level: string;
  payload: string | Record<string, unknown>;
  sequence?: number;
}
export type Action =
  | "login"
  | "logout"
  | "courses"
  | "select_course"
  | "catalog"
  | "open_item"
  | "play"
  | "pause"
  | "resume"
  | "stop_play"
  | "cancel"
  | "auto_next"
  | "task_tab"
  | "parse"
  | "auto"
  | "auto_chain"
  | "stop"
  | "grades"
  | "discuss_preview"
  | "discuss_fill_next"
  | "discuss_auto"
  | "discuss_auto_confirm"
  | "discuss_confirm"
  | "discuss_discard"
  | "discuss_not_published"
  | "discuss_set_max"
  | "read"
  | "dump"
  | "wrong_list"
  | "search"
  | "ai_check"
  | "ai_test"
  | "save_settings";
export type Result<T> = { ok: true; data: T } | { ok: false; error: string };
export interface DesktopAPI {
  bootstrap(): Promise<Result<Snapshot>>;
  poll(): Promise<Result<{ events: BackendEvent[]; snapshot: Snapshot }>>;
  command(
    action: Action,
    params: Record<string, unknown>,
  ): Promise<{ ok: true } | { ok: false; error: string }>;
}
declare global {
  interface Window {
    pywebview?: { api: DesktopAPI };
  }
}
export type RunCommand = (
  action: Action,
  params?: Record<string, unknown>,
) => Promise<boolean>;
