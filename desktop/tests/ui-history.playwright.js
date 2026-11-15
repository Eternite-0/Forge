// History is synthetic authenticated HTTP; the independent current-file source uses actual File API bytes.
async (page, { outputDir }) => {
  const checks = [], requests = [], writes = [], missing = [], screenshots = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const digest = 'a'.repeat(64), baseDigest = 'b'.repeat(64);
  const definitions = [
    ['receipt_one', 'src/same.ts', 'file_write', 'live'],
    ['receipt_two', 'src/same.ts', 'file_revise', 'mock'],
    ['receipt_missing', 'gone.txt', 'file_revise', 'live'],
    ['receipt_invalid', 'invalid.txt', 'file_revise', 'live'],
    ['receipt_malformed', 'malformed.ts', 'file_revise', 'live'],
    ['receipt_pending', 'pending.ts', 'file_revise', 'live'],
  ];
  const events = definitions.map(([id, path, tool, mode], index) => ({ kind: 'tool_receipt', event_id: id, created_at: `2026-01-01T00:00:${String(index + 1).padStart(2, '0')}Z`, quest_id: null, run_id: 'run_history', title: 'Tool execution recorded', status: 'succeeded', execution_mode: mode, detail: { tool, path, result_sha256: digest } }));
  const messages = [
    { id: 'msg_original', role: 'user', content: 'ORIGINAL REQUEST: inspect these recorded changes.', created_at: '2026-01-01T00:00:00Z', metadata: {} },
    ...Array.from({ length: 18 }, (_, index) => ({ id: `msg_tail_${index}`, role: 'assistant', content: `Synthetic history verification tail ${index}.`, created_at: `2026-01-01T00:01:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } })),
  ];
  const makeRecord = (id, fields = {}) => {
    const entry = definitions.find(([key]) => key === id);
    return { receipt_id: id, base_receipt_id: id === 'receipt_one' ? null : 'receipt_base', quest_id: 'quest_history', run_id: 'run_history', path: entry[1], execution_mode: entry[3], change: id === 'receipt_one' ? 'created' : 'revised', created_at: '2026-01-01T00:00:05Z', sha256: digest, base_sha256: id === 'receipt_one' ? null : baseDigest, added: id === 'receipt_one' ? 1200 : 3, removed: id === 'receipt_one' ? 0 : 1, text: id === 'receipt_one' ? `--- /dev/null\n+++ b/src/same.ts\n@@ -0,0 +1,1200 @@\n${Array.from({ length: 1200 }, (_, index) => `+HISTORY ONE ${index}`).join('\n')}` : '--- a/src/same.ts\n+++ b/src/same.ts\n@@ -1 +1,3 @@\n-old\n+HISTORY TWO\n+second\n+third\n', truncated: false, origin: { original_request_message_id: 'msg_original', goal_update_message_id: 'msg_goal_not_loaded' }, ...fields };
  };
  let generation = 'history-generation-1', failConversation = false, firstStatus = 200, pendingArrived;
  const pendingReleases = [];
  const pendingStarted = new Promise((resolve) => { pendingArrived = resolve; });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const req = route.request(), url = new URL(req.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push({ path: url.pathname, method: req.method() });
    let body, status = 200;
    if (req.method() !== 'GET') { writes.push(url.pathname); status = 404; body = { detail: 'Unexpected write' }; }
    else if (url.pathname === '/health') body = { session: generation };
    else if (url.pathname === '/api/conversation') {
      status = failConversation ? 401 : 200;
      body = failConversation ? { detail: 'Synthetic Core generation expired' } : { messages, next_before: null, has_more: false };
    } else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (['/api/quests', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: generation === 'history-generation-1' ? 'history-space-one' : 'history-space-two', workspace_root: generation === 'history-generation-1' ? 'D:/Synthetic/HistoryOne' : 'D:/Synthetic/HistoryTwo', context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else if (url.pathname.startsWith('/api/artifacts/receipts/')) {
      const id = url.pathname.split('/')[4];
      if (id === 'receipt_missing' || id === 'receipt_invalid') { status = id === 'receipt_missing' ? 404 : 422; body = { detail: id === 'receipt_missing' ? 'Recorded history unavailable' : 'Recorded history exceeds limit' }; }
      else if (id === 'receipt_malformed') body = makeRecord(id, { receipt_id: 'receipt_wrong' });
      else if (id === 'receipt_one' && firstStatus !== 200) { status = firstStatus; body = { detail: 'History refresh unavailable' }; }
      else if (id === 'receipt_pending' && generation === 'history-generation-1') {
        body = makeRecord(id, { text: '+OLD STALE HISTORY CONTENT' }); pendingArrived();
        await new Promise((resolve) => { pendingReleases.push(resolve); });
      } else body = makeRecord(id, id === 'receipt_pending' ? { text: '+NEW GENERATION HISTORY CONTENT' } : {});
    } else { missing.push(url.pathname); status = 404; body = { detail: 'Missing history fixture endpoint' }; }
    try { await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) }); } catch (error) { if (!(url.pathname.includes('receipt_pending') && generation !== 'history-generation-1')) throw error; }
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('aa'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('[data-scroll-key="event:tool_receipt:receipt_one"]').waitFor({ state: 'attached' });
  await page.getByRole('button', { name: '查看工作空间文件', exact: true }).click();
  await page.getByLabel('选择预览目录', { exact: true }).evaluate((input) => {
    const transfer = new DataTransfer(), file = new File(['CURRENT FILE CONTENT'], 'same.ts', { type: 'text/plain' });
    Object.defineProperty(file, 'webkitRelativePath', { value: 'Project/src/same.ts' });
    transfer.items.add(file); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  const preview = page.getByLabel('工作空间文件预览', { exact: true });
  const line = (id) => page.locator(`[data-scroll-key="event:tool_receipt:${id}"]`);
  const pane = () => preview.locator('.artifact-history-preview:visible');
  const readCount = (id) => requests.filter(({ path }) => path === `/api/artifacts/receipts/${id}/diff`).length;
  const openHistory = async (id) => {
    const event = line(id), group = event.locator('xpath=ancestor::details[contains(@class,"event-group")]');
    if (!(await group.evaluate((element) => element.open))) await group.locator(':scope > summary').click();
    if (!(await event.evaluate((element) => element.open))) await event.locator(':scope > summary').click();
    await event.getByRole('button', { name: `查看文件历史 ${definitions.find(([key]) => key === id)[1]}`, exact: true }).click();
    await preview.waitFor();
  };
  await openHistory('receipt_one');
  await pane().locator('.artifact-history-summary').waitFor();
  check(readCount('receipt_one') >= 1 && requests.filter(({ path }) => path.startsWith('/api/artifacts/')).every(({ path }) => path === '/api/artifacts/receipts/receipt_one/diff'), 'opening recorded changes reads only its exact immutable receipt endpoint');
  check((await pane().locator('.artifact-history-summary').innerText()).includes('+1,200') && (await pane().locator('.artifact-history-summary').innerText()).includes('−0'), 'history displays true added and removed counts from the record');
  check((await line('receipt_one').locator('.inline-diff-heading').innerText()).includes('+1200'), 'loaded receipt counts are reflected beside that timeline record');
  const source = pane().locator('.source-lines');
  await source.evaluate((element) => { element.scrollTop = 8000; }); await page.waitForTimeout(80);
  check(Number(await source.locator('[data-line]').first().getAttribute('data-line')) > 200 && await source.locator('.source-line').count() <= 160, 'long recorded diffs reuse virtual source rendering');
  const originalSource = await source.elementHandle();
  await pane().getByRole('button', { name: '重新读取记录的更改', exact: true }).click();
  await pane().getByRole('status').filter({ hasText: '正在读取记录的更改' }).waitFor({ state: 'hidden' });
  check(await originalSource.evaluate((element) => element.isConnected && Math.abs(element.scrollTop - 8000) <= 1), 'refresh retains the original diff DOM and reading position');
  const beforeToggleReads = readCount('receipt_one');
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await page.getByRole('button', { name: '查看工作空间文件', exact: true }).click();
  check(await originalSource.evaluate((element) => element.isConnected && Math.abs(element.scrollTop - 8000) <= 1), 'close and reopen retain the mounted historical source position');
  check(readCount('receipt_one') === beforeToggleReads, 'reopening the same history panel does not refetch or reinterpret the receipt');
  await preview.getByRole('button', { name: '查看当前文件', exact: true }).click();
  await preview.getByRole('tab', { name: 'same.ts', exact: true }).waitFor();
  check((await preview.getByLabel('src/same.ts 源码', { exact: true }).innerText()).includes('CURRENT FILE CONTENT'), 'current file content has a separate tab and source');
  await preview.getByRole('tab', { name: 'same.ts · 记录的更改', exact: true }).click();
  check((await pane().locator('.source-lines').innerText()).includes('HISTORY ONE') && !(await pane().locator('.source-lines').innerText()).includes('CURRENT FILE CONTENT'), 'recorded changes never fall back to the current file bytes');
  await pane().getByRole('button', { name: '查看任务目标更新', exact: true }).click();
  check((await pane().getByRole('status').innerText()).includes('关联消息尚未加载'), 'unloaded task request origin has an explicit status instead of a false navigation');
  await pane().getByRole('button', { name: '查看原始请求', exact: true }).click();
  await preview.waitFor({ state: 'hidden' });
  const requestMessage = page.locator('[data-scroll-key="message:msg_original"]');
  check(await requestMessage.isVisible() && await requestMessage.evaluate((element) => element.getBoundingClientRect().bottom > document.querySelector('.timeline').getBoundingClientRect().top), 'loaded original request navigation returns to its actual message');
  await openHistory('receipt_two');
  await pane().locator('.artifact-history-summary').waitFor();
  check(await preview.getByRole('tab', { name: 'same.ts · 记录的更改', exact: true }).count() === 2, 'two receipts for the same path keep distinct history tabs');
  check((await pane().locator('.artifact-history-summary').innerText()).includes('+3') && (await pane().locator('.artifact-history-summary').innerText()).includes('−1'), 'same-path revisions keep their own exact counts');
  check((await pane().innerText()).includes('模拟记录') && (await pane().innerText()).includes('不作为真实模型执行证明'), 'mock provenance stays explicit without denying that a tool write may have occurred');
  check((await pane().locator('.source-lines').innerText()).includes('HISTORY TWO') && !(await pane().locator('.source-lines').innerText()).includes('HISTORY ONE'), 'same-path receipt selection shows only that revision');
  await page.mouse.move(0, 0); await page.screenshot({ path: `${outputDir}/history-revision-light.png`, fullPage: true }); screenshots.push('history-revision-light');
  for (const [id, status] of [['receipt_missing', 404], ['receipt_invalid', 422], ['receipt_malformed', 200]]) {
    await openHistory(id);
    await pane().getByRole('alert').waitFor();
    const error = await pane().getByRole('alert').innerText();
    check(status === 200 ? error.includes('无效的历史更改') : error.includes(String(status)), `${id} reports its unavailable, size or malformed response explicitly`);
    check(!(await pane().locator('.artifact-history-source').isVisible()), `${id} never exposes another receipt's diff`);
  }
  await preview.getByRole('tab', { name: 'same.ts · 记录的更改', exact: true }).nth(0).click();
  firstStatus = 404;
  await pane().getByRole('button', { name: '重新读取记录的更改', exact: true }).click();
  await pane().getByRole('alert').waitFor();
  check(!(await pane().locator('.artifact-history-source').isVisible()), 'failed refresh hides the previously loaded diff');
  firstStatus = 200;
  await pane().getByRole('button', { name: '重试', exact: true }).click();
  await pane().locator('.artifact-history-summary').waitFor();
  check(await originalSource.evaluate((element) => element.isConnected && Math.abs(element.scrollTop - 8000) <= 1), 'retry restores exact history without discarding its source reading state');
  await openHistory('receipt_pending'); await pendingStarted;
  check((await pane().getByRole('status').innerText()).includes('正在读取'), 'a delayed history request has a genuine loading state');
  failConversation = true;
  await page.waitForResponse((response) => response.url().includes('/api/conversation?') && response.status() === 401, { timeout: 10000 });
  await page.getByLabel('Core 启动密钥').waitFor();
  check(await preview.getByRole('tab').count() === 0 && await page.locator('.artifact-history-source:visible').count() === 0, 'disconnect clears prior history tabs and visible receipt content');
  failConversation = false; generation = 'history-generation-2';
  await page.getByLabel('Core 启动密钥').fill('bb'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await line('receipt_pending').waitFor({ state: 'attached' }); await openHistory('receipt_pending');
  await pane().locator('.source-lines').filter({ hasText: 'NEW GENERATION HISTORY CONTENT' }).waitFor();
  pendingReleases.forEach((release) => release()); await page.waitForTimeout(100);
  check((await pane().innerText()).includes('NEW GENERATION HISTORY CONTENT') && !(await page.locator('body').innerText()).includes('OLD STALE HISTORY CONTENT'), 'late old-generation history cannot overwrite the new workspace record');
  check(await preview.getByRole('tab').count() === 1, 'the new generation has only its own receipt tab');
  check(writes.length === 0 && missing.length === 0, 'history navigation performs only existing read endpoints and the new exact receipt read');
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  check(storage.local === 0 && storage.session === 0, 'history navigation and cached reading positions never use persistent browser storage');
  return { status: 'passed', fixture: 'synthetic HTTP receipt history; actual File API current bytes', checks: checks.length, screenshots, requests: requests.length, writes: writes.length, storage, results: checks };
}
