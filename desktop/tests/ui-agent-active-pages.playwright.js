// Synthetic read-only scoped Core. Exercises the real frontend, not real Workers.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5207/';
  const scope = { conversation_id: 'main', workspace_id: 'scale-workspace', project_id: 'scale-project' };
  const make = (id, state, created_at = '2026-11-17T00:00:00Z') => ({ run_id: id, worker_id: `worker-${id}`, quest_id: 'scale-quest', role: id, task_title: '并行验收', task_objective: `${id} 的独立委派任务`, state, execution_mode: 'mock', lease_status: state === 'running' ? 'active' : 'none', delegation_state: 'authorized', parent_message_id: 'scale-user', source: { kind: 'message', id: 'scale-user' }, outcome: { kind: 'pending', label: '模拟记录' }, milestones: [], model_id: '模拟模型', created_at });
  const agents = Array.from({ length: 53 }, (_, i) => make(`agent-${i}`, ['queued', 'blocked', 'review_pending'][i % 3]));
  let settled = false;
  await page.addInitScript(() => { window.isTauri = true; window.__TAURI_INTERNALS__ = { invoke: async command => {
    if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-scale', workspaceId: 'scale-workspace', generation: 'scale-gen' };
    if (command === 'runtime_status') return { state: 'ready', generation: 'scale-gen' };
    throw Error('OPERATION_NOT_FOUND');
  } }; });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const r = route.request(), u = new URL(r.url()), headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (r.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (r.method() !== 'GET') { writes.push(u.pathname); return route.fulfill({ status: 405, headers, json: {} }); }
    reads.push({ path: u.pathname, phase: u.searchParams.get('phase'), before: u.searchParams.get('before') });
    let json, status = 200;
    if (u.pathname === '/api/conversations/main/subagents') {
      const phase = u.searchParams.get('phase'), before = u.searchParams.get('before');
      if (phase === 'active') {
        const pool = agents.filter((_, i) => !settled || i !== 0);
        const start = before ? 50 : 0;
        json = { ...scope, phase, items: pool.slice(start, start + 50), next_before: start === 0 ? 'older' : null, has_more: start === 0 };
      } else if (phase === 'terminal') json = { ...scope, phase, items: [], next_before: null, has_more: false };
      else { unknown.push('missing phase'); status = 422; json = {}; }
    } else if (u.pathname.startsWith('/api/conversations/main/subagents/')) {
      const id = u.pathname.split('/').pop();
      await new Promise(resolve => setTimeout(resolve, 350));
      const item = agents.find(x => x.run_id === id);
      json = { ...scope, item: settled && id === 'agent-0' ? { ...item, state: 'succeeded' } : item };
    } else if (u.pathname === '/health') json = { session: 'scale-gen' };
    else if (u.pathname === '/api/conversation') json = { messages: [{ id: 'scale-user', role: 'user', content: '检查活动与历史子智能体分页。', created_at: '2026-11-17T00:00:00Z' }], router: {}, has_more: false };
    else if (u.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: scope.workspace_id, workspace_root: 'C:/Fixture/Scale', provider_configured: true, router_model_id: '模拟展示' };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(u.pathname)) json = { items: [], has_more: false };
    else if (u.pathname === '/api/restore-status') json = { fenced: false };
    else if (u.pathname === '/api/runtime/health') json = { components: [] };
    else if (['/api/projects/discovered', '/api/models'].includes(u.pathname)) json = [];
    else if (u.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { unknown.push(u.pathname); status = 404; json = {}; }
    await route.fulfill({ status, headers, json });
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(base);
  await page.getByRole('button', {name:'执行详情',exact:true}).click();
  const group = page.getByRole('region', { name: '本对话子智能体' });
  await page.getByRole('button', { name: '加载更多进行中与待处理', exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.conversation-agent-row').length === 53);
  check(await page.locator('.conversation-agent-row').count() === 53, 'all 53 genuine active rows loaded through explicit page 2');
  const detailReads = () => reads.filter(x => /subagents\/agent-/.test(x.path));
  const heads = () => reads.filter(x => x.phase === 'active' && !x.before).length;
  const initialHeads = heads();
  await page.evaluate(() => {
    window.__unknownRows = [];
    new MutationObserver(() => {
      for (const row of document.querySelectorAll('.conversation-agent-row')) {
        if (row.querySelector('small')?.dataset.state === 'unknown') window.__unknownRows.push(row.textContent);
      }
    }).observe(document.body, { subtree: true, attributes: true, childList: true });
  });
  for (let i = 0; i < 65 && heads() < initialHeads + 2; i++) await page.waitForTimeout(250);
  await page.waitForTimeout(600);
  check(heads() >= initialHeads + 2, 'two actual 6-second active-head polls exercised');
  const extraReads = detailReads().filter(x => ['agent-50','agent-51','agent-52'].some(id => x.path.endsWith('/' + id)));
  const unknownRows = await page.evaluate(() => window.__unknownRows);
  const metrics = { headPolls: heads() - initialHeads, pageOnlyDetailReads: extraReads.length, observedUnknownTransitions: unknownRows.length };
  if (process.env.FORGE_EXPECT_ACTIVE_PAGE_BUG === '1') {
    check(extraReads.length >= 6, 'baseline reproduces page-only detail reads on both head polls');
    check(unknownRows.length > 0, 'baseline reproduces false unknown transition');
  } else {
    check(extraReads.length === 0, 'partial head never causes repeated detail reads for page-only rows');
    check(unknownRows.length === 0, 'page-only rows retain last-confirmed state without false unknown');
  }
  for (let i = 50; i < 53; i++) {
    const row = page.locator('.conversation-agent-row').filter({ hasText: `agent-${i} 的独立委派任务` });
    check(await row.locator('small').getAttribute('data-state') === agents[i].state, `page-only ${agents[i].state} retains truthful last-confirmed status`);
    if (process.env.FORGE_EXPECT_ACTIVE_PAGE_BUG !== '1') check((await row.locator('small').innerText()).includes('上次确认'), 'off-head status explicitly disclosed as last confirmed');
  }
  check(reads.filter(x => x.phase === 'active' && x.before).length === 1, 'page 2 fetched only on explicit click');
  const rowA = page.locator('.conversation-agent-row').filter({ hasText: 'agent-0 的独立委派任务' });
  await rowA.focus(); const original = await rowA.elementHandle(); settled = true;
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.conversation-agent-row')).find(e => e.textContent.includes('agent-0 的独立委派任务'))?.querySelector('small')?.dataset.state === 'succeeded', null, { timeout: 15000 });
  check(detailReads().some(x => x.path.endsWith('/agent-0')), 'genuinely missing head row reconciles exact run detail');
  check(await original.evaluate(e => e.isConnected && document.activeElement === e), 'settling head row preserves exact DOM identity and keyboard focus');
  check(await rowA.locator('small').getAttribute('data-state') === 'succeeded', 'terminal reconciliation updates actual status');
  check(writes.length === 0 && unknown.length === 0, 'zero mutations or unknown API routes');
  return { checks, metrics, detailReads: detailReads() };
}
