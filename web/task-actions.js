export async function createTaskWithProgress(api, orderId, onCreated) {
  // 先持久化并展示 ID，再发起请求驱动的执行；断线后仍能通过该 ID 显式恢复。
  const task = await api('/tasks?defer_start=true', {
    method: 'POST',
    body: { order_id: orderId },
  });
  await onCreated(task);
  return api(`/tasks/${encodeURIComponent(task.id)}/recover`, { method: 'POST' });
}
