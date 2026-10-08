import type { BackendEvent, SettingField, Value } from "./types.ts";

export function progressPercent(fraction: number): number {
  return Math.min(1, Math.max(0, fraction)) * 100;
}

export function mergeSettingsValues(
  current: Record<string, Value>,
  incoming: Record<string, Value>,
  fields: Pick<SettingField, "key" | "kind">[],
  dirty: ReadonlySet<string>,
): Record<string, Value> {
  const next = { ...current };
  for (const field of fields) {
    if (field.kind === "secret") next[field.key] = current[field.key] ?? "";
    else if (!dirty.has(field.key) && Object.hasOwn(incoming, field.key))
      next[field.key] = incoming[field.key];
  }
  return next;
}

export function remainingDirtyFields(
  dirty: ReadonlySet<string>,
  submitted: Record<string, number>,
  current: Record<string, number>,
): Set<string> {
  return new Set(
    [...dirty].filter(
      (key) => submitted[key] === undefined || submitted[key] !== current[key],
    ),
  );
}

export function settingsPayload(
  fields: Pick<SettingField, "key" | "kind">[],
  values: Record<string, Value>,
): Record<string, Value> {
  const result: Record<string, Value> = {};
  for (const field of fields) {
    const value = values[field.key] ?? "";
    if (field.kind === "int" || field.kind === "float") {
      if (String(value).trim() === "" || !Number.isFinite(Number(value)))
        throw new Error(`${field.key} 请输入有效数字`);
      if (field.kind === "int" && !Number.isInteger(Number(value)))
        throw new Error(`${field.key} 请输入整数`);
      result[field.key] = Number(value);
    } else result[field.key] = value;
  }
  return result;
}
export function appendEvents(
  previous: BackendEvent[],
  incoming: BackendEvent[],
): BackendEvent[] {
  let sequence = previous.at(-1)?.sequence ?? previous.length;
  return [
    ...previous,
    ...incoming.map((event) => ({ ...event, sequence: ++sequence })),
  ].slice(-250);
}
export function eventText(event: BackendEvent): string {
  return typeof event.payload === "string"
    ? event.payload
    : String(event.payload.text ?? "");
}
export function resultEvent(
  events: BackendEvent[],
  level: "search_results" | "wrong_results",
  after = 0,
): BackendEvent | undefined {
  return [...events]
    .reverse()
    .find((event) => event.level === level && (event.sequence ?? 1) > after);
}
