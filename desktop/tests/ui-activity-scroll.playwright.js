// Independently delayed read-only activity snapshots must retain the conversation viewport.
async (page) => {
  const messages = Array.from({ length: 40 }, (_, index) => ({
    id: `reading-${index}`, role: index === 0 ? 'user' : 'assistant',
    content: `Reading marker ${index}\n\n${'A paragraph in the conversation. '.repeat(10)}`,
    created_at: `2026-01-01T00:${String(index).padStart(2, '0')}:00Z`,
  }));
  let runs = [], activityReads = 0;
  const unexpected = [];
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body, status = 200;
    if (request.method() !== 'GET') { unexpected.push(`${request.method()} ${url.pathname}`); status = 400; body = {}; }
    else if (url.pathname === '/health') body = { session: 'activity-reading-fixture' };
    else if (url.pathname === '/api/conversation') body = { messages, router: { router_session_id: 'fixture' }, has_more: false };
    else if (['/api/quests', '/api/approvals/pending', '/api/timeline'].includes(url.pathname)) body = { items: [], has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: 'fixture', workspace_root: 'D:/Fixture', router_model_id: 'fixture', provider_id: 'fixture', provider_configured: true, restore_fenced: false };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered' || url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/activity') body = { items: runs, has_more: false };
    else if (url.pathname === '/api/conversations/main/subagents') {
      activityReads += 1;
      // Separate activity commits from the synchronous conversation polling commit.
      if (activityReads > 1) await new Promise((resolve) => setTimeout(resolve, 1200));
      body = { conversation_id: 'main', workspace_id: 'fixture', items: runs.map(run => ({ ...run, quest_id: 'delegated', task_title: 'Reading review', task_objective: run.role, source: { kind: 'message', id: 'reading-0' }, lease_status: 'active', execution_mode: 'mock', delegation_state: 'active', outcome: { kind: run.state, label: '' }, milestones: [] })), has_more: false, next_before: null };
    } else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/budget') body = { token_limit: 10000, concurrency_limit: 2 };
    else { unexpected.push(url.pathname); status = 404; body = {}; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(globalThis.process.env.FORGE_UI_BASE_URL || 'http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const marker = page.locator('[data-scroll-key="message:reading-20"]');
  await marker.waitFor();
  await page.waitForTimeout(100);
  const offset = () => marker.evaluate((element) => element.getBoundingClientRect().top - document.querySelector('.timeline').getBoundingClientRect().top);
  await marker.evaluate((element) => {
    const timeline = document.querySelector('.timeline');
    timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top - 60;
  });
  await page.waitForTimeout(100);
  const initial = await offset();
  runs = [0, 1, 2].map((index) => ({ run_id: `early-${index}`, worker_id: `worker-${index}`, role: `Review ${index}`, state: 'running', created_at: `2026-01-01T00:0${index}:30Z`, steps: 2, tool_spent: 1 }));
  await page.getByRole('button', { name: /本对话子智能体 · 3/ }).waitFor();
  if (await page.locator('.agent-activity').count()) throw new Error('Delegates without recorded lifecycle must not invent process snapshots.');
  await page.waitForTimeout(100);
  const afterInsertion = await offset();
  if (Math.abs(afterInsertion - initial) > 1) throw new Error(`Activity insertion moved the reading marker: ${initial} → ${afterInsertion}`);
  runs = runs.map((run) => ({ ...run, state: 'succeeded' }));
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('3 执行成功'), null, { timeout: 11000 });
  await page.waitForTimeout(100);
  const afterCollapse = await offset();
  if (Math.abs(afterCollapse - initial) > 1) throw new Error(`Activity completion collapse moved the reading marker: ${initial} → ${afterCollapse}`);
  await page.locator('.timeline').evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await page.waitForTimeout(100);
  runs = [...runs, { run_id: 'late', worker_id: 'worker-late', role: 'Latest review', state: 'running', created_at: '2026-01-01T02:00:00Z', steps: 1, tool_spent: 0 }];
  await page.waitForFunction(() => document.querySelector('.conversation-agents-toggle')?.textContent.includes('本对话子智能体 · 4'), null, { timeout: 11000 });
  if (await page.locator('.agent-activity').count()) throw new Error('New scoped delegate cannot manufacture historical work.');
  await page.waitForTimeout(100);
  const bottomDistance = await page.locator('.timeline').evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop);
  if (bottomDistance > 1) throw new Error(`New activity stopped following the latest content: ${bottomDistance}`);
  if (unexpected.length) throw new Error(`Unexpected requests: ${unexpected.join(', ')}`);
  return { status: 'passed', initial, afterInsertion, afterCollapse, bottomDistance, activityReads };
}
