import { test } from "node:test";
import assert from "node:assert/strict";
import { createPollingSession } from "../src/polling.ts";

test("repeated desktop-ready signals keep one bootstrap and non-overlapping polling chain", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let bootstrapCalls = 0,
    pollCalls = 0,
    deliverPoll;
  const api = {
    bootstrap: async () => {
      bootstrapCalls++;
      return { ok: true, data: { ready: true } };
    },
    poll: () => {
      pollCalls++;
      return new Promise((resolve) => {
        deliverPoll = resolve;
      });
    },
  };
  const received = [];
  const session = createPollingSession(() => api, {
    onSnapshot: (value) => received.push(value),
    onEvents() {},
    onError() {},
    onOffline() {},
    onConnected() {},
  });
  await Promise.all([session.start(), session.start(), session.start()]);
  await session.start();
  assert.equal(bootstrapCalls, 1);
  context.mock.timers.tick(500);
  assert.equal(pollCalls, 1);
  context.mock.timers.tick(5000);
  await session.start();
  assert.equal(pollCalls, 1);
  deliverPoll({ ok: true, data: { events: [], snapshot: { ready: true } } });
  await Promise.resolve();
  await Promise.resolve();
  context.mock.timers.tick(500);
  assert.equal(pollCalls, 2);
  session.stop();
  deliverPoll({ ok: true, data: { events: [], snapshot: { ready: false } } });
  await Promise.resolve();
  await Promise.resolve();
  context.mock.timers.tick(5000);
  assert.equal(pollCalls, 2);
  assert.equal(received.length, 2);
});
