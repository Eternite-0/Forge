// Synthetic read-only Core receipts, rendered through the real frontend.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [], nativeReads = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const scope = { conversation_id: 'main', workspace_id: 'artifact-space', project_id: 'artifact-project' };
  const stamp = new Date(Date.now() - 60000).toISOString(), sha = 'a'.repeat(64);
  const run = id => ({ run_id: id, worker_id: `worker-${id}`, quest_id: 'artifact-quest', role: `代理 ${id}`, task_title: '历史文件验收', task_objective: `核对 ${id} 的历史文件`, state: 'succeeded', execution_mode: 'mock', lease_status: 'none', delegation_state: 'authorized', parent_message_id: 'artifact-user', source: { kind: 'message', id: 'artifact-user' }, outcome: { kind: 'verified', label: '' }, milestones: [], model_id: '模拟模型', created_at: stamp });
  const item = id => ({ receipt_id: `receipt-${id}`, path: `docs/${id}.md`, sha256: sha, bytes: 40, created_at: stamp, execution_mode: 'mock', change: 'revised' });
  let listMode = 'valid', mismatch = '', delayList = false, delayDiff = false;
  const releases = [];
  const listCount = () => reads.filter(path => path.endsWith('/artifacts')).length;
  const diffCount = () => reads.filter(path => path.endsWith('/diff')).length;
  await page.addInitScript(() => { window.isTauri = true; window.__artifactNativeCalls = []; window.__TAURI_INTERNALS__ = { invoke: async command => {
    window.__artifactNativeCalls.push(command);
    if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-artifacts', workspaceId: 'artifact-space', generation: 'artifact-gen' };
    if (command === 'runtime_status') return { state: 'ready', generation: 'artifact-gen' };
    throw Error('OPERATION_NOT_FOUND');
  } }; });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const r = route.request(), u = new URL(r.url()), headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (r.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (r.method() !== 'GET') { writes.push(u.pathname); return route.fulfill({ status: 405, headers, json: {} }); }
    reads.push(u.pathname); let json, status = 200, delayed = false;
    if (u.pathname === '/api/conversations/main/subagents') json = { ...scope, phase: u.searchParams.get('phase'), items: u.searchParams.get('phase') === 'active' ? [] : [run('A'), run('B')], has_more: false, next_before: null };
    else if (/\/subagents\/[AB]\/artifacts$/.test(u.pathname)) {
      const id = u.pathname.split('/').at(-2);
      json = { ...scope, run_id: id, execution_mode: 'mock', provenance: 'historical_receipts', current_files_verified: false, items: listMode === 'empty' || listMode === 'unavailable' ? [] : [item(id)], has_more: false, next_before: null, unavailable: listMode === 'unavailable' };
      if (listMode === 'wrongscope') json.workspace_id = 'other-workspace';
      if (listMode === 'malformed') json.items[0].bytes = '40';
      if (id === 'A' && delayList) { delayed = true; await new Promise(resolve => releases.push(resolve)); }
    } else if (/\/subagents\/[AB]$/.test(u.pathname)) json = { ...scope, item: run(u.pathname.split('/').pop()) };
    else if (/\/api\/artifacts\/receipts\/receipt-[AB]\/diff$/.test(u.pathname)) {
      const id = u.pathname.split('/').at(-2).slice(-1);
      json = { receipt_id: `receipt-${id}`, base_receipt_id: `base-${id}`, quest_id: 'artifact-quest', run_id: id, path: `docs/${id}.md`, execution_mode: 'mock', change: 'revised', created_at: stamp, sha256: sha, base_sha256: 'b'.repeat(64), added: 1, removed: 1, text: `--- a/docs/${id}.md\n+++ b/docs/${id}.md\n@@ -1 +1 @@\n-OLD ${id}\n+HISTORICAL ${id} ONLY\n`, truncated: false, origin: null };
      if (mismatch) json[mismatch] = { receipt_id: 'wrong-receipt', run_id: 'other-run', path: 'wrong/path.md', sha256: 'c'.repeat(64), execution_mode: 'live' }[mismatch];
      if (id === 'A' && delayDiff) { delayed = true; await new Promise(resolve => releases.push(resolve)); }
    } else if (u.pathname === '/health') json = { session: 'artifact-gen' };
    else if (u.pathname === '/api/conversation') json = { messages: [{ id: 'artifact-user', role: 'user', content: '查看子智能体留下的历史文件。', created_at: stamp }], router: {}, has_more: false };
    else if (u.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: scope.workspace_id, workspace_root: 'C:/Fixture/Artifacts', provider_configured: true, router_model_id: '模拟展示' };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(u.pathname)) json = { items: [], has_more: false };
    else if (u.pathname === '/api/restore-status') json = { fenced: false };
    else if (u.pathname === '/api/runtime/health') json = { components: [] };
    else if (['/api/projects/discovered', '/api/models'].includes(u.pathname)) json = [];
    else if (u.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { unknown.push(u.pathname); status = 404; json = {}; }
    try { await route.fulfill({ status, headers, json }); } catch (error) { if (!delayed) throw error; }
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(process.env.FORGE_TEST_URL || 'http://127.0.0.1:5205/');
  await page.locator('.conversation-execution-details > summary').click();
  await page.getByRole('button', { name: /本对话子智能体 · 2/ }).click();
  const row = id => page.locator('.conversation-agent-row').filter({ hasText: `核对 ${id} 的历史文件` });
  const panel = page.locator('.conversation-agent-detail'), files = page.getByRole('region', { name: '子智能体历史文件' }), history = page.getByRole('region', { name: '文件历史更改' });
  const back = page.getByRole('button', { name: '返回对话', exact: true }), draft = page.getByLabel('消息内容', { exact: true });
  const file = id => files.getByRole('button', { name: new RegExp(`docs/${id}\\.md`) });
  const open = async id => { await row(id).click(); await panel.getByText(`核对 ${id} 的历史文件`, { exact: true }).waitFor(); };
  const expand = () => files.getByRole('button', { name: /^查看返回文件/ }).click();
  await draft.fill('保留草稿，不要发送'); await open('A'); await page.waitForTimeout(6500);
  check(listCount() === 0 && diffCount() === 0, 'ordinary agent polling and initial detail do not read historical files');
  await expand(); await file('A').waitFor();
  check((await files.innerText()).includes('执行时保存的版本') && (await file('A').innerText()).includes('模拟记录'), 'history provenance and mock mode are explicit');
  const beforePolling = listCount(); await page.waitForTimeout(6500);
  check(listCount() === beforePolling && diffCount() === 0, 'expanded historical list is on-demand, not polled or eagerly diff-read');
  await file('A').focus(); await page.keyboard.press('Enter');
  await history.getByText('HISTORICAL A ONLY', { exact: false }).waitFor();
  check((await history.innerText()).includes('OLD A') && (await history.innerText()).includes('模拟记录'), 'exact immutable receipt renders actual old/new text and mock marker');
  await history.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${outputDir}/agent-artifact-history-desktop.png`});
  check(await files.getByRole('button', { name: '返回历史文件', exact: true }).evaluate(e => document.activeElement === e), 'opening historical document focuses its own return control');
  await page.keyboard.press('Escape'); await history.waitFor({ state: 'hidden' });
  check(await panel.isVisible() && await file('A').evaluate(e => document.activeElement === e), 'first Escape returns to same agent historical file with origin focus');
  await page.keyboard.press('Escape'); await panel.waitFor({ state: 'hidden' });
  check(await row('A').evaluate(e => document.activeElement === e) && await draft.inputValue() === '保留草稿，不要发送', 'second Escape returns chat and preserves draft/focus');
  await open('A'); await expand(); await file('A').click(); await history.getByText('HISTORICAL A ONLY', { exact: false }).waitFor();
  for (const field of ['receipt_id', 'run_id', 'path', 'sha256', 'execution_mode']) {
    mismatch = field; await history.getByRole('button', { name: '重新读取记录的更改', exact: true }).click(); await history.getByRole('alert').waitFor();
    check(!await history.locator('.artifact-history-source').isVisible(), `${field} mismatch hides all prior document content`);
  }
  mismatch = ''; await files.getByRole('button', { name: '返回历史文件', exact: true }).click();
  for (const mode of ['empty', 'unavailable', 'wrongscope', 'malformed']) {
    listMode = mode;
    if (await files.getByRole('button', { name: '刷新历史文件', exact: true }).count()) await files.getByRole('button', { name: '刷新历史文件', exact: true }).click();
    else await files.getByRole('button', { name: '重试', exact: true }).click();
    const label = mode === 'empty' ? '没有符合条件的历史文件回执。' : mode === 'unavailable' ? '部分历史回执不可用，仅显示已核对的记录。' : '历史文件暂不可用，不能据此判断没有文件。';
    await files.getByText(label, { exact: false }).waitFor();
    check(await file('A').count() === 0, `${mode}: no stale or unvalidated historical file is exposed`);
  }
  listMode = 'valid'; await back.click(); await open('A'); delayList = true; await expand();
  for (let i = 0; i < 50 && !releases.length; i++) await page.waitForTimeout(20);
  check(releases.length === 1, 'A historical list is genuinely pending');
  await back.click(); await open('B'); await expand(); await file('B').waitFor();
  delayList = false; releases.splice(0).forEach(release => release()); await page.waitForTimeout(250);
  check(await file('A').count() === 0 && await file('B').count() === 1, 'late prior-agent list cannot cross into B');
  await back.click(); await open('A'); await expand(); await file('A').waitFor(); delayDiff = true; await file('A').click();
  for (let i = 0; i < 50 && !releases.length; i++) await page.waitForTimeout(20);
  check(releases.length === 1, 'A receipt diff is genuinely pending');
  await back.click(); await open('B'); await expand(); await file('B').click(); await history.getByText('HISTORICAL B ONLY', { exact: false }).waitFor();
  delayDiff = false; releases.splice(0).forEach(release => release()); await page.waitForTimeout(250);
  check(!(await history.innerText()).includes('HISTORICAL A ONLY'), 'late prior-agent diff cannot replace B content');
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await page.waitForTimeout(100);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}: nested historical document stays in narrow viewport`);
    await files.getByRole('button', { name: '返回历史文件', exact: true }).focus(); await page.keyboard.press('Escape'); await history.waitFor({ state: 'hidden' });
    check(await file('B').evaluate(e => document.activeElement === e), `${width}: nested Escape restores historical row focus`);
    await page.keyboard.press('Escape'); await panel.waitFor({ state: 'hidden' });
    check(await draft.inputValue() === '保留草稿，不要发送', `${width}: agent return preserves unsent draft`);
    await open('B'); await expand(); await file('B').click(); await history.getByText('HISTORICAL B ONLY', { exact: false }).waitFor();
    await history.scrollIntoViewIfNeeded(); await page.screenshot({ path: `${outputDir}/agent-artifacts-${width}.png` });
  }
  const calls = await page.evaluate(() => window.__artifactNativeCalls);
  nativeReads.push(...calls.filter(command => command === 'workspace_file_read'));
  check(!nativeReads.length, 'historical receipt inspection never substitutes current disk file reads');
  check(!writes.length && !unknown.length, 'artifact UI sends no mutation or unknown API requests');
  return { checks, reads, evidence: 'Synthetic immutable receipt HTTP; no live provider/tools or current-file claims.' };
}
