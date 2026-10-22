import { escapeHtml as e } from './format.js';

const actionNames = {
  investigate: '模型选择补充调查',
  propose: '模型提出资源组合',
  manual: '模型请求人工协商',
  invalid_output: '模型输出契约不合格',
  validation: '程序校验与反馈',
};

export function renderDecisionTrace(task) {
  const state = task.state || {};
  if (state.decision_protocol !== 'v2') return '';
  const history = state.decision_history || [];
  const calls = task.model_calls || [];
  const scripted = calls.some((call) => call.provider === 'scripted-test');
  const unknown = calls.some((call) => call.actual_input == null || call.actual_output == null);
  const input = calls.reduce((sum, call) => sum + (call.actual_input ?? call.reserved_input), 0);
  const output = calls.reduce((sum, call) => sum + (call.actual_output ?? call.reserved_output), 0);
  const resources = (state.adaptive_proposal?.candidates || [])
    .map((candidate) => {
      const cost = state.validation?.metrics?.[candidate.id]?.transfer_cost_cents;
      return /* HTML */ `<div class="decision-record">
        <strong>${e(candidate.title)} · ${e(candidate.delivery_mode)}</strong>
        <p>
          ${candidate.transfers
            .map((row) => `${e(row.warehouse_id)} 调拨 ${row.quantity} 件`)
            .join('；') || '未使用外仓调拨'}
        </p>
        <p>
          ${candidate.purchase_allocations
            .map((row) => `${e(row.batch_id)} 使用 ${row.quantity} 件`)
            .join('；') || '未使用采购批次'}
        </p>
        <p>
          ${candidate.deliveries
            .map((row) => `${e(row.delivery_date)} 交付 ${row.quantity} 件`)
            .join('；')}
        </p>
        <small
          >程序核算调拨费：${cost == null
            ? '尚未校验'
            : `${(cost / 100).toFixed(2)} 元`}；不代表全部履约成本。</small
        >
      </div>`;
    })
    .join('');
  const records = history
    .map(
      (step) =>
        /* HTML */ `<div class="decision-record">
          <strong
            >${e(actionNames[step.action] || step.action)}${step.turn == null
              ? ''
              : ` · 第 ${step.turn + 1} 次决策`}</strong
          >
          <p>${e(step.reason || '')}</p>
          ${(step.requests || [])
            .map(
              (request) =>
                `<p><code>${e(request.tool_name)}</code> ${e(JSON.stringify(request.arguments))} · ${e(request.purpose)}</p>`,
            )
            .join('')}
          ${(step.feedback || [])
            .map(
              (issue) =>
                `<p class="errors">${e(issue.code)} · ${e(issue.field)}：期望 ${e(issue.expected)}，实际 ${e(issue.actual)}</p>`,
            )
            .join('')}
          ${step.action === 'validation' && step.valid_ids?.length
            ? `<p>校验通过：${step.valid_ids.map(e).join('、')}</p>`
            : ''}
          ${step.replayed ? '<small>已复用持久化模型结果，未重复调用。</small>' : ''}
        </div>`,
    )
    .join('');
  const evidence = Object.values(state.evidence || {})
    .map(
      (record) =>
        /* HTML */ `<div class="decision-record">
          <strong>${e(record.id)}</strong>
          <p>${e(JSON.stringify(record.data))}</p>
          <small>订单版本 ${record.order_version} · 查询时间 ${e(record.queried_at)}</small>
        </div>`,
    )
    .join('');
  return /* HTML */ `<section class="decision-trace">
    <div class="notice">
      ${scripted
        ? '当前任务使用可控测试响应，不计入真实 LLM 验收。'
        : '任务配置为模型接口；实际请求及用量见调用记录，合法性与写入由程序控制。'}<br />服务：${e(
        state.model_configuration?.provider || '—',
      )}
      · 模型：${e(state.model_configuration?.model || '—')}
    </div>
    <div class="section-label">Agent 资源方案<span>模型建议 · 程序核算</span></div>
    ${resources}
    <div class="section-label">决策轨迹<span>${calls.length} 次模型请求尝试</span></div>
    <p class="task-subtitle">
      输入 ${input} / 输出 ${output}
      tokens${unknown ? '（含估算或结果未知的预留）' : '（供应商返回 usage）'}；不展示隐藏思维链。
    </p>
    ${records || '<p>尚无决策记录</p>'}
    <details>
      <summary>查看已调查证据 · ${Object.keys(state.evidence || {}).length} 条</summary>
      ${evidence}
    </details>
  </section>`;
}
