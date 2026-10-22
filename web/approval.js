import { escapeHtml as e } from './format.js';

export function renderApprovalSummary(task, candidateId) {
  const state = task.state;
  const modern = state.decision_protocol === 'v2';
  const candidate = (modern ? state.adaptive_proposal : state.proposal).candidates.find(
    (row) => row.id === candidateId,
  );
  if (!candidate) return '<p>当前候选已不可用，请刷新任务。</p>';
  const actions = modern
    ? [
        `使用本仓 ${candidate.local_quantity} 件`,
        ...candidate.transfers.map((row) => `从 ${row.warehouse_id} 调拨 ${row.quantity} 件`),
        ...candidate.purchase_allocations.map(
          (row) => `使用采购批次 ${row.batch_id} 的 ${row.quantity} 件`,
        ),
        ...candidate.deliveries.map((row) => `${row.delivery_date} 计划交付 ${row.quantity} 件`),
      ]
    : [
        `本仓 ${candidate.local_quantity} 件，调拨 ${candidate.transfer_quantity} 件，采购 ${candidate.purchase_quantity} 件`,
        `预计第 ${candidate.completion_days} 天资源齐备${candidate.split_delivery ? '；分批交付' : '；整单交付'}`,
      ];
  const binding = task.approval_context?.[candidateId];
  const cost = state.validation?.metrics?.[candidateId]?.transfer_cost_cents;
  return `<div class="approval-summary"><h3>${e(candidate.title)}</h3><ul>${actions.map((text) => `<li>${e(text)}</li>`).join('')}</ul>
    ${cost == null ? '' : `<p>程序核算调拨费：${(cost / 100).toFixed(2)} 元</p>`}
    <p class="risk">风险：${candidate.risks.map(e).join('；')}</p>
    <small>本次只修改订单交付计划，后续实物履约仍由业务系统完成。</small>
    ${binding ? '<p class="task-subtitle">审批已绑定当前方案、证据与资源快照；任一事实变化将重新复核。</p>' : ''}</div>`;
}

export function renderRecovery(task) {
  const state = task.state || {};
  const execution = state.execution;
  const approval = state.approval;
  return `<details class="recovery-panel"><summary>执行与恢复记录</summary>
    <p>${task.checkpoint_id ? '已保存图执行进度。' : '尚未保存图执行进度。'}${execution ? '业务操作结果已返回；最终状态以结果验证为准。' : '尚未记录业务执行结果；可恢复任务需先对账，不能据此认定没有发生修改。'}</p>
    <dl><dt>Checkpoint</dt><dd>${e(task.checkpoint_id || '—')}</dd><dt>待继续节点</dt><dd>${e(task.next_nodes.join('、') || '无')}</dd>
    <dt>操作 ID</dt><dd>${e(execution?.operation_id || '—')}</dd><dt>业务重放</dt><dd>${execution ? (execution.replayed ? '已复用同一操作的提交结果' : '首次返回') : '—'}</dd>
    <dt>审批版本 / 决定</dt><dd>${approval ? `v${approval.proposal_version} / ${approval.decision === 'approve' ? '批准' : '拒绝'}` : '—'}</dd>
    <dt>批准方案摘要</dt><dd>${e(approval?.plan_digest || 'v1 演示协议或尚未审批')}</dd></dl>
    <small>图恢复由 LangGraph Checkpointer 提供；业务副作用由项目的事务和操作账本保护。当前采用显式恢复。</small></details>`;
}
