// Synthetic HTTP only: no tools, providers, vaults or real approval records are touched.
async (page, { outputDir }) => {
  const checks = [], mutations = [], missing = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const quest = { id: 'quest-approval-failure', title: '审批故障回归', goal: '核对失败和重复提交', status: 'running', version: 3, priority: 3, plan_revision: 2 };
  const approval = { id: 'approval-failure', quest_id: quest.id, action_type: 'publish', risk: 'R2', resource_scope: 'https://fixture.invalid/publish', plan_revision: 2, parameter_digest: 'b'.repeat(64), status: 'PENDING', reason: '合成精确审批', expires_at: '2099-12-01T00:00:00Z' };
  const settings = { revision: 3, config: null, workspace_id: 'approval-failure', workspace_root: 'D:/Fixture/ApprovalFailure', provider_configured: true, router_model_id: 'fixture-model', restore_fenced: false };
  const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
  let detailReads = 0, failDetail = true, conflictDecision = true;
  page.on('dialog', dialog => dialog.accept());
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body, status = 200;
    if (method === 'POST') {
      const payload = JSON.parse(request.postData() || '{}'); mutations.push({ path, payload });
      if (path === '/api/approvals/approval-failure/decision') {
        await new Promise(resolve => setTimeout(resolve, 200));
        if (conflictDecision) { status = 409; body = { detail: 'synthetic approval revision conflict' }; }
        else { approval.status = payload.decision; body = approval; }
      } else { missing.push(`${method} ${path}`); status = 404; body = { detail: 'Unexpected synthetic mutation' }; }
    } else if (path === '/health') body = { session: 'approval-failure-generation' };
    else if (path === '/api/conversation') body = { messages: [{ id: 'failure-message', role: 'assistant', content: '审批失败回归仅使用合成记录。' }], router: {}, has_more: false };
    else if (path === '/api/quests') body = { items: [quest], has_more: false };
    else if (path === `/api/quests/${quest.id}`) body = { quest, workers: [] };
    else if (path.endsWith('/plan')) body = { plan_revision: 2, status: 'approved', digest: 'a'.repeat(64), nodes: [] };
    else if (path.endsWith('/grants') || path === '/api/projects/discovered' || path === '/api/models') body = [];
    else if (path === '/api/approvals/pending') body = { items: approval.status === 'PENDING' ? [approval] : [], has_more: false };
    else if (path === '/api/approvals') body = { items: [approval], has_more: false };
    else if (path === '/api/approvals/approval-failure') {
      detailReads++;
      if (failDetail) { status = 503; body = { detail: 'synthetic approval detail unavailable' }; }
      else body = approval;
    } else if (path === '/api/timeline' || path === '/api/activity') body = { items: [], has_more: false };
    else if (path === '/api/conversations/main/subagents') body = { conversation_id: 'main', workspace_id: settings.workspace_id, items: [], next_before: null, has_more: false };
    else if (path === '/api/settings') body = settings;
    else if (path === '/api/restore-status') body = { fenced: false };
    else if (path === '/api/runtime/health') body = { components: [] };
    else if (path === '/api/budget') body = { token_limit: 10000, concurrency_limit: 1 };
    else { missing.push(`${method} ${path}`); status = 404; body = { detail: 'Unknown synthetic endpoint' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('cd'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.from-agent').first().waitFor();
  await page.getByRole('button', { name: '后台任务', exact: true }).click();
  await page.getByRole('button', { name: /^审批故障回归，运行中/ }).click();
  await page.locator('.workspace-tabs').getByRole('button', { name: '审批中心', exact: true }).click();
  const item = page.locator('.approval-item').first();
  const decide = item.getByRole('button', { name: '批准', exact: true });
  const reject = item.getByRole('button', { name: '拒绝', exact: true });
  const revoke = item.getByRole('button', { name: '撤销审批', exact: true });
  const summary = item.locator('summary').filter({ hasText: '审批详情' });
  await summary.click();
  await item.getByText('synthetic approval detail unavailable', { exact: false }).waitFor();
  check(await decide.isDisabled() && await reject.isDisabled() && await revoke.isDisabled(), 'failed authoritative detail reads disable decisions and revocation despite a pending list row');
  await summary.click();
  check(await decide.isDisabled() && await revoke.isDisabled(), 'closing failed details cannot restore stale approval actions');
  check(mutations.length === 0, 'failed detail reads never submit a mutation');
  failDetail = false;
  await summary.click();
  await page.waitForFunction(() => !document.querySelector('.approval-item .button-row button')?.disabled);
  check(detailReads === 2 && await decide.isEnabled(), 'reopening details explicitly retries and restores actions only after a successful read');
  // Fail another explicit refresh, then exercise the visible retry action.
  await summary.click(); failDetail = true; await summary.click();
  await item.getByRole('button', { name: '重试读取审批详情', exact: true }).waitFor();
  failDetail = false;
  await item.getByRole('button', { name: '重试读取审批详情', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.approval-item .button-row button')?.disabled);
  check(detailReads === 4 && await decide.isEnabled(), 'the visible retry clears the read error and uses fresh authoritative detail');
  await decide.evaluate(element => { element.click(); element.click(); });
  await page.getByText('synthetic approval revision conflict', { exact: false }).first().waitFor();
  check(mutations.length === 1, 'rapid repeated decisions issue only one in-flight POST');
  check(await decide.isVisible() && !(await page.getByText('审批决定已记录', { exact: true }).count()), 'a 409 preserves the pending item and error without reporting a successful decision');
  check(mutations[0].payload.expected_digest === approval.parameter_digest && mutations[0].payload.expected_plan_revision === 2, 'the rejected request still binds the exact digest and plan revision');
  conflictDecision = false;
  await decide.evaluate(element => { element.click(); element.click(); });
  await page.getByText('审批决定已记录', { exact: true }).first().waitFor();
  await item.locator('strong').filter({ hasText: '已批准' }).waitFor();
  check(mutations.length === 2 && mutations.every(entry => entry.path === '/api/approvals/approval-failure/decision'), 'one deliberate retry succeeds with no automatic or duplicated mutations');
  check(missing.length === 0, `all fixture requests match existing endpoints: ${missing.join(', ')}`);
  await page.screenshot({ path: `${outputDir}/forge-approval-failure.png`, fullPage: true });
  return { status: 'passed', fixture: 'synthetic HTTP only', checks, detailReads, mutations };
}
