// 使用 fetch 订阅 SSE，令牌放 Authorization 头，不放 URL，避免进入访问日志。
export function parseProgressFrame(frame) {
  let id = null;
  let event = 'message';
  const data = [];
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (line.startsWith('id:')) id = Number(line.slice(3).trim());
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (event !== 'progress' || !Number.isSafeInteger(id) || id <= 0 || !data.length) return null;
  return { id, data: JSON.parse(data.join('\n')) };
}

function pause(ms, signal) {
  return new Promise((resolve) => {
    function finish() {
      clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    }
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}

export function subscribeProgress(taskId, getToken, { after = 0, onEvent, onState }) {
  const controller = new AbortController();
  const signal = controller.signal;
  let cursor = after;
  const done = (async () => {
    let retry = 1000;
    while (!signal.aborted) {
      let reader;
      try {
        onState('connecting');
        const response = await fetch(
          `/api/tasks/${encodeURIComponent(taskId)}/stream?after=${cursor}`,
          {
            headers: { Authorization: 'Bearer ' + getToken() },
            signal,
          },
        );
        if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
          throw new Error('进度订阅暂不可用');
        }
        onState('live');
        retry = 1000;
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (!signal.aborted) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buffer += decoder.decode(chunk.value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop();
          for (const frame of frames) {
            const record = parseProgressFrame(frame);
            // 事件来自数据库游标；断线重连或切回任务时只消费更大的 ID。
            if (record && record.id > cursor) {
              cursor = record.id;
              onEvent(record.data, cursor);
            }
          }
        }
      } catch {
        if (signal.aborted) break;
      } finally {
        await reader?.cancel().catch(() => {});
      }
      if (!signal.aborted) {
        onState('reconnecting');
        await pause(retry, signal);
        retry = Math.min(retry * 2, 10000);
      }
    }
  })();
  return { stop: () => controller.abort(), done };
}
