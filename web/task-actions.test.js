import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskWithProgress } from './task-actions.js';
import { renderTaskDetail } from './render.js';

test('慢执行返回前先展示持久任务 ID 并建立进度订阅', async () => {
  const calls = [];
  let releaseExecution;
  const pending = new Promise((resolve) => (releaseExecution = resolve));
  let executionStarted;
  const started = new Promise((resolve) => (executionStarted = resolve));
  const api = async (path, options) => {
    calls.push(path);
    assert.equal(options.method, 'POST');
    if (path === '/tasks?defer_start=true') {
      assert.deepEqual(options.body, { order_id: 'SO-TEST' });
      return { id: 'persistent-id', status: 'CREATED' };
    }
    assert.equal(path, '/tasks/persistent-id/recover');
    assert.deepEqual(calls, ['/tasks?defer_start=true', 'subscribed', path]);
    executionStarted();
    return pending;
  };
  let finished = false;
  const result = createTaskWithProgress(api, 'SO-TEST', async (task) => {
    assert.equal(task.id, 'persistent-id');
    calls.push('subscribed');
  }).then((task) => {
    finished = true;
    return task;
  });
  await started;
  assert.equal(finished, false);
  releaseExecution({ id: 'persistent-id', status: 'WAITING_APPROVAL' });
  assert.equal((await result).status, 'WAITING_APPROVAL');
});

test('执行请求断线后仍保留已展示的任务，未启动任务提供显式恢复入口', async () => {
  let shown;
  const task = {
    id: 'persistent-id',
    order_id: 'SO-TEST',
    status: 'CREATED',
    planner_mode: 'demo',
    state: {},
    next_nodes: [],
    active: false,
    created_at: '2026-10-21T00:00:00Z',
  };
  await assert.rejects(
    createTaskWithProgress(
      async (path) => {
        if (path === '/tasks?defer_start=true') return task;
        throw new Error('connection lost');
      },
      task.order_id,
      async (created) => (shown = created),
    ),
    /connection lost/,
  );
  assert.equal(shown.id, task.id);
  assert.match(renderTaskDetail(shown, 'operator'), /id="recover-task"/);
  assert.match(renderTaskDetail(shown, 'operator'), /开始处理任务/);
});
