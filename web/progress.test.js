import test from 'node:test';
import assert from 'node:assert/strict';
import { parseProgressFrame, subscribeProgress } from './progress.js';

test('SSE 解析忽略心跳并接受 CRLF 和多行数据', () => {
  assert.equal(parseProgressFrame(': heartbeat'), null);
  assert.equal(parseProgressFrame('id: nope\nevent: progress\ndata: {}'), null);
  assert.deepEqual(
    parseProgressFrame('id: 7\r\nevent: progress\r\ndata: {"id":7,\r\ndata: "type":"执行"}'),
    {
      id: 7,
      data: { id: 7, type: '执行' },
    },
  );
});

test('分块读取、断线游标重连、重复事件过滤和主动关闭', async () => {
  const previous = globalThis.fetch;
  const seen = [],
    requests = [];
  let subscription;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, authorization: options.headers.Authorization });
    const id = requests.length;
    const text = `${id === 2 ? 'id: 1\nevent: progress\ndata: {"id":1}\n\n' : ''}id: ${id}\nevent: progress\ndata: {"id":${id},"text":"进度"}\n\n`;
    const bytes = new TextEncoder().encode(text);
    return new Response(
      new ReadableStream({
        start(controller) {
          // 故意逐字节切分，覆盖 UTF-8 多字节字符跨网络分块的情况。
          for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
          controller.close();
        },
      }),
      { headers: { 'Content-Type': 'text/event-stream' } },
    );
  };
  try {
    subscription = subscribeProgress('task', () => 'demo-token', {
      onState() {},
      onEvent(row) {
        seen.push(row.id);
        if (row.id === 2) subscription.stop();
      },
    });
    await subscription.done;
    assert.deepEqual(seen, [1, 2]);
    assert.equal(requests[1].url, '/api/tasks/task/stream?after=1');
    assert.equal(requests[0].authorization, 'Bearer demo-token');
    assert(!requests[0].url.includes('demo-token'));
  } finally {
    subscription?.stop();
    globalThis.fetch = previous;
  }
});
