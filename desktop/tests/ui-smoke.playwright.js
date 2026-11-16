// Test-only HTTP fixtures; no live provider, workspace or credentials are used.
async (page) => {
  const sent = [];
  let posted = 0;
  let releaseFirst;
  let firstArrived;
  const firstRequest = new Promise((resolve) => { firstArrived = resolve; });
  let saved = Array.from({ length: 20 }, (_, index) => ({ id: `history-${index}`, role: 'assistant', content: `Synthetic historical message ${index}`, created_at: `2025-01-01T00:00:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } }));
  let externalRequests = 0;
  await page.route('https://tracker.invalid/**', async (route) => { externalRequests += 1; await route.abort(); });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body = {};
    let status = 200;
    if (url.pathname === '/health') body = { session: 'test-session' };
    else if (url.pathname === '/api/conversation/message') {
      sent.push(JSON.parse(req.postData() || '{}'));
      posted += 1;
      if (posted === 1) { await new Promise((resolve) => { releaseFirst = resolve; firstArrived(); }); status = 409; body = { detail: 'test conflict' }; }
      else {
        const user_message = { id: 'm1', role: 'user', content: sent.at(-1).content, created_at: '2026-01-01T00:00:01Z', metadata: { request_id: sent.at(-1).request_id } };
        const assistant_message = { id: 'm2', role: 'assistant', content: '```python\nprint(1)\n```\n![tracker](https://tracker.invalid/pixel?token=synthetic)\n<script>window.bad=true</script>\n[unsafe](javascript:alert(1))', created_at: '2026-01-01T00:00:03Z', metadata: { execution_mode: 'mock' } };
        saved = [...saved, user_message, assistant_message]; body = { intent: 'chat', user_message, assistant_message };
      }
    } else if (url.pathname === '/api/conversation') body = { messages: saved, next_before: null, has_more: false };
    else if (url.pathname === '/api/quests') body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/approvals/pending') body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/activity') body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else if (url.pathname === '/api/timeline') body = { items: [{ kind: 'tool_receipt', event_id: 'receipt-1', seq: null, created_at: '2026-01-01T00:00:02Z', quest_id: null, title: 'Tool execution recorded', status: 'succeeded', detail: { tool: 'file_read', path: 'input.txt', result_sha256: 'a'.repeat(64) }, execution_mode: 'mock' }], next_before: null, has_more: false };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else { status = 404; body = { detail: 'fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.getByText('Core 已连接', { exact: true }).first().waitFor();
  const draft = page.getByLabel('消息内容');
  await draft.fill('synthetic draft');
  await page.getByRole('button', { name: '发送消息' }).click();
  await page.getByText('发送中', { exact: true }).waitFor();
  await firstRequest;
  await draft.fill('next draft');
  releaseFirst();
  await page.getByText(/test conflict/).first().waitFor();
  if (await draft.inputValue() !== 'next draft') throw new Error('failed send overwrote next draft');
  await page.getByRole('button', { name: '使用原请求重试' }).click();
  await page.getByRole('button', { name: '使用原请求重试' }).waitFor({ state: 'detached' });
  if (await draft.inputValue() !== 'next draft') throw new Error('retry lost next draft');
  if (sent.length !== 2 || sent[0].request_id !== sent[1].request_id) throw new Error('retry changed request ID');
  await draft.evaluate((element) => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
  await page.waitForTimeout(100);
  if (sent.length !== 2 || await draft.inputValue() !== 'next draft') throw new Error('IME composition submitted a message');
  const activity = page.locator('.event-group > summary').filter({ hasText: '已读取文件' });
  const process = activity.locator('xpath=ancestor::details[contains(@class,"process-transcript")]');
  if (!(await process.evaluate(element => element.open))) await process.locator(':scope > summary').click();
  await activity.waitFor();
  await activity.scrollIntoViewIfNeeded();
  await activity.click();
  await page.locator('.event-line > summary').filter({ hasText: 'input.txt' }).click();
  await page.locator('.event-line').filter({ hasText: 'input.txt' }).locator('.event-record > summary').click();
  await page.getByText('结果摘要').waitFor();
  const timeline = page.getByLabel('对话历史');
  const position = await timeline.evaluate((element) => element.scrollTop);
  await page.waitForTimeout(12500);
  const afterPolls = await timeline.evaluate((element) => element.scrollTop);
  if (Math.abs(position - afterPolls) > 1) throw new Error('polling moved an expanded record');
  await page.getByRole('button', { name: '复制', exact: true }).waitFor();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'http://127.0.0.1:5173' });
  await page.getByRole('button', { name: '复制', exact: true }).click();
  if (!(await page.evaluate(() => navigator.clipboard.readText())).includes('print(1)')) throw new Error('code copy did not copy the code');
  if (await page.locator('.markdown img, .markdown script, .markdown a[href^="javascript:"]').count()) throw new Error('unsafe Markdown content rendered');
  if (externalRequests || await page.evaluate(() => window.bad === true)) throw new Error('untrusted Markdown executed or loaded an external image');
  for (const label of ['后台任务','运行活动','模型库','任务团队','记忆中心','审批中心','设置','对话']) {
    const rail = page.getByLabel('主导航');
    if (label === '模型库') await page.keyboard.press('Control+,');
    if (['运行活动','任务团队','审批中心'].includes(label)) {
      await rail.getByRole('button', { name: '后台任务', exact: true }).click();
    }
    const destination = label === '模型库'
      ? page.locator('.settings-nav').getByRole('button', { name: label, exact: true })
      : ['运行活动','任务团队','审批中心'].includes(label)
        ? page.locator('.workspace-tabs').getByRole('button', { name: label, exact: true })
        : rail.getByRole('button', { name: label, exact: true });
    await destination.click();
    if (await page.locator('textarea[aria-label="消息内容"]').count() !== 1) throw new Error(`chat missing in ${label}`);
  }
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  if (storage.local || storage.session) throw new Error('unexpected persistent browser storage');
  return { status: 'passed', requests: sent.length, requestIdReused: true, singleChat: true, storage };
}
