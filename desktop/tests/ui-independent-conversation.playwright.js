// Synthetic read-only checks for independently published feeds and opaque cursors.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [];
  const check = (value, name) => { if (!value) throw Error(name); checks.push(name); };
  const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
  let release, expired = false, recovered = false;
  const gate = new Promise(resolve => release = resolve);
  let releaseStale;
  const staleGate = new Promise(resolve => releaseStale = resolve);
  const message = (id, content) => ({ id, role: 'assistant', content, created_at: '2026-11-17T02:00:00Z' });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (req.method() !== 'GET') { writes.push(path); return route.fulfill({ status: 405, headers, json: {} }); }
    reads.push(url.pathname + url.search); let json = {};
    if (path === '/health') json = { session: 'independent-fixture' };
    else if (path === '/api/settings') { await gate; json = { revision: 1, config: {}, workspace_root: '/fixture/PermanentChat', router_model_id: 'fixture-model', restore_fenced: false }; }
    else if (path === '/api/quests' || path === '/api/timeline') { await gate; json = { items: [], has_more: false }; }
    else if (path === '/api/restore-status') json = { fenced: false };
    else if (path === '/api/conversation') {
      const after = url.searchParams.get('after');
      if (url.searchParams.get('before') === 'stale-older-page') {
        await staleGate;
        json = { messages: [message('expired-history', '失效历史页不可回填')], has_more: false };
      } else if (url.searchParams.has('before')) json = { messages: [message('older', '较早历史仍然可达')], next_before: 'stale-older-page', has_more: true };
      else if (after && expired) { expired = false; recovered = true; return route.fulfill({ status: 409, headers, json: { detail: 'synthetic stale cursor' } }); }
      else if (after === 'opaque/first') json = { messages: [message('delta-one', '第一批增量')], mode: 'delta', resume_after: 'opaque/second', has_newer: true, has_more: false };
      else if (after === 'opaque/second') json = { messages: [message('delta-two', '第二批增量')], mode: 'delta', resume_after: 'opaque/final', has_newer: false, has_more: false };
      else if (after) json = { messages: [], mode: 'delta', resume_after: after, has_newer: false };
      else json = { messages: [message(recovered ? 'recovered' : 'initial', recovered ? '重新同步后的记录' : '对话无需等待其他数据源')], mode: 'snapshot', resume_after: recovered ? 'recovered-cursor' : 'opaque/first', next_before: 'older-page', has_more: true };
    } else if (['/api/approvals/pending', '/api/approvals', '/api/activity'].includes(path)) json = { items: [], has_more: false };
    else if (path === '/api/models' || path === '/api/projects/discovered') json = [];
    await route.fulfill({ headers, json });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(process.env.FORGE_TEST_URL || 'http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.getByText('对话无需等待其他数据源', { exact: true }).waitFor({ timeout: 3000 });
  const draft = page.getByLabel('消息内容'); await draft.fill('保留这个长期对话草稿');
  await page.locator('.conversation-context summary').click();
  check(await page.getByText('任务 · 读取中', { exact: true }).isVisible(), 'chat renders while context and timeline are stalled');
  await page.getByText('第二批增量', { exact: true }).waitFor({ timeout: 10000 });
  check(await page.getByText('第一批增量', { exact: true }).count() === 1, 'two opaque delta batches drain without duplication');
  await page.getByRole('button', { name: '加载更早的消息与记录', exact: true }).click();
  await page.getByText('较早历史仍然可达', { exact: true }).waitFor();
  check(reads.some(url => url.includes('before=older-page')), 'delta cannot erase older history cursor');
  check(reads.filter(url => url.startsWith('/api/quests')).length === 1, 'slow task polling coalesces');
  release(); await page.getByText('任务 · 已更新', { exact: true }).waitFor();
  await page.screenshot({ path: `${outputDir}/independent-conversation-desktop.png` });
  await page.locator('.context-actions').getByRole('button', { name: '待审批 0', exact: true }).click();
  await page.getByRole('button', { name: '关闭任务详情', exact: true }).click();
  check(await draft.inputValue() === '保留这个长期对话草稿', 'inspection preserves permanent draft');
  const staleRequest = page.waitForRequest(request => request.url().includes('before=stale-older-page'));
  await page.getByRole('button', { name: '加载更早的消息与记录', exact: true }).click();
  await staleRequest;
  expired = true;
  await page.getByText('重新同步后的记录', { exact: true }).waitFor({ timeout: 10000 });
  check(await page.getByText('第二批增量', { exact: true }).count() === 0 && await draft.inputValue() === '保留这个长期对话草稿', '409 resets records and preserves draft');
  const staleResponse = page.waitForResponse(response => response.url().includes('before=stale-older-page'));
  releaseStale();
  await (await staleResponse).finished();
  await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 100)));
  check(await page.getByText('失效历史页不可回填', { exact: true }).count() === 0, 'an in-flight older page cannot revive the invalid transcript');
  await page.setViewportSize({ width: 390, height: 844 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px context has no horizontal overflow');
  await page.screenshot({ path: `${outputDir}/independent-conversation-narrow.png` });
  check(writes.length === 0, 'refresh and inspection remain read-only');
  return { checks, reads };
}
