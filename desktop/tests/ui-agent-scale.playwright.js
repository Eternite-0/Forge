// Synthetic read-only scoped Core. Exercises the real frontend, not real Workers.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5203/';
  const scope = { conversation_id: 'main', workspace_id: 'scale-workspace', project_id: 'scale-project' };
  const make = (id, state, created_at = '2026-11-17T00:00:00Z') => ({ run_id: id, worker_id: `worker-${id}`, quest_id: 'scale-quest', role: id, task_title: '并行验收', task_objective: `${id} 的独立委派任务`, state, execution_mode: 'mock', lease_status: state === 'running' ? 'active' : 'none', delegation_state: 'authorized', parent_message_id: 'scale-user', source: { kind: 'message', id: 'scale-user' }, outcome: { kind: 'pending', label: '模拟记录' }, milestones: [], model_id: '模拟模型', created_at });
  let a = make('active-A', 'running', '2020-01-01T00:00:00Z'), b = make('active-B', 'running');
  const terminal = Array.from({ length: 101 }, (_, i) => make(`terminal-${String(i).padStart(3, '0')}`, 'succeeded'));
  let activeWrong = false, terminalFail = false, wrongWorkspace = false, terminalFailures = 0, extraActive = null, homeMode = false, triggerMode = false;
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
      if (phase === 'active') json = { ...scope, workspace_id: wrongWorkspace ? 'other-workspace' : scope.workspace_id, phase: activeWrong ? 'terminal' : 'active', items: (homeMode ? (triggerMode ? [{ ...make('trigger-agent', 'running'), parent_message_id: null, source: { kind: 'trigger', id: 'registered-trigger' } }] : []) : [a, b, ...(extraActive ? [extraActive] : [])]).filter(x => ['running', 'review_pending'].includes(x.state)), next_before: null, has_more: false };
      else if (phase === 'terminal') {
        if (terminalFail) { terminalFailures++; status = 503; json = { detail: 'synthetic terminal unavailable' }; }
        else { const history = homeMode ? [] : terminal; const start = before ? Number(before) : 0; json = { ...scope, phase, items: history.slice(start, start + 50), next_before: start + 50 < history.length ? String(start + 50) : null, has_more: start + 50 < history.length }; }
      } else { unknown.push('missing phase'); status = 422; json = {}; }
    } else if (u.pathname.startsWith('/api/conversations/main/subagents/')) {
      const id = u.pathname.split('/').pop(); json = { ...scope, item: id === a.run_id ? a : id === b.run_id ? b : id === 'trigger-agent' ? { ...make('trigger-agent', 'running'), parent_message_id: null, source: { kind: 'trigger', id: 'registered-trigger' } } : terminal.find(x => x.run_id === id) };
    } else if (u.pathname === '/health') json = { session: 'scale-gen' };
    else if (u.pathname === '/api/conversation') json = { messages: homeMode ? [] : [{ id: 'scale-user', role: 'user', content: '检查活动与历史子智能体分页。', created_at: '2026-11-17T00:00:00Z' }], router: {}, has_more: false };
    else if (u.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: scope.workspace_id, workspace_root: 'C:/Fixture/Scale', provider_configured: true, router_model_id: '模拟展示' };
    else if (u.pathname === '/api/activity' && homeMode) json = { items: [make('unrelated-global-home', 'running')], has_more: false };
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
  if (!process.env.FORGE_AGENT_SCALE_HOME_ONLY) {
  const group = page.getByRole('region', { name: '本对话子智能体' });
  const rowA = page.locator('.conversation-agent-row').filter({ hasText: 'active-A 的独立委派任务' });
  const rowB = page.locator('.conversation-agent-row').filter({ hasText: 'active-B 的独立委派任务' });
  check(await rowA.count() === 1 && await rowB.count() === 1, 'old active run remains visible despite 101 newer terminal runs');
  check(reads.some(x => x.phase === 'active') && reads.some(x => x.phase === 'terminal'), 'active and terminal heads are separate actual HTTP reads');
  const older = page.getByRole('button', { name: '加载更早的子智能体', exact: true });
  await older.click(); await page.waitForFunction(() => document.querySelectorAll('.conversation-agent-row').length === 102);
  await older.click(); await page.waitForFunction(() => document.querySelectorAll('.conversation-agent-row').length === 103);
  check(!await older.isVisible(), 'exhausting explicit older pages hides pagination');
  const olderReads = () => reads.filter(x => x.phase === 'terminal' && x.before).length;
  const historyReadCount = olderReads();
  const originalA = await rowA.elementHandle(), originalB = await rowB.elementHandle();
  await rowB.focus(); await page.keyboard.press('Enter');
  const panel = page.locator('.conversation-agent-detail'), back = page.getByRole('button', { name: '返回执行列表', exact: true });
  await panel.getByText('active-B 的独立委派任务', { exact: true }).waitFor();
  a = { ...a, state: 'succeeded', lease_status: 'none' };
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.conversation-agent-row')).find(e => e.textContent.includes('active-A 的独立委派任务'))?.querySelector('small')?.dataset.state === 'succeeded', null, { timeout: 16000 });
  check(await panel.getByText('active-B 的独立委派任务', { exact: true }).isVisible() && await back.evaluate(e => document.activeElement === e), 'B detail identity and focus survive A settling');
  check(await originalA.evaluate(e => e.isConnected) && await originalB.evaluate(e => e.isConnected), 'settlement preserves exact flat DOM row identities');
  check(reads.some(x => x.path.endsWith('/active-A')), 'missing active run is reconciled by exact detail rather than silently removed');
  await back.click(); await panel.waitFor({ state: 'hidden' });
  check(await rowB.evaluate(e => document.activeElement === e), 'Back returns original B row focus');
  const priorHeads = reads.filter(x => x.phase === 'terminal' && !x.before).length;
  await page.waitForFunction(() => true);
  for (let i = 0; i < 50 && reads.filter(x => x.phase === 'terminal' && !x.before).length === priorHeads; i++) await page.waitForTimeout(500);
  check(reads.filter(x => x.phase === 'terminal' && !x.before).length > priorHeads, 'terminal head refresh occurs independently');
  await page.waitForTimeout(400);
  check(olderReads() === historyReadCount, 'periodic refresh does not refetch loaded historical pages');
  check(!await older.isVisible(), 'exhausted history is not resurrected by terminal head refresh');
  terminalFail = true;
  await rowB.focus();
  for (let i = 0; i < 60 && !terminalFailures; i++) await page.waitForTimeout(500);
  check(terminalFailures > 0, 'terminal-head failure was actually exercised');
  await group.getByText('已结束记录暂不可用；进行中记录仍独立更新。', { exact: false }).waitFor();
  await page.waitForTimeout(6500);
  check(await group.getByText('已结束记录暂不可用；进行中记录仍独立更新。', { exact: false }).isVisible(), 'terminal error persists across independent active refresh');
  check(await rowB.locator('small').getAttribute('data-state') === 'running', 'terminal failure does not contaminate fresh active state');
  activeWrong = true;
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.conversation-agent-row')).find(e => e.textContent.includes('active-B 的独立委派任务'))?.querySelector('small')?.dataset.state === 'unknown', null, { timeout: 16000 });
  check(await rowB.locator('small').getAttribute('data-state') === 'unknown', 'wrong-phase response fails closed to unknown, not stale running');
  activeWrong = false; terminalFail = false; wrongWorkspace = true;
  await group.getByRole('button', { name: '重新读取', exact: true }).click(); await page.waitForTimeout(300);
  check(await rowB.locator('small').getAttribute('data-state') === 'unknown', 'wrong-workspace response cannot restore trusted running state');
  wrongWorkspace = false;
  await group.getByRole('button', { name: '重新读取', exact: true }).click();
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.conversation-agent-row')).find(e => e.textContent.includes('active-B 的独立委派任务'))?.querySelector('small')?.dataset.state === 'running');
  await rowB.focus();
  b = { ...b, state: 'succeeded', lease_status: 'none' };
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.conversation-agent-row')).find(e => e.textContent.includes('active-B 的独立委派任务'))?.querySelector('small')?.dataset.state === 'succeeded', null, { timeout: 16000 });
  check(await originalB.evaluate(e => e.isConnected && document.activeElement === e), 'focused row retains exact identity and focus when moving to ended group');
  extraActive = make('new-active-C', 'running');
  await page.waitForFunction(() => document.querySelector('.conversation-agent-row')?.textContent.includes('new-active-C 的独立委派任务'), null, { timeout: 16000 });
  check((await page.locator('.conversation-agent-row').first().innerText()).includes('new-active-C 的独立委派任务'), 'new active run is placed ahead of loaded terminal history');
  check(await originalB.evaluate(e => document.activeElement === e), 'inserting new active rows does not steal existing focus');
  await page.screenshot({ path: `${outputDir}/agent-scale-overview.png` });
  }
  homeMode = true; activeWrong = false; terminalFail = false; wrongWorkspace = false;
  await page.reload();
  await page.locator('.conversation-shell.home-state .empty-conversation').waitFor();
  await page.waitForTimeout(500);
  check(await page.locator('.conversation-shell.home-state').count() === 1 && await page.locator('.conversation-agent-row').count() === 0, 'empty scoped chat remains home despite unrelated global running worker');
  triggerMode = true;
  const triggerNavigation = page.getByRole('button', {name:'执行详情',exact:true});
  await triggerNavigation.waitFor({timeout:16000}); await triggerNavigation.click();
  check(await page.locator('.conversation-shell.home-state').count() === 0, 'genuine scoped trigger delegation exits empty home without fabricated chat messages');
  await page.locator('.conversation-agent-row').filter({ hasText: 'trigger-agent 的独立委派任务' }).click();
  await page.locator('.conversation-agent-technical > summary').click();
  await page.locator('.conversation-agent-detail').getByText('已登记触发事件', { exact: true }).waitFor();
  check(await page.locator('.conversation-agent-detail').getByText('trigger-agent 的独立委派任务', { exact: true }).isVisible(), 'trigger-only delegated run has accessible exact detail');
  check(!writes.length && !unknown.length, 'scale tests make no mutations or unknown API requests');
  return { checks, reads, evidence: 'Synthetic scoped data; no live worker execution claim.' };
}
