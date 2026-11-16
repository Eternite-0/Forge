// Stateful synthetic Core: validates UI payloads without executing tools or calling providers.
async (page, { outputDir }) => {
  const checks = [], calls = [], mutations = [], missing = [], recalls = [], eventCursors = [];
  const check = (ok, label) => { if (!ok) throw new Error(label); checks.push(label); };
  const quest = { id: 'quest-controls', title: '验证任务控件', goal: '保留真实权限、版本和预算', status: 'running', version: 3, priority: 3, plan_revision: 2 };
  const plan = { plan_revision: 2, status: 'approved', digest: 'a'.repeat(64), nodes: [] };
  const run = { run_id: 'run-controls', worker_id: 'worker-controls', quest_id: quest.id, role: '研究', state: 'running', steps: 2, tool_spent: 1, provider_id: 'fixture', model_id: 'fixture-model' };
  const teamBody = {
    roles: [{ id: 'research', task_node_key: 'research-node', required_capabilities: { research: 70 }, tools: ['file_read'], input_tokens: 100, output_tokens: 200, execution_token_budget: 300, max_steps: 4, max_tool_calls: 2, max_duration_seconds: 60, depth: 0, dynamic: false, responsibility: 'Read approved material', input_contract: ['approved node'], output_schema: { type: 'object' }, acceptance_criteria: ['cite sources'] }],
    limits: { max_members: 1, max_depth: 0, max_concurrency: 1, max_total_tokens: 300, max_total_tool_calls: 2, max_cost_microunits: null, currency: null, allowed_tools: ['file_read'] },
    members: [{ role_id: 'research', model_id: 'fixture-model', provider_id: 'fixture', estimated_tokens: 300, tool_scope: ['file_read'] }],
  };
  const approvals = [];
  const config = { provider: { id: 'fixture', kind: 'openai_compatible', base_url: 'https://provider.invalid/v1', credential_ref: 'FIXTURE_KEY', supports_tools: true, supports_json_schema: true, allow_insecure_loopback: false }, router_model_id: 'fixture-model', router_context_limit: 32768, model_token_limit: 34000, model_concurrency_limit: 3, network_hosts: [] };
  const settings = { revision: 3, config, workspace_id: 'controls-workspace', workspace_root: 'D:/Fixture/Controls', provider_id: 'fixture', provider_configured: true, router_model_id: 'fixture-model', restore_fenced: false };
  const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS' };
  let healthUnavailable = false;
  page.on('dialog', dialog => dialog.accept());
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    calls.push(`${method} ${path}`);
    let body, status = 200;
    if (method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}');
      mutations.push({ path, query: Object.fromEntries(url.searchParams), payload });
      if (path === '/api/conversation/message') body = { user_message: { id: 'sent-user', role: 'user', content: payload.content }, assistant_message: { id: 'sent-assistant', role: 'assistant', content: '已接收。' } };
      else if (path.endsWith('/priority')) { quest.priority = payload.priority; quest.version++; body = quest; }
      else if (path.endsWith('/plan/control')) { quest.status = payload.action === 'pause' ? 'paused' : 'running'; plan.status = payload.action === 'pause' ? 'paused' : 'approved'; quest.version++; body = plan; }
      else if (path.endsWith('/plan/generate')) body = plan;
      else if (path.endsWith('/teams/reassign')) body = { id: 'team-new', status: 'proposed' };
      else if (path.endsWith('/workers')) body = { id: 'worker-proposal', role: url.searchParams.get('role'), status: 'proposed' };
      else if (path.endsWith('/transition')) { quest.status = payload.status; quest.version++; body = quest; }
      else if (path === '/api/approvals') { const item = { ...payload, id: 'approval-controls', status: 'PENDING', parameter_digest: 'b'.repeat(64), plan_revision: 2 }; approvals.push(item); body = item; }
      else if (path.endsWith('/decision')) { approvals[0].status = payload.decision; body = approvals[0]; }
      else if (path.endsWith('/revoke') && path.startsWith('/api/approvals/')) { approvals[0].status = 'REVOKED'; body = approvals[0]; }
      else { missing.push(`${method} ${path}`); status = 404; body = { detail: 'No synthetic mutation' }; }
    } else if (path === '/health') body = { session: 'controls' };
    else if (path === '/api/conversation') body = { messages: [{ id: 'message-controls', role: 'assistant', content: '任务和历史引用可以在当前对话中核对。' }], router: {}, has_more: false };
    else if (path === '/api/quests') body = { items: [quest], has_more: false };
    else if (path === '/api/quests/quest-controls') body = { quest, workers: [{ id: 'worker-controls', role: '研究', model_id: 'fixture-model', status: 'proposed' }] };
    else if (path.endsWith('/plan')) body = plan;
    else if (path.endsWith('/plan/history')) body = [{ id: 'plan-old', revision: 1, status: 'superseded' }];
    else if (path.endsWith('/teams')) body = [{ id: 'team-controls', status: 'approved', plan_revision: 2, body: teamBody }];
    else if (path.endsWith('/grants')) body = [];
    else if (path === '/api/team/preview') body = [{ role_id: 'research', role_name: '研究', model_candidates: [{ provider: 'fixture', model_id: 'fixture-model', fit_score: 70 }] }];
    else if (path === '/api/activity') body = { items: [run], has_more: false };
    else if (path === '/api/timeline') body = { items: [], has_more: false };
    else if (path === '/api/approvals/pending') body = { items: approvals.filter(item => item.status === 'PENDING'), has_more: false };
    else if (path === '/api/approvals') body = { items: approvals, has_more: false };
    else if (path === '/api/approvals/approval-controls') body = approvals[0];
    else if (path === '/api/settings') body = settings;
    else if (path === '/api/restore-status') body = { fenced: false };
    else if (path === '/api/projects/discovered') body = [];
    else if (path === '/api/runtime/health') {
      if (healthUnavailable) { status = 503; body = { detail: 'fixture heartbeat unavailable' }; }
      else body = { components: [{ component: 'router', state: 'ok', last_seen: Date.now() / 1000, timeout_seconds: 60 }, { component: 'expired-worker', state: 'ok', last_seen: Date.now() / 1000 - 20, timeout_seconds: 10 }, { component: 'unreported-worker', state: 'ok', last_seen: null, timeout_seconds: 60 }] };
    } else if (path === '/api/recall') {
      const query = Object.fromEntries(url.searchParams); recalls.push(query);
      body = { ref: query.reference, kind: 'message', summary: { title: '历史记录' }, source_hash: 'c'.repeat(64), content: Number(query.offset) ? '第二段' : '第一段', offset: Number(query.offset), next_offset: Number(query.offset) ? 12 : 6, complete: Number(query.offset) > 0, relations: [{ ref: 'quest:quest-controls', relation: 'related_quest' }], relations_omitted: Number(query.relation_offset) === 0, next_relation_offset: Number(query.relation_offset) === 0 ? 1 : null };
    } else if (path === '/api/events') {
      eventCursors.push(url.searchParams.get('after'));
      body = Number(url.searchParams.get('after')) < 7 ? [{ id: 'event-controls', seq: 7, event_type: 'quest.updated', quest_id: quest.id, created_at: '2026-11-16T08:00:00Z', payload: { status: quest.status } }] : [];
    } else if (path === '/api/models') body = [{ id: 'profile-controls', provider: 'fixture', model_id: 'fixture-model', display_name: 'Fixture model', enabled: true }, { id: 'profile-alternate', provider: 'fixture', model_id: 'alternate-model', display_name: 'Alternate model', enabled: true }];
    else if (path === '/api/budget') body = { token_limit: 34000, concurrency_limit: 3 };
    else if (path === '/api/memory') body = { items: [], cursor: null };
    else { missing.push(`${method} ${path}`); status = 404; body = { detail: 'No synthetic fixture' }; }
    return route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('cd'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.from-agent').first().waitFor();
  await page.getByRole('button', { name: '查看模型配置', exact: true }).click();
  await page.getByRole('button', { name: 'Alternate model', exact: true }).click();
  await page.getByLabel('Router 模型 ID', { exact: true }).waitFor();
  check(await page.getByLabel('Router 模型 ID', { exact: true }).inputValue() === 'alternate-model' && settings.config.router_model_id === 'fixture-model', 'composer model selection prefills runtime settings without claiming an immediate switch');
  check(await page.getByText('重启 Core', { exact: false }).count() > 0, 'model configuration communicates its restart requirement');
  await page.locator('.settings-nav').getByRole('button', { name: '返回对话', exact: true }).click();
  check((await page.getByRole('button', { name: '查看模型配置', exact: true }).innerText()).includes('fixture-model'), 'returning to chat retains the actual active Router model');
  await page.getByRole('button', { name: '查看运行健康' }).click();
  await page.locator('[data-health="stale"]').waitFor();
  check(await page.locator('[data-health="healthy"]').count() === 1 && await page.locator('[data-health="unknown"]').count() === 1, 'health distinguishes fresh, expired and unknown Unix-second heartbeats');
  await page.keyboard.press('Escape');
  const recall = page.locator('.conversation-shell .recall-card').first();
  await recall.locator('summary').first().click();
  await recall.getByLabel('查询历史引用').fill('message:message-controls');
  await recall.getByRole('button', { name: '查询', exact: true }).click();
  await recall.getByRole('button', { name: '下一段内容' }).click();
  await recall.getByText('记录内容 · 字节 6–12', { exact: true }).waitFor();
  await recall.getByRole('button', { name: '更多关联' }).click();
  await page.waitForFunction(() => !document.querySelector('.conversation-shell .recall-card button')?.disabled);
  await recall.getByRole('button', { name: 'related_quest · quest:quest-controls' }).click();
  await recall.locator('.recall-result code').filter({ hasText: 'quest:quest-controls' }).waitFor();
  check(recalls.some(item => item.offset === '6') && recalls.some(item => item.relation_offset === '1') && recalls.some(item => item.reference === 'quest:quest-controls'), 'recall traverses real references and both bounded pagination cursors');
  await page.getByRole('button', { name: '后台任务', exact: true }).click();
  await page.getByRole('button', { name: /^验证任务控件 quest-controls/ }).click();
  const controls = page.locator('.quest-controls');
  await controls.locator('summary').first().click();
  await controls.getByText('版本 1 ·', { exact: false }).waitFor();
  await controls.getByLabel('任务优先级').selectOption('1');
  await page.waitForFunction(() => document.querySelector('.quest-phase')?.textContent.includes('版本 4'));
  await controls.getByRole('button', { name: '暂停计划', exact: true }).click();
  await controls.getByRole('button', { name: '恢复计划', exact: true }).click();
  await controls.getByRole('button', { name: '暂停计划', exact: true }).waitFor();
  await controls.getByLabel('保留已完成节点，重新规划剩余工作').check();
  await controls.getByLabel('展开节点（可选）').fill('research-node');
  await controls.getByRole('button', { name: '生成计划', exact: true }).click();
  await controls.getByLabel('沿用的团队合约').selectOption('team-controls');
  await controls.getByLabel('需要重新分配的执行').selectOption('run-controls');
  await controls.getByLabel('重新分配原因').fill('使用相同权限重新安排研究');
  await controls.getByRole('button', { name: '提出重新分配', exact: true }).click();
  const workers = controls.locator('details').filter({ has: page.locator('summary', { hasText: '子智能体提案' }) }).last();
  await workers.locator('summary').first().click();
  await workers.getByLabel('角色', { exact: true }).fill('核验');
  await workers.getByLabel('模型 ID（可选）').fill('fixture-model');
  await workers.getByRole('button', { name: '创建提案', exact: true }).click();
  await controls.getByLabel('调整任务状态').selectOption('needs_input');
  await page.waitForFunction(() => document.querySelector('.quest-phase')?.textContent.includes('版本 7'));
  const priority = mutations.find(item => item.path.endsWith('/priority'));
  check(priority.payload.expected_version === 3 && priority.payload.priority === 1, 'priority is bound to the current quest version');
  check(mutations.filter(item => item.path.endsWith('/plan/control')).map(item => `${item.payload.action}:${item.payload.expected_version}`).join('|') === 'pause:4|resume:5', 'pause and resume use the refreshed version and legal plan state');
  check(mutations.find(item => item.path.endsWith('/plan/generate')).payload.expansion_parent === 'research-node', 'remaining-DAG generation preserves the selected expansion node');
  const reassignment = mutations.find(item => item.path.endsWith('/teams/reassign')).payload;
  check(reassignment.expected_version === 6 && reassignment.run_id === run.run_id && JSON.stringify(reassignment.proposal.limits) === JSON.stringify(teamBody.limits) && reassignment.proposal.roles[0].tools.join() === 'file_read', 'team reassignment preserves run binding, recorded permissions and budget');
  check(mutations.find(item => item.path.endsWith('/workers')).query.role === '核验' && !calls.some(item => item.startsWith('GET') && item.endsWith('/workers')), 'worker proposals use the existing POST query contract');
  const draft = page.getByLabel('消息内容');
  await draft.fill('任务详情打开时仍可发送');
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('textarea[aria-label="消息内容"]')?.value === '');
  check(mutations.some(item => item.path === '/api/conversation/message'), 'task drawer leaves the real send action clickable');
  await page.locator('.workspace-tabs').getByRole('button', { name: '审批中心', exact: true }).click();
  await page.getByRole('button', { name: '创建审批请求', exact: true }).click();
  await page.getByLabel('操作范围', { exact: true }).fill('https://publish.invalid/review');
  await page.getByLabel('原因', { exact: true }).fill('核对明确的发布范围');
  await page.getByLabel('精确参数（JSON）').fill('{"amount":1e999}');
  await page.getByRole('button', { name: '提交审批请求', exact: true }).click();
  await page.getByText('参数中的数值必须是有限数，不能静默改写为 null。', { exact: true }).waitFor();
  check(!mutations.some(item => item.path === '/api/approvals'), 'exact approval parameters reject numbers that JSON serialization would silently rewrite');
  await page.getByLabel('精确参数（JSON）').fill('{"url":"https://publish.invalid/review","body":"fixture"}');
  await page.getByRole('button', { name: '提交审批请求', exact: true }).click();
  const approval = page.locator('.approval-item').first();
  await approval.locator('summary').filter({ hasText: '审批详情' }).click();
  await page.waitForFunction(() => document.querySelector('.approval-item details')?.textContent.includes('approval-controls'));
  await approval.getByRole('button', { name: '批准', exact: true }).click();
  await page.getByText('审批决定已记录', { exact: true }).waitFor();
  await approval.getByRole('button', { name: '撤销审批', exact: true }).click();
  await page.getByText('审批已撤销', { exact: true }).waitFor();
  const decision = mutations.find(item => item.path.endsWith('/decision')).payload;
  check(decision.expected_digest === 'b'.repeat(64) && decision.expected_plan_revision === 2, 'approval decision is bound to the exact digest and plan revision');
  check(calls.includes('GET /api/approvals/approval-controls') && mutations.some(item => item.path.endsWith('/approval-controls/revoke')), 'approval creation, authoritative detail and revocation are all reachable');
  await page.locator('.workspace-tabs').getByRole('button', { name: '运行活动', exact: true }).click();
  const feed = page.locator('.event-feed');
  await feed.locator('summary').first().click();
  await feed.getByText('quest.updated', { exact: true }).waitFor();
  await feed.getByRole('button', { name: '刷新事件', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.event-feed > button')?.disabled);
  check(eventCursors.join('|') === '0|7' && await feed.locator('.event-feed-list > details').count() === 1, 'event polling advances its sequence cursor without duplicating events');
  await page.screenshot({ path: `${outputDir}/forge-controls-activity.png`, fullPage: true });
  healthUnavailable = true;
  await page.getByRole('button', { name: '查看运行健康' }).click();
  await page.getByText('fixture heartbeat unavailable', { exact: false }).waitFor({ timeout: 10000 });
  check(await page.locator('[data-health]').count() === 0, 'failed health reads clear the previously displayed component states');
  check(missing.length === 0, `only existing contracts are requested: ${missing.join(', ')}`);
  return { status: 'passed', checks, mutations: mutations.map(item => ({ path: item.path, payload: item.payload, query: item.query })), calls: [...new Set(calls)] };
}
