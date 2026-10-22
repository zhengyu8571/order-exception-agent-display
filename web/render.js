import { escapeHtml, time, badge } from './format.js';
import { renderDecisionTrace } from './decision.js';
import { renderRecovery } from './approval.js';

export function renderTaskList(visible, selectedId) {
  return visible.length
    ? visible
        .map(
          (t) =>
            /* HTML */ `<article
              class="task-row ${t.id === selectedId ? 'active' : ''}"
              data-id="${escapeHtml(t.id)}"
            >
              <div class="row-top">
                <strong>${escapeHtml(t.order_id)}</strong>${badge(t.status)}
              </div>
              <div class="row-description">库存不足 · 履约计划调整</div>
              <div class="row-bottom">
                <span>${escapeHtml(t.id.slice(0, 8))}</span><time>${time(t.created_at)}</time>
              </div>
            </article>`,
        )
        .join('')
    : '<div class="no-tasks">暂无符合条件的任务<br>创建异常任务，开始处理。</div>';
}

export function renderTaskDetail(task, role) {
  const s = task.state || {},
    f = s.facts || {},
    proposal = s.proposal,
    valid = s.validation?.valid_ids || [];
  const order = f.order || {},
    stock = f.stock || {},
    arrivals = f.arrivals || {};
  const modern = s.decision_protocol === 'v2';
  const context = s.context || {};
  const localQuantity = modern ? context.local_quantity : stock.local_quantity;
  const remoteQuantity = modern
    ? (context.warehouses || []).reduce((sum, row) => sum + row.available_quantity, 0)
    : stock.remote_quantity;
  const purchaseQuantity = modern
    ? (context.purchase_batches || []).reduce((sum, row) => sum + row.quantity, 0)
    : arrivals.quantity;
  let current = ['CREATED', 'RUNNING', 'INVESTIGATING', 'RECOVERABLE'].includes(task.status)
    ? 0
    : task.status === 'WAITING_APPROVAL'
      ? 3
      : task.status === 'COMPLETED'
        ? 6
        : ['BLOCKED', 'NEEDS_MANUAL'].includes(task.status)
          ? 2
          : task.status === 'STALE'
            ? 4
            : task.status === 'REJECTED'
              ? 3
              : 4;
  if (task.active)
    current =
      {
        INVESTIGATING: 0,
        SUPPLEMENTAL: 0,
        RETHINK: 1,
        VALIDATING: 2,
        WAITING_APPROVAL: 3,
        APPROVED: 3,
        EXECUTING: 4,
        VERIFYING: 5,
      }[s.phase] ?? current;
  const steps = ['业务调查', '方案生成', '规则校验', '人工审批', '可靠执行', '结果验证'];
  const candidates = (proposal?.candidates || [])
    .map((c) => {
      const errors = s.validation?.findings?.[c.id] || [];
      const allowed = valid.includes(c.id),
        chosen = s.approval?.candidate_id === c.id;
      return /* HTML */ `<article
        class="candidate ${c.id === proposal.recommended_id ? 'recommended' : ''} ${allowed
          ? ''
          : 'invalid'}"
      >
        <div class="candidate-head">
          <h3>
            ${escapeHtml(c.title)}${c.id === proposal.recommended_id
              ? '<span class="recommend-tag">推荐方案</span>'
              : ''}
          </h3>
          ${chosen
            ? '<span class="status COMPLETED">已选定</span>'
            : allowed
              ? '<span class="status COMPLETED">校验通过</span>'
              : '<span class="status REJECTED">校验未通过</span>'}
        </div>
        <p>${escapeHtml(c.rationale)}</p>
        <div class="numbers">
          <span>本仓 ${c.local_quantity} 件</span><span>调拨 ${c.transfer_quantity} 件</span
          ><span>采购 ${c.purchase_quantity} 件</span><span>第 ${c.completion_days} 天齐备</span>
        </div>
        <p class="risk">△ ${c.risks.map(escapeHtml).join('；')}</p>
        ${errors.length ? `<div class="errors">${errors.map(escapeHtml).join('；')}</div>` : ''}
        <div class="candidate-footer">
          <small
            >${c.split_delivery
              ? '分批交付 · 首批 ' + c.first_delivery_quantity + ' 件'
              : '资源齐备后整单交付'}</small
          >${task.status === 'WAITING_APPROVAL' && allowed && role === 'supervisor'
            ? `<button class="primary approve-candidate" data-candidate="${escapeHtml(c.id)}">批准并执行 →</button>`
            : ''}
        </div>
      </article>`;
    })
    .join('');
  return /* HTML */ `<div class="detail-heading">
      <h2>${escapeHtml(task.order_id)}</h2>
      ${badge(task.status)}
    </div>
    <div class="task-subtitle">
      ${escapeHtml(order.customer || '订单异常处理')} · 任务 ${escapeHtml(task.id.slice(0, 8))} ·
      ${time(task.created_at)}
    </div>
    <div class="steps">
      ${steps
        .map(
          (name, i) =>
            `<div class="step ${i < current ? 'done' : i === current ? 'current' : ''}"><b>${i < current ? '✓' : i + 1}</b>${name}</div>`,
        )
        .join('')}
    </div>
    <div class="section-label">业务调查<span>已查询 ${Object.keys(f).length} / 4 类信息</span></div>
    <div class="facts">
      <div class="fact">
        <span>订单需求</span><strong>${order.required_quantity ?? '—'}</strong><small>件</small>
      </div>
      <div class="fact">
        <span>本仓 / 外仓可用</span
        ><strong>${localQuantity ?? '—'} / ${remoteQuantity ?? '—'}</strong><small>件</small>
      </div>
      <div class="fact">
        <span>预计采购到货</span><strong>${purchaseQuantity ?? '—'}</strong
        ><small
          >${modern ? '件 · 批次详情见证据' : `件 · ${arrivals.arrival_days ?? '—'} 天后`}</small
        >
      </div>
      <div class="fact">
        <span>客户要求交期</span
        ><strong
          >${modern ? escapeHtml(context.order?.due_date || '—') : (order.due_days ?? '—')}</strong
        ><small>${modern ? '原定日期' : '天内'}</small>
      </div>
    </div>
    ${proposal
      ? `<div class="section-label">候选处置方案<span>方案版本 v${s.revision} · ${task.planner_mode === 'llm' ? '模型生成' : '演示规则生成'}</span></div>${candidates}`
      : ''}${task.status === 'WAITING_APPROVAL'
      ? `<div class="notice">${role === 'supervisor' ? '请选择已通过校验的方案进行审批。' : '方案已生成，等待主管审批。操作员无权批准业务修改。'}<br>预计到货不代表确定到货；当前审批范围为订单交付计划调整。</div>`
      : ''}${task.status === 'COMPLETED'
      ? '<div class="notice success">✓ 交付计划已更新，操作记录和订单结果已核验。实际调拨、采购到货和出库由后续业务流程完成。</div>'
      : ''}${s.error || task.last_error
      ? `<div class="notice error">${escapeHtml(s.error || task.last_error)}</div>`
      : ''}
    <div class="action-bar">
      ${task.status === 'WAITING_APPROVAL' && role === 'supervisor'
        ? `<button id="reject-task" class="secondary">拒绝方案</button>${modern ? '' : '<button id="simulate-change" class="text-btn">模拟订单变化</button>'}`
        : ''}${['CREATED', 'RECOVERABLE'].includes(task.status) && !task.active
        ? `<button id="recover-task" class="primary">${task.status === 'CREATED' ? '开始处理任务' : '从已保存进度继续'} →</button>`
        : ''}${['STALE', 'BLOCKED', 'REJECTED', 'NEEDS_MANUAL'].includes(task.status)
        ? '<button id="replan-task" class="secondary">重新调查与生成方案</button>'
        : ''}
    </div>
    ${modern && task.status === 'WAITING_APPROVAL' && role === 'supervisor'
      ? `<details class="simulation-panel"><summary>审批过期实验 · 模拟资源变化</summary><p>改变一项业务事实，随后审批将触发过期拦截；此操作不代表真实 ERP 调整。</p><select id="resource-kind" aria-label="模拟变化的资源">
      ${context.warehouses?.length ? '<option value="warehouse">外仓库存减少 1 件</option><option value="route">运输单价增加 1 分</option>' : ''}
      ${context.purchase_batches?.length ? '<option value="batch">采购批次取消</option>' : ''}
      <option value="policy">切换客户分批政策</option><option value="local">本仓库存减少 1 件</option></select><button id="simulate-resource" class="secondary">应用模拟变化</button></details>`
      : ''}
    ${renderDecisionTrace(task)} ${renderRecovery(task)}
    <div class="timeline">
      <div class="section-label">处理记录<span>持久保存 · 可追溯</span></div>
      <div id="events"><span class="task-subtitle">正在读取…</span></div>
    </div>`;
}
