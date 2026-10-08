import { test } from "node:test";
import assert from "node:assert/strict";
import { settingsPayload, appendEvents } from "../src/state.ts";
import * as state from "../src/state.ts";

test("blank secret preserves configured key and numeric input is validated", () => {
  const fields = [
    { key: "AI_API_KEY", kind: "secret" },
    { key: "MAX", kind: "int" },
  ];
  assert.deepEqual(settingsPayload(fields, { AI_API_KEY: "", MAX: "5" }), {
    AI_API_KEY: "",
    MAX: 5,
  });
  assert.throws(
    () => settingsPayload(fields, { AI_API_KEY: "", MAX: "2.5" }),
    /整数/,
  );
  assert.throws(
    () => settingsPayload(fields, { AI_API_KEY: "", MAX: "" }),
    /数字/,
  );
});

test("poll event history stays bounded without losing newest backend result", () => {
  const history = Array.from({ length: 300 }, (_, i) => ({
    level: "info",
    payload: String(i),
  }));
  const next = appendEvents(history, [
    { level: "grades", payload: "最新成绩" },
  ]);
  assert.equal(next.length, 250);
  assert.equal(next.at(-1).payload, "最新成绩");
});

test("result cursors remain monotonic after history reaches its cap", () => {
  let history = appendEvents(
    [],
    Array.from({ length: 300 }, () => ({ level: "info", payload: "心跳" })),
  );
  const cursor = history.at(-1).sequence;
  history = appendEvents(history, [{ level: "raw", payload: "搜索结果" }]);
  assert.equal(history.length, 250);
  assert.equal(history.filter((event) => event.sequence > cursor).length, 1);
  assert.equal(history.at(-1).payload, "搜索结果");
});

test("search and wrong results ignore unrelated raw events and each other", () => {
  assert.equal(typeof state.resultEvent, "function");
  const events = appendEvents(
    [],
    [
      { level: "search_results", payload: "课程资料" },
      { level: "wrong_results", payload: "错题内容" },
      { level: "raw", payload: "其他操作的输出" },
      { level: "info", payload: "播放状态" },
    ],
  );
  assert.equal(state.resultEvent(events, "search_results").payload, "课程资料");
  assert.equal(state.resultEvent(events, "wrong_results").payload, "错题内容");
  assert.equal(state.resultEvent(events, "search_results", 1), undefined);
  assert.equal(
    state.resultEvent([{ level: "raw", payload: "不可混入" }], "wrong_results"),
    undefined,
  );
});

test("fraction progress renders as percent and clamps to the visual bounds", () => {
  assert.equal(typeof state.progressPercent, "function");
  assert.equal(state.progressPercent(0), 0);
  assert.equal(state.progressPercent(0.5), 50);
  assert.equal(state.progressPercent(1), 100);
  assert.equal(state.progressPercent(-0.2), 0);
  assert.equal(state.progressPercent(1.2), 100);
});

test("external settings updates merge around an unsaved edit and save only that edit", () => {
  assert.equal(typeof state.mergeSettingsValues, "function");
  const fields = [
    { key: "DISCUSS_MAX", kind: "int" },
    { key: "AI_MODEL", kind: "str" },
    { key: "AI_API_KEY", kind: "secret" },
  ];
  const dirty = new Set(["AI_MODEL"]);
  const merged = state.mergeSettingsValues(
    { DISCUSS_MAX: 5, AI_MODEL: "unsaved-model", AI_API_KEY: "" },
    {
      DISCUSS_MAX: 10,
      AI_MODEL: "saved-model",
      AI_API_KEY: "must-never-enter-form",
    },
    fields,
    dirty,
  );
  assert.equal(merged.DISCUSS_MAX, 10);
  assert.equal(merged.AI_MODEL, "unsaved-model");
  assert.equal(merged.AI_API_KEY, "");
  assert.deepEqual(
    state.settingsPayload(
      fields.filter((field) => dirty.has(field.key)),
      merged,
    ),
    { AI_MODEL: "unsaved-model" },
  );
});

test("save acknowledgement clears only edits unchanged since submission", () => {
  assert.equal(typeof state.remainingDirtyFields, "function");
  const remaining = state.remainingDirtyFields(
    new Set(["AI_MODEL", "AI_API_KEY", "DISCUSS_MAX"]),
    { AI_MODEL: 1, AI_API_KEY: 1 },
    { AI_MODEL: 2, AI_API_KEY: 1, DISCUSS_MAX: 1 },
  );
  assert.deepEqual([...remaining].sort(), ["AI_MODEL", "DISCUSS_MAX"]);
  const merged = state.mergeSettingsValues(
    { AI_MODEL: "newer-edit", DISCUSS_MAX: 12 },
    { AI_MODEL: "submitted-edit", DISCUSS_MAX: 10 },
    [
      { key: "AI_MODEL", kind: "str" },
      { key: "DISCUSS_MAX", kind: "int" },
    ],
    remaining,
  );
  assert.equal(merged.AI_MODEL, "newer-edit");
  assert.equal(merged.DISCUSS_MAX, 12);
});
