// Synthetic read-only Core projection; file opening must not invent file content or Git history.
async (page, { outputDir }) => {
  const checks = [], unexpected = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const receipt = (id, path, overrides = {}) => ({ kind: 'tool_receipt', event_id: id, seq: null, created_at: `2026-01-01T00:00:${String(Number(id.slice(1)) + 1).padStart(2, '0')}Z`, quest_id: null, title: 'Tool execution recorded', status: 'succeeded', execution_mode: 'live', detail: { tool: 'file_revise', path }, ...overrides });
  const events = [
    ...'abcdefg'.split('').map((name, index) => receipt(`e${index}`, `src/${name}.ts`)),
    receipt('e7', 'src/a.ts'),
    receipt('e8', 'failed.ts', { status: 'failed' }),
    receipt('e9', 'mock.ts', { execution_mode: 'mock' }),
    receipt('e10', 'unknown.ts', { status: 'uncertain' }),
    receipt('e11', 'exit.ts', { detail: { tool: 'file_write', path: 'exit.ts', exit_code: 1 } }),
    receipt('e12', '../outside.ts'),
    receipt('e13', 'read-only.ts', { detail: { tool: 'file_read', path: 'read-only.ts' } }),
  ];
  let messages = [{ id: 'intro', role: 'assistant', content: 'Synthetic projection of file receipts.', created_at: '2026-01-01T00:00:00Z', metadata: { execution_mode: 'mock' } },
    ...Array.from({ length: 25 }, (_, index) => ({ id: `tail-${index}`, role: 'assistant', content: `Synthetic scroll tail ${index}`, created_at: `2026-01-01T00:01:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } }))];
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body = {}, status = 200;
    if (request.method() !== 'GET') { unexpected.push(url.pathname); status = 404; }
    else if (url.pathname === '/health') body = { session: 'activity-files-fixture' };
    else if (url.pathname === '/api/conversation') body = { messages, next_before: null, has_more: false };
    else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (url.pathname === '/api/quests' || url.pathname === '/api/approvals/pending' || url.pathname === '/api/activity') body = { items: [], has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: 'activity-files-fixture', context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else { unexpected.push(url.pathname); status = 404; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ef'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const process = page.locator('.process-transcript');
  const list = page.locator('.conversation-list > .activity-group-files');
  await list.waitFor();
  check(!(await process.evaluate((element) => element.open)) && await list.isVisible(), 'the closing file summary stays visible after the reply while process details are collapsed');
  check(await list.locator('.activity-file-row:visible').count() === 3, 'file list initially shows three recorded paths');
  check(await list.locator('.activity-file-row').count() === 7, 'successful live writes are deduplicated by validated relative path');
  check(!(await list.textContent()).match(/failed\.ts|mock\.ts|unknown\.ts|exit\.ts|outside\.ts|read-only\.ts/), 'failed, mock, unknown, nonzero-exit, invalid and read-only receipts do not enter the edited-file list');
  await process.locator(':scope > summary').click();
  const more = list.locator('.activity-more-files');
  check((await more.locator(':scope > summary').innerText()).includes('再显示 4 个文件'), 'more-files count reflects only the remaining distinct paths');
  await more.locator(':scope > summary').focus(); await page.keyboard.press('Space');
  await page.waitForFunction(() => document.querySelector('.activity-more-files > .detail-collapsible')?.style.height === 'auto');
  check(await list.locator('.activity-file-row:visible').count() === 7, 'keyboard expansion exposes every recorded file');
  await list.locator('.activity-file-row[title="src/g.ts"]').click();
  const preview = page.getByLabel('工作空间文件预览', { exact: true });
  await preview.getByRole('tab', { name: 'g.ts', exact: true }).waitFor();
  check(await preview.locator('.preview-breadcrumb').getAttribute('title') === 'src/g.ts', 'a grouped file opens its actual relative path in preview');
  check((await preview.getByRole('alert').innerText()).includes('选择此文件所在的目录'), 'a projection does not invent content when no browser file source was selected');
  await preview.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await preview.waitFor({ state: 'hidden' });
  const original = await more.elementHandle(), timeline = page.getByLabel('对话历史');
  await more.locator(':scope > summary').evaluate((element) => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top; });
  const position = await timeline.evaluate((element) => element.scrollTop);
  await page.waitForResponse((response) => response.url().includes('/api/timeline?') && response.status() === 200, { timeout: 10000 });
  await page.waitForTimeout(100);
  check(await original.evaluate((element) => element.isConnected && element.open), 'unchanged polling retains the expanded more-files DOM');
  check(Math.abs(await timeline.evaluate((element) => element.scrollTop) - position) <= 1, 'unchanged polling preserves a nonzero file-list reading position');
  const fileOffset = () => page.locator('.activity-group-files .activity-file-row[title="src/d.ts"]').evaluate((element) => element.getBoundingClientRect().top - document.querySelector('.timeline').getBoundingClientRect().top);
  const anchorOffset = await fileOffset();
  messages = [...messages, { id: 'late-split', role: 'assistant', content: 'Synthetic message splitting the receipt group.', created_at: '2026-01-01T00:00:01.500Z', metadata: { execution_mode: 'mock' } }];
  await page.locator('.message.from-agent').filter({ hasText: 'Synthetic message splitting' }).waitFor();
  const split = page.locator('.conversation-list > .activity-group-files .activity-more-files');
  check(await split.evaluate((element) => element.open), 'splitting a tool summary retains the closing more-files expansion');
  check(await list.locator('.activity-file-row:visible').count() === 7 && await split.locator('.activity-file-row:visible').count() === 4, 'the closing file summary retains every distinct successful path after a tool group splits');
  check(Math.abs(await fileOffset() - anchorOffset) <= 1, 'group splitting preserves the same file reading anchor within one pixel');
  check(!(await page.locator('.activity-group-files').allInnerTexts()).some((content) => content.includes('本轮')), 'activity file groups do not claim unsupported chat-turn or historical-diff semantics');
  await page.mouse.move(0, 0); await page.screenshot({ path: `${outputDir}/activity-files-expanded.png`, fullPage: true });
  check(unexpected.length === 0, `grouped files introduce no new HTTP endpoints or writes${unexpected.length ? `: ${unexpected.join(', ')}` : ''}`);
  return { status: 'passed', fixture: 'synthetic HTTP projection only; no file bytes', checks: checks.length, screenshots: 1, results: checks };
}
