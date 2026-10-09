import { useEffect, useRef, useState } from "react";
import { Button, Empty, Modal, Panel, Toggle } from "../components";
import {
  loginStatusLabel,
  settingsPayload,
  mergeSettingsValues,
  remainingDirtyFields,
} from "../state";
import type { BackendEvent, RunCommand, Snapshot, Value } from "../types";
export function Settings({
  snapshot,
  events,
  run,
  disabled,
}: {
  snapshot: Snapshot | null;
  events: BackendEvent[];
  run: RunCommand;
  disabled: boolean;
}) {
  const [values, setValues] = useState<Record<string, Value>>({});
  const [status, setStatus] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [logout, setLogout] = useState(false);
  const [saving, setSaving] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);
  const dirty = useRef(new Set<string>());
  const versions = useRef<Record<string, number>>({});
  const pending = useRef<{
    versions: Record<string, number>;
    clear: boolean;
  } | null>(null);
  useEffect(() => {
    if (!snapshot) return;
    setValues((previous) =>
      mergeSettingsValues(
        previous,
        snapshot.settings.values,
        snapshot.settings.fields,
        dirty.current,
      ),
    );
  }, [snapshot]);
  useEffect(() => {
    const request = pending.current;
    if (!request || !snapshot || !requestId) return;
    const result = events.find(
      (event) =>
        event.level === "request_finished" &&
        typeof event.payload !== "string" &&
        event.payload.request_id === requestId &&
        event.payload.action === "save_settings",
    );
    if (!result || typeof result.payload === "string") return;
    pending.current = null;
    setRequestId(null);
    setSaving(false);
    if (result.payload.status !== "succeeded") {
      setStatus("设置保存未完成，请检查运行记录并重试。");
      return;
    }
    dirty.current = remainingDirtyFields(
      dirty.current,
      request.versions,
      versions.current,
    );
    setStatus(
      request.clear
        ? "API 密钥已清除。"
        : dirty.current.size
          ? "已提交的设置已保存，仍有新的修改待保存。"
          : "设置已保存。",
    );
    setClearKey(false);
    setValues((previous) => {
      const next = { ...previous };
      for (const field of snapshot.settings.fields)
        if (
          field.kind === "secret" &&
          request.versions[field.key] !== undefined &&
          request.versions[field.key] === (versions.current[field.key] ?? 0)
        )
          next[field.key] = "";
      return mergeSettingsValues(
        next,
        snapshot.settings.values,
        snapshot.settings.fields,
        dirty.current,
      );
    });
  }, [events, snapshot, requestId]);
  async function persist(payload: Record<string, Value>, clear = false) {
    if (pending.current || !snapshot) return;
    const submittedKeys = clear
      ? snapshot.settings.fields
          .filter((field) => field.kind === "secret")
          .map((field) => field.key)
      : Object.keys(payload);
    pending.current = {
      versions: Object.fromEntries(
        submittedKeys.map((key) => [key, versions.current[key] ?? 0]),
      ),
      clear,
    };
    setSaving(true);
    setStatus(clear ? "正在清除密钥…" : "正在保存设置…");
    if (
      !(await run(
        "save_settings",
        { values: payload, ...(clear ? { clear_api_key: true } : {}) },
        setRequestId,
      ))
    ) {
      pending.current = null;
      setRequestId(null);
      setSaving(false);
      setStatus("设置保存未完成，请检查错误提示。");
    }
  }
  async function save() {
    if (!snapshot) return;
    if (!dirty.current.size) {
      setStatus("没有需要保存的修改。");
      return;
    }
    try {
      const payload = settingsPayload(
        snapshot.settings.fields.filter((field) =>
          dirty.current.has(field.key),
        ),
        values,
      );
      await persist(payload);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "请检查设置内容。");
    }
  }
  function edit(key: string, value: Value) {
    dirty.current.add(key);
    versions.current[key] = (versions.current[key] ?? 0) + 1;
    setValues((previous) => ({ ...previous, [key]: value }));
    setStatus(
      saving
        ? "正在保存；新修改将保留，完成后可再次保存。"
        : "有未保存的修改。",
    );
  }
  if (!snapshot)
    return (
      <Panel title="设置">
        <Empty icon="settings" title="等待桌面连接">
          配置会从本地桌面服务读取。
        </Empty>
      </Panel>
    );
  const groups = [
    ...new Set([
      ...snapshot.settings.groups,
      ...snapshot.settings.fields.map((field) => field.group),
    ]),
  ];
  return (
    <div className="page-stack">
      <Panel
        title="账户与 AI"
        description="登录会打开关联浏览器；密钥仅保存在本地配置中。"
      >
        <div className="account-row">
          <div>
            <strong>
              {loginStatusLabel(snapshot.login_state)}
            </strong>
            <p className="muted">
              退出登录会清理本地课程记录，请先确认当前任务已停止。
            </p>
          </div>
          <div className="button-row">
            <Button
              tone="primary"
              icon="login"
              disabled={disabled}
              onClick={() => void run("login")}
            >
              {snapshot.login_state === "signed_in" ? "打开学习通" : "登录"}
            </Button>
            {snapshot.login_pending && (
              <Button onClick={() => void run("cancel")}>取消登录</Button>
            )}
            <Button disabled={disabled} onClick={() => setLogout(true)}>
              退出登录
            </Button>
          </div>
        </div>
        <div className="ai-summary">
          <span
            className={`status-dot ${snapshot.ai.configured ? "green" : ""}`}
          />
          <span>
            {snapshot.ai.configured ? "已配置 API 密钥" : "尚未配置 API 密钥"}
          </span>
          <span className="muted">
            {snapshot.ai.enabled ? "AI 已启用" : "AI 未启用"}
          </span>
        </div>
        <div className="button-row">
          <Button disabled={disabled} onClick={() => void run("ai_check")}>
            检查 AI 配置
          </Button>
          <Button
            disabled={disabled || !snapshot.ai.configured}
            onClick={() => void run("ai_test")}
          >
            测试已保存的连接
          </Button>
          <Button
            tone="quiet"
            disabled={disabled || !snapshot.ai.configured}
            onClick={() => setClearKey(true)}
          >
            清除 API 密钥
          </Button>
        </div>
        <p className="quiet-note">
          连接测试会使用已保存的配置，可能产生少量 API
          用量。修改配置后请先保存。
        </p>
      </Panel>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
        className="settings-form"
      >
        {groups.map((group) => (
          <Panel key={group} title={group}>
            <div className="settings-grid">
              {snapshot.settings.fields
                .filter((field) => field.group === group)
                .map((field) =>
                  field.kind === "bool" ? (
                    <div className="setting-toggle" key={field.key}>
                      <Toggle
                        label={field.label}
                        checked={Boolean(values[field.key])}
                        onChange={(value) => edit(field.key, value)}
                      />
                      <span className="setting-key">{field.key}</span>
                    </div>
                  ) : (
                    <label
                      className={`field ${field.kind === "text" ? "full-width" : ""}`}
                      key={field.key}
                    >
                      <span>{field.label}</span>
                      {field.kind === "text" ? (
                        <textarea
                          rows={4}
                          value={String(values[field.key] ?? "")}
                          onChange={(e) => edit(field.key, e.target.value)}
                        />
                      ) : (
                        <input
                          type={
                            field.kind === "secret"
                              ? "password"
                              : field.kind === "int" || field.kind === "float"
                                ? "number"
                                : "text"
                          }
                          step={field.kind === "int" ? "1" : "any"}
                          autoComplete={
                            field.kind === "secret" ? "new-password" : "off"
                          }
                          placeholder={
                            field.kind === "secret"
                              ? snapshot.ai.configured
                                ? "已配置，留空保留现有密钥"
                                : "输入 API 密钥"
                              : ""
                          }
                          value={String(values[field.key] ?? "")}
                          onChange={(e) => edit(field.key, e.target.value)}
                        />
                      )}
                      <span className="setting-key">
                        {field.kind === "secret"
                          ? "留空保留已保存的密钥；输入新值后保存替换。"
                          : field.key}
                      </span>
                    </label>
                  ),
                )}
            </div>
          </Panel>
        ))}
        <div className="settings-save">
          <span role="status">
            {status || "修改后保存，即可更新本地设置。"}
          </span>
          <Button type="submit" tone="primary" disabled={disabled || saving}>
            {saving ? "保存中…" : "保存全部设置"}
          </Button>
        </div>
      </form>
      {clearKey && (
        <Modal
          title="清除已保存的 API 密钥"
          onClose={() => setClearKey(false)}
          footer={
            <>
              <Button onClick={() => setClearKey(false)}>保留密钥</Button>
              <Button
                tone="danger"
                disabled={disabled || saving}
                onClick={() => void persist({}, true)}
              >
                {saving ? "清除中…" : "确认清除"}
              </Button>
            </>
          }
        >
          <p>清除后需要重新配置密钥才能使用 AI 功能。其他设置不会改变。</p>
        </Modal>
      )}
      {logout && (
        <Modal
          title="退出登录"
          onClose={() => setLogout(false)}
          footer={
            <>
              <Button onClick={() => setLogout(false)}>取消</Button>
              <Button
                tone="danger"
                disabled={disabled}
                onClick={() => {
                  void run("logout").then((ok) => {
                    if (ok) setLogout(false);
                  });
                }}
              >
                退出并清理课程记录
              </Button>
            </>
          }
        >
          <p>退出登录会清理本地课程记录。再次使用时需要重新登录并读取课程。</p>
        </Modal>
      )}
    </div>
  );
}
