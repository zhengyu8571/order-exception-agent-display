import { createApi } from './api.js';
import { escapeHtml, time } from './format.js';
import { labels, eventLabels, sourceLabels, stageLabels } from './labels.js';
import { renderTaskList, renderTaskDetail } from './render.js';
import { renderApprovalSummary } from './approval.js';
import { subscribeProgress } from './progress.js';
import { createTaskWithProgress } from './task-actions.js';

const $ = (s) => document.querySelector(s);
let token = localStorage.getItem('orderflow-token') || 'local-supervisor';
let allTasks = [],
  selectedId = null,
  filter = 'all',
  role = '',
  busy = false,
  lastDecision = null,
  detailSignature = '';
let offset = 0,
  total = 0,
  refreshSequence = 0,
  plannerMode = 'demo';
let progress = null,
  progressTask = null,
  progressTimer = null,
  searchTimer = null;
const eventCursors = new Map();
const eventRows = new Map();
const api = createApi(() => token);

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.add('visible');
  setTimeout(() => $('#toast').classList.remove('visible'), 4500);
}
async function action(fn) {
  if (busy) return;
  busy = true;
  document.body.classList.add('working');
  $('#activity').hidden = false;
  try {
    await fn();
  } catch (error) {
    toast(error.message || '连接已中断，请刷新后检查任务进度');
  } finally {
    busy = false;
    document.body.classList.remove('working');
    $('#activity').hidden = true;
    await refresh().catch(() => {});
  }
}

async function refresh() {
  const sequence = ++refreshSequence;
  const params = new URLSearchParams({
    status: filter,
    search: $('#search').value,
    offset,
    limit: 20,
  });
  const page = await api('/tasks/page?' + params);
  if (sequence !== refreshSequence) return;
  const tasks = page.items;
  total = page.total;
  if (!tasks.length && offset > 0) {
    offset = Math.max(0, Math.ceil(total / 20) * 20 - 20);
    return refresh();
  }
  allTasks = tasks;
  $('#metric-total').textContent = Object.values(page.counts).reduce((sum, n) => sum + n, 0);
  $('#metric-wait').textContent = page.counts.WAITING_APPROVAL || 0;
  $('#metric-done').textContent = page.counts.COMPLETED || 0;
  $('#metric-recover').textContent = [
    'CREATED',
    'RUNNING',
    'RECOVERABLE',
    'STALE',
    'BLOCKED',
    'REJECTED',
    'NEEDS_MANUAL',
  ].reduce((sum, key) => sum + (page.counts[key] || 0), 0);
  $('#task-count').textContent = total;
  $('#page-info').textContent = total
    ? `${offset + 1}–${offset + tasks.length} / ${total}`
    : '0 / 0';
  $('#page-prev').disabled = offset === 0;
  $('#page-next').disabled = offset + 20 >= total;
  if (!selectedId && tasks.length) selectedId = tasks[0].id;
  renderList();
  if (selectedId)
    await renderDetail(
      tasks.find((t) => t.id === selectedId) || (await api('/tasks/' + selectedId)),
    );
  else {
    progress?.stop();
    progress = null;
    progressTask = null;
    detailSignature = '';
    $('#detail').innerHTML =
      '<div class="no-tasks">当前筛选下没有任务，可创建任务或调整筛选条件。</div>';
  }
}

function renderList() {
  $('#task-list').innerHTML = renderTaskList(allTasks, selectedId);
  document.querySelectorAll('.task-row').forEach(
    (row) =>
      (row.onclick = async () => {
        selectedId = row.dataset.id;
        renderList();
        await renderDetail(allTasks.find((t) => t.id === selectedId));
      }),
  );
}

function ensureProgress(taskId) {
  if (progressTask === taskId && progress) return;
  progress?.stop();
  clearTimeout(progressTimer);
  progressTask = taskId;
  progress = subscribeProgress(taskId, () => token, {
    after: eventCursors.get(taskId) || 0,
    onState: (state) => {
      if (taskId === selectedId)
        $('#connection').textContent = {
          connecting: '连接执行进度',
          live: '执行进度已连接',
          reconnecting: '进度重连中',
        }[state];
    },
    onEvent: (row) => {
      rememberEvents(taskId, [row]);
      // busy 只防止重复提交；正在执行时也要显示已提交的阶段事件。
      renderEvents(taskId);
      clearTimeout(progressTimer);
      progressTimer = setTimeout(() => {
        if (!document.querySelector('dialog[open]')) refresh().catch(() => {});
      }, 400);
    },
  });
}

async function renderDetail(task) {
  if (!task || task.id !== selectedId) return;
  const signature = JSON.stringify(task);
  if (signature === detailSignature) {
    ensureProgress(task.id);
    return;
  }
  // 进度刷新不能打断用户阅读证据或恢复记录；同一任务保留已展开的区域。
  const expanded =
    task.id === progressTask
      ? [...$('#detail').querySelectorAll('details[open]')].map((node) =>
          node.querySelector('summary').textContent.trim(),
        )
      : [];
  $('#detail').innerHTML = renderTaskDetail(task, role);
  $('#detail')
    .querySelectorAll('details')
    .forEach((node) => {
      if (expanded.includes(node.querySelector('summary').textContent.trim())) node.open = true;
    });
  document
    .querySelectorAll('.approve-candidate')
    .forEach((b) => (b.onclick = () => decision(task, 'approve', b.dataset.candidate)));
  if ($('#reject-task')) $('#reject-task').onclick = () => decision(task, 'reject', '');
  if ($('#recover-task'))
    $('#recover-task').onclick = () =>
      action(async () => {
        await api(`/tasks/${task.id}/recover`, { method: 'POST' });
        toast('已尝试从保存的进度继续');
      });
  if ($('#replan-task'))
    $('#replan-task').onclick = () =>
      action(async () => {
        await api(`/tasks/${task.id}/replan`, { method: 'POST' });
        toast('已重新调查，旧审批不会沿用');
      });
  if ($('#simulate-change'))
    $('#simulate-change').onclick = () =>
      action(async () => {
        await api(`/demo/orders/${task.order_id}/change`, { method: 'POST' });
        toast('已模拟外仓库存变化，执行前校验将检查最新版本');
      });
  if ($('#simulate-resource'))
    $('#simulate-resource').onclick = () =>
      action(async () => {
        const context = task.state.context;
        const choice = $('#resource-kind').value;
        const warehouse = context.warehouses[0];
        const batch = context.purchase_batches[0];
        const change = {
          warehouse: {
            target: 'warehouse',
            resource_id: warehouse?.id,
            quantity: Math.max(0, (warehouse?.available_quantity || 0) - 1),
          },
          route: {
            target: 'route',
            resource_id: warehouse?.id,
            unit_cost_cents: (warehouse?.unit_cost_cents || 0) + 1,
          },
          batch: { target: 'batch', resource_id: batch?.id, status: 'CANCELLED' },
          policy: { target: 'policy', allow_split: !context.policy.allow_split },
          local: { target: 'local', quantity: Math.max(0, context.local_quantity - 1) },
        }[choice];
        await api(`/demo/orders/${task.order_id}/resources`, { method: 'POST', body: change });
        toast('资源已独立变更；原方案的审批将在执行前被判定过期');
      });
  const rows = await api(`/tasks/${task.id}/events?after=${eventCursors.get(task.id) || 0}`);
  if (task.id !== selectedId) return;
  rememberEvents(task.id, rows);
  renderEvents(task.id);
  detailSignature = signature;
  ensureProgress(task.id);
}

function rememberEvents(taskId, rows) {
  const merged = new Map((eventRows.get(taskId) || []).map((row) => [row.id, row]));
  for (const row of rows) merged.set(row.id, row);
  const ordered = [...merged.values()].sort((a, b) => a.id - b.id);
  eventRows.set(taskId, ordered.slice(-12));
  if (ordered.length)
    eventCursors.set(taskId, Math.max(eventCursors.get(taskId) || 0, ordered.at(-1).id));
}

function renderEvents(taskId) {
  if (taskId !== selectedId || !$('#events')) return;
  $('#events').innerHTML =
    [...(eventRows.get(taskId) || [])]
      .reverse()
      .map(
        (row) =>
          `<div class="event"><i></i><div>${escapeHtml(eventLabels[row.type] || row.type)}${row.data.source ? ` · ${escapeHtml(sourceLabels[row.data.source] || row.data.source)}` : ''}${row.data.stage ? ` · ${escapeHtml(stageLabels[row.data.stage] || row.data.stage)}` : ''}${row.data.duration_ms ? ` · ${(row.data.duration_ms / 1000).toFixed(1)}s` : ''}${row.data.version ? ` · v${row.data.version}` : ''}${row.data.message ? `<small>${escapeHtml(row.data.message)}</small>` : ''}</div><time>${time(row.created_at)}</time></div>`,
      )
      .join('') || '<span class="task-subtitle">暂无处理记录</span>';
}

function decision(task, type, candidate) {
  lastDecision = { task, type, candidate, requestId: crypto.randomUUID() };
  $('#decision-title').textContent = type === 'approve' ? '批准并执行方案' : '拒绝当前方案';
  $('#decision-description').textContent = `${task.order_id} · 方案版本 v${task.state.revision}`;
  $('#decision-summary').innerHTML =
    type === 'approve'
      ? renderApprovalSummary(task, candidate)
      : '<p>拒绝后本轮不执行业务修改；重新生成的方案仍需再次审批。</p>';
  $('#decision-submit').textContent = type === 'approve' ? '确认批准并执行' : '确认拒绝';
  $('#comment').value = '';
  $('#decision-dialog').showModal();
}
$('#decision-form').onsubmit = (event) => {
  event.preventDefault();
  $('#decision-dialog').close();
  action(async () => {
    const { task, type, candidate, requestId } = lastDecision;
    const result = await api(`/tasks/${task.id}/approval`, {
      method: 'POST',
      body: {
        ...(task.approval_context?.[candidate] || {}),
        proposal_version: task.state.revision,
        decision: type,
        candidate_id: candidate,
        comment: $('#comment').value,
        request_id: requestId,
      },
    });
    toast(
      result.status === 'COMPLETED' ? '交付计划调整完成' : labels[result.status] || '审批已记录',
    );
  });
};
async function loadOrders() {
  const orders = await api('/orders');
  $('#order-select').innerHTML = orders
    .filter((o) => o.status === 'EXCEPTION')
    .map(
      (o) =>
        `<option value="${escapeHtml(o.id)}">${escapeHtml(o.id)} · ${o.required_quantity} 件 · ${o.due_days} 天内交付</option>`,
    )
    .join('');
}
async function openCreate() {
  try {
    await loadOrders();
    $('#create-dialog').showModal();
  } catch (error) {
    toast(error.message);
  }
}
$('#new-task').onclick = openCreate;
$('#empty-create').onclick = openCreate;
$('#create-form').onsubmit = (event) => {
  event.preventDefault();
  const id = $('#order-select').value;
  if (!id) return toast('请先新增一笔演示订单');
  $('#create-dialog').close();
  action(async () => {
    const task = await createTaskWithProgress(api, id, async (created) => {
      selectedId = created.id;
      offset = 0;
      // 订阅在慢执行请求之前建立；刷新失败也不能把持久任务变成不可发现的临时状态。
      await refresh();
      ensureProgress(created.id);
    });
    toast(
      task.status === 'WAITING_APPROVAL'
        ? '调查完成，方案等待审批'
        : labels[task.status] || '任务已创建',
    );
  });
};
$('#seed-scenario').onclick = () =>
  action(async () => {
    const profile = $('#scenario-profile').value;
    const result = await api('/demo/scenarios', {
      method: 'POST',
      body: profile ? { profile } : {},
    });
    await loadOrders();
    $('#order-select').value = result.order_id;
    toast('已新增一笔库存不足演示订单');
  });
$('#refresh').onclick = () => refresh().catch((error) => toast(error.message));
$('#search').oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    offset = 0;
    selectedId = null;
    refresh().catch((error) => toast(error.message));
  }, 300);
};
$('#page-prev').onclick = () => {
  offset = Math.max(0, offset - 20);
  selectedId = null;
  refresh().catch((error) => toast(error.message));
};
$('#page-next').onclick = () => {
  offset += 20;
  selectedId = null;
  refresh().catch((error) => toast(error.message));
};
document.querySelectorAll('[data-filter]').forEach(
  (b) =>
    (b.onclick = () => {
      filter = b.dataset.filter;
      offset = 0;
      selectedId = null;
      document
        .querySelectorAll('[data-filter]')
        .forEach((x) => x.classList.toggle('active', x === b));
      refresh().catch((error) => toast(error.message));
    }),
);
document.querySelectorAll('.close').forEach((b) => (b.onclick = () => b.closest('dialog').close()));
$('#settings').onclick = () => {
  $('#token-input').value = token;
  $('#auth-dialog').showModal();
};
$('#settings-mobile').onclick = $('#settings').onclick;
document
  .querySelectorAll('[data-token]')
  .forEach((b) => (b.onclick = () => ($('#token-input').value = b.dataset.token)));
$('#auth-form').onsubmit = async (event) => {
  event.preventDefault();
  const previous = token;
  token = $('#token-input').value;
  try {
    await initialize();
    localStorage.setItem('orderflow-token', token);
    $('#auth-dialog').close();
  } catch (error) {
    token = previous;
    toast(error.message);
  }
};
async function initialize() {
  const me = await api('/me');
  role = me.role;
  detailSignature = '';
  progress?.stop();
  progress = null;
  progressTask = null;
  $('#role-label').textContent = role === 'supervisor' ? '审批主管' : '业务操作员';
  $('#settings-mobile').textContent = role === 'supervisor' ? '主管' : '操作员';
  $('.avatar').textContent = role === 'supervisor' ? '主' : '操';
  const health = await fetch('/health').then((r) => r.json());
  plannerMode = health.planner_mode;
  $('#connection').textContent = '服务在线';
  $('#mode').textContent = health.planner_mode === 'llm' ? '模型决策模式' : '演示决策模式';
  $('#seed-scenario').hidden = role !== 'supervisor';
  $('#scenario-profile').disabled = plannerMode !== 'llm';
  $('#scenario-profile').value = plannerMode === 'llm' ? 'A' : '';
  $('#scenario-note').textContent =
    plannerMode === 'llm'
      ? '选择业务数据场景；调查和方案由当前模型决定，不保证固定答案。'
      : '当前为固定候选演示。A–F 资源场景需启用模型模式，演示不代表真实模型效果。';
  await refresh();
}
initialize().catch((error) => {
  toast(error.message);
  $('#connection').textContent = '需要访问凭据';
  $('#token-input').value = token;
  $('#auth-dialog').showModal();
});
setInterval(() => {
  if (!document.querySelector('dialog[open]'))
    refresh().catch(() => ($('#connection').textContent = '连接暂不可用'));
}, 15000);
window.addEventListener('pagehide', () => progress?.stop());
