import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate as nextTick } from 'node:timers/promises';
import { stageLabels } from './labels.js';

// 最小 DOM 适配器只提供页面使用的接口；导入真实 app.js，验证请求未完成时的事件处理。
// 这是前端逻辑回归，不作为浏览器布局验收。
test('页面忙于执行时仍显示 SSE 阶段事件，且状态快照不变也不会丢进度', async () => {
  const previous = Object.fromEntries(
    ['document', 'window', 'localStorage', 'fetch', 'setInterval', 'setTimeout'].map((key) => [
      key,
      globalThis[key],
    ]),
  );
  const elements = new Map();
  const listeners = new Map();
  const timers = [];
  const element = (selector) => {
    if (!elements.has(selector)) {
      const classes = new Set();
      elements.set(selector, {
        value: '',
        textContent: '',
        innerHTML: '',
        classList: {
          add: (name) => classes.add(name),
          remove: (name) => classes.delete(name),
          contains: (name) => classes.has(name),
        },
        querySelectorAll: () => [],
        close() {},
      });
    }
    return elements.get(selector);
  };
  const optionalButtons = new Set([
    '#reject-task',
    '#recover-task',
    '#replan-task',
    '#simulate-change',
    '#simulate-resource',
  ]);
  globalThis.document = {
    body: element('body'),
    querySelector(selector) {
      if (selector === 'dialog[open]' || optionalButtons.has(selector)) return null;
      if (selector === '#events' && !element('#detail').innerHTML.includes('id="events"'))
        return null;
      return element(selector);
    },
    querySelectorAll: () => [],
  };
  globalThis.window = { addEventListener: (name, callback) => listeners.set(name, callback) };
  globalThis.localStorage = { getItem: () => null };
  globalThis.setInterval = () => 0;
  globalThis.setTimeout = (callback, seconds) => {
    const timer = previous.setTimeout(callback, seconds);
    timers.push(timer);
    return timer;
  };
  let task = null;
  let streamController;
  let releaseRun;
  const pendingRun = new Promise((resolve) => (releaseRun = resolve));
  let markStarted;
  const started = new Promise((resolve) => (markStarted = resolve));
  const json = (data) => Response.json(data);
  globalThis.fetch = async (url, options) => {
    if (url === '/api/me') return json({ role: 'supervisor' });
    if (url === '/health') return json({ planner_mode: 'demo' });
    if (url.startsWith('/api/tasks/page?'))
      return json({ items: task ? [task] : [], total: task ? 1 : 0, counts: {} });
    if (url === '/api/tasks?defer_start=true') {
      task = {
        id: 'live-progress',
        order_id: 'SO-TEST',
        status: 'CREATED',
        planner_mode: 'demo',
        state: {},
        next_nodes: [],
        active: false,
        created_at: '2026-10-21T00:00:00Z',
      };
      return json(task);
    }
    if (url.includes('/events?')) return json([]);
    if (url.includes('/stream?'))
      return new Response(
        new ReadableStream({
          start(controller) {
            streamController = controller;
            options.signal.addEventListener('abort', () => controller.close(), { once: true });
          },
        }),
        {
          headers: { 'Content-Type': 'text/event-stream' },
        },
      );
    if (url === '/api/tasks/live-progress/recover') {
      markStarted();
      return pendingRun;
    }
    throw new Error(`unexpected request ${url}`);
  };
  const until = async (condition) => {
    for (let attempt = 0; attempt < 200; attempt++) {
      if (condition()) return;
      await nextTick();
    }
    assert.fail('page did not reach expected state');
  };
  try {
    await import(`./app.js?test=${Date.now()}`);
    await until(() => element('#task-count').textContent === 0);
    element('#order-select').value = 'SO-TEST';
    element('#create-form').onsubmit({ preventDefault() {} });
    await started;
    await until(() => streamController !== undefined);
    assert(element('body').classList.contains('working'));
    const packet = {
      id: 1,
      type: 'graph.stage.started',
      data: { stage: 'propose' },
      created_at: '2026-10-21T00:00:01Z',
    };
    streamController.enqueue(
      new TextEncoder().encode(`id: 1\nevent: progress\ndata: ${JSON.stringify(packet)}\n\n`),
    );
    await until(() => element('#events').innerHTML.includes(stageLabels.propose));
    assert.equal(task.status, 'CREATED');
    assert.equal(element('#activity').hidden, false);
    // 业务请求还没完成，快照也没改变，但阶段事件已经可见。
    task = { ...task, status: 'WAITING_APPROVAL' };
    releaseRun(json(task));
    await until(() => element('#activity').hidden === true);
  } finally {
    releaseRun(json(task));
    listeners.get('pagehide')?.();
    await nextTick();
    for (const timer of timers) clearTimeout(timer);
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
