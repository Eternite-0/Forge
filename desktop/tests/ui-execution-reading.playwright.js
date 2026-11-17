// Synthetic read-only scoped Core. Exercises the real frontend, not real Workers.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5207/';
  const scope = { conversation_id: 'main', workspace_id: 'scale-workspace', project_id: 'scale-project' };
  const make = (id, state, created_at = '2026-11-17T00:00:00Z') => ({ run_id: id, worker_id: `worker-${id}`, quest_id: 'scale-quest', role: id, task_title: '并行验收', task_objective: `${id} 的独立委派任务`, state, execution_mode: 'mock', lease_status: state === 'running' ? 'active' : 'none', delegation_state: 'authorized', parent_message_id: 'scale-user', source: { kind: 'message', id: 'scale-user' }, outcome: { kind: 'pending', label: '模拟记录' }, milestones: [], model_id: '模拟模型', created_at });
  const agents = Array.from({ length: 53 }, (_, i) => make(`agent-${i}`, ['queued', 'blocked', 'review_pending'][i % 3]));
  let settled = false, scenario = "children", holdDetail = false, releaseDetail = null;
  const messages = Array.from({ length: 60 }, (_, i) => ({ id: `reading-${i}`, role: i % 2 ? 'assistant' : 'user', content: `消息 ${i}\n\n` + '用于阅读位置验证的内容。'.repeat(35), created_at: new Date(Date.UTC(2026,10,17,0,0,i)).toISOString() }));
  const append = id => messages.push({ id, role: 'assistant', content: id + '\n\n' + '新到达消息内容。'.repeat(60), created_at: new Date(Date.UTC(2026,10,17,0,2,messages.length)).toISOString() });
  await page.addInitScript(() => { window.isTauri = true; window.__TAURI_INTERNALS__ = { invoke: async command => {
    if (command === 'connection_get' && window.__replacement) return window.__replacement;
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
        json = { ...scope, phase, items: scenario === "children" ? agents.slice(0, 2) : [], next_before: null, has_more: false };
      } else if (phase === 'terminal') json = { ...scope, phase, items: [], next_before: null, has_more: false };
      else { unknown.push('missing phase'); status = 422; json = {}; }
    } else if (u.pathname.startsWith('/api/conversations/main/subagents/')) {
      const id = u.pathname.split('/').pop();
      if (holdDetail) await new Promise(resolve => { releaseDetail = resolve; });
      await new Promise(resolve => setTimeout(resolve, 350));
      const item = agents.find(x => x.run_id === id);
      json = { ...scope, item: settled && id === 'agent-0' ? { ...item, state: 'succeeded' } : item };
    } else if (u.pathname === '/health') json = { session: 'scale-gen' };
    else if (u.pathname === '/api/conversation') json = { messages, router: {}, has_more: false };
    else if (u.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: scope.workspace_id, workspace_root: 'C:/Fixture/Scale', provider_configured: true, router_model_id: '模拟展示' };
    else if (u.pathname === '/api/approvals/pending') json = { items: scenario === 'approval' ? [{ id: 'approval-test', quest_id: 'fixture-quest', status: 'PENDING', action_type: 'file_write', resource_scope: 'work/result.txt', risk: 'medium', parameter_digest: 'a'.repeat(64), plan_revision: 1, reason: '需要确认写入', expires_at: '2099-01-01T00:00:00Z' }] : [], has_more: false };
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
  const draft = page.getByRole('textbox', { name: '消息内容' });
  const entry = page.getByRole('button', { name: '执行详情', exact: true });
  const screen = page.locator('.execution-screen');
  const timeline = page.locator('.conversation-shell .timeline');
  await entry.waitFor(); await draft.fill('阅读模式草稿');
  const metrics = () => timeline.evaluate(e => ({ top: e.scrollTop, bottom: e.scrollHeight - e.clientHeight - e.scrollTop, height: e.scrollHeight }));
  // Explicit user jump-to-latest establishes follow mode, not just a CSS position.
  await timeline.hover(); await page.mouse.wheel(0, -800); await page.waitForTimeout(250);
  await page.getByRole('button', { name: /回到最新/ }).click(); await page.waitForTimeout(300);
  check((await metrics()).bottom < 3, 'baseline starts following latest at actual bottom');
  const footerReservation = await page.locator('.conversation-shell').evaluate(e=>e.style.getPropertyValue('--conversation-footer-height'));
  await entry.click(); await screen.waitFor();
  append('while-follow-execution'); await timeline.getByText(/while-follow-execution/).waitFor({ state: 'attached', timeout: 15000 });
  if (process.env.FORGE_EXPECT_FOLLOW_BUG !== '1') check(await page.locator('.conversation-shell').evaluate(e=>e.style.getPropertyValue('--conversation-footer-height')) === footerReservation, 'hidden conversation preserves measured composer reservation');
  await screen.getByRole('button', { name: '← 返回对话', exact: true }).click(); await draft.waitFor();
  await page.waitForTimeout(350);
  const returnedFollowing = await metrics();
  if (process.env.FORGE_EXPECT_FOLLOW_BUG !== '1') check(returnedFollowing.bottom < 3 && !await page.getByRole('button', { name: /回到最新/ }).isVisible(), 'following Back includes message received while execution was open');
  append('after-follow-return');
  await timeline.getByText(/after-follow-return/).waitFor({ timeout: 15000 }); await page.waitForTimeout(500);
  const following = await metrics();
  const followIndicator = await page.getByRole('button', { name: /回到最新/ }).isVisible();
  if (process.env.FORGE_EXPECT_FOLLOW_BUG === '1') check(following.bottom > 50 && followIndicator, 'baseline reproduces lost following after Back and actual poll append');
  else check(following.bottom < 3 && !followIndicator, 'Back restores live following and new polled message stays at bottom');
  check(await draft.inputValue() === '阅读模式草稿', 'following return retains draft');
  await timeline.hover(); await page.mouse.wheel(0, -1100); await page.waitForTimeout(300);
  const paused = await metrics(); check(paused.bottom > 300, 'actual user wheel pauses older reading');
  // Focus without scrolling permits keyboard entry while preserving the paused viewport.
  await entry.evaluate(e => e.focus({ preventScroll: true })); await page.keyboard.press('Enter'); await screen.waitFor();
  append('while-paused-execution'); await timeline.getByText(/while-paused-execution/).waitFor({ state: 'attached', timeout: 15000 });
  await screen.getByRole('button', { name: '← 返回对话', exact: true }).click(); await draft.waitFor();
  check(await entry.evaluate(e => document.activeElement === e), 'Back restores entry focus');
  const returned = await metrics();
  check(Math.abs(returned.top - paused.top) < 3 && await page.getByRole('button', { name: /有新消息/ }).isVisible(), 'paused Back keeps old anchor and unread affordance after in-screen arrival');
  append('after-paused-return'); await timeline.getByText(/after-paused-return/).waitFor({ timeout: 15000 }); await page.waitForTimeout(500);
  const afterPaused = await metrics();
  check(Math.abs(returned.top - paused.top) < 3 && Math.abs(afterPaused.top - paused.top) < 3, 'paused older reading stays at exact offset through Back and append');
  check(await page.getByRole('button', { name: /回到最新/ }).isVisible(), 'paused reader retains explicit return-to-latest control');
  check(await draft.inputValue() === '阅读模式草稿', 'paused return retains draft');
  const heldAnchor = await timeline.evaluate(e => {
    const top = e.getBoundingClientRect().top, bottom = e.getBoundingClientRect().bottom;
    const rows = Array.from(e.querySelectorAll('[data-scroll-key]')).map(node => ({key:node.dataset.scrollKey, rect:node.getBoundingClientRect()})).filter(x => x.rect.height > 0 && x.rect.bottom > top && x.rect.top < bottom && !/^group(?:-files)?:/.test(x.key));
    rows.sort((a,b)=>Math.abs(a.rect.top-top)-Math.abs(b.rect.top-top));return {key:rows[0].key,offset:rows[0].rect.top-top};
  });
  await entry.evaluate(e => e.focus({ preventScroll: true })); await page.keyboard.press('Enter'); await screen.waitFor();
  // Screen is open while the older-reading snapshot is held.
  await page.setViewportSize({ width: 900, height: 600 });
  await page.waitForTimeout(400);
  await screen.getByRole('button', { name: '← 返回对话', exact: true }).click(); await draft.waitFor();
  const resizedAnchor = await timeline.evaluate((e, anchor) => {
    const node = Array.from(e.querySelectorAll('[data-scroll-key]')).find(n => n.dataset.scrollKey === anchor.key);
    return node ? { key: node.dataset.scrollKey, offset: node.getBoundingClientRect().top - e.getBoundingClientRect().top } : null;
  }, heldAnchor);
  check(resizedAnchor?.key === heldAnchor.key && Math.abs(resizedAnchor.offset - heldAnchor.offset) < 3, 'paused message identity and relative offset survive resizing inside execution screen');
  await entry.evaluate(e => e.focus({ preventScroll: true })); await page.keyboard.press('Enter'); await screen.waitFor();
  messages.splice(0, messages.length, { id: 'new-workspace-message', role: 'assistant', content: '新工作空间独立会话', created_at: '2026-11-17T00:05:00Z' });
  scope.workspace_id = 'new-workspace';
  await page.evaluate(() => { window.__replacement = { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'replacement', workspaceId: 'new-workspace', generation: 'replacement-generation' }; });
  await screen.waitFor({ state: 'hidden', timeout: 10000 });
  await timeline.getByText('新工作空间独立会话', { exact: true }).waitFor(); await page.waitForTimeout(350);
  check((await metrics()).top === 0, 'workspace replacement never receives old paused scroll offset');
  check(!await entry.evaluate(e => document.activeElement === e), 'workspace invalidation does not restore old execution origin focus');
  check(!writes.length && !unknown.length, 'no writes or unexpected API');
  return { checks, returnedFollowing, following, paused, returned, afterPaused, heldAnchor, resizedAnchor };
}
