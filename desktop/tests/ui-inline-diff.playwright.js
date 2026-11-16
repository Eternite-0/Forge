// Immutable diffs and Core generations are synthetic HTTP; no actual files or writes are used.
async (page, { outputDir }) => {
  const checks = [], requests = [], writes = [], missing = [], pendingReleases = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const digest = 'a'.repeat(64), baseDigest = 'b'.repeat(64);
  const changes = '--- a/src/example.ts\n+++ b/src/example.ts\n@@ -10,3 +20,3 @@\n unchanged context\n-old first line\n-old second line\n+first replacement ' + 'long synthetic code '.repeat(30) + '\n+second replacement\n@@ -100,1 +200,301 @@\n second hunk context\n' + Array.from({ length: 300 }, (_, index) => `+new recorded line ${index} ${'long synthetic code '.repeat(30)}`).join('\n');
  const definitions = [['receipt_inline', 'src/example.ts', 'file_revise', 'mock'], ['receipt_pending', 'src/pending.ts', 'file_write', 'live']];
  const events = definitions.map(([id, path, tool, mode], index) => ({ kind: 'tool_receipt', event_id: id, created_at: `2026-01-01T00:00:0${index + 1}Z`, quest_id: null, run_id: 'run_inline', title: 'Tool execution recorded', status: 'succeeded', execution_mode: mode, detail: { tool, path, result_sha256: digest } }));
  events.unshift({ kind: 'tool_receipt', event_id: 'receipt_intro', created_at: '2026-01-01T00:00:00.500Z', quest_id: null, title: 'Tool execution recorded', status: 'succeeded', execution_mode: 'mock', detail: { tool: 'file_read', path: 'README.md', result_sha256: digest } });
  let messages = [{ id: 'inline-message', role: 'assistant', content: '合成文件回执，检查会话内的真实记录预览。', created_at: '2026-01-01T00:00:00Z', metadata: { execution_mode: 'mock' } }, ...Array.from({ length: 20 }, (_, index) => ({ id: `inline-tail-${index}`, role: 'assistant', content: `Synthetic inline transcript tail ${index}`, created_at: `2026-01-01T00:01:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } }))];
  let generation = 'inline-generation-one', failConversation = false, pendingArrived;
  const pendingStarted = new Promise((resolve) => { pendingArrived = resolve; });
  const record = (id, text) => {
    const definition = definitions.find(([receipt]) => receipt === id);
    return { receipt_id: id, base_receipt_id: 'receipt_base', quest_id: 'quest_inline', run_id: 'run_inline', path: definition[1], execution_mode: definition[3], change: 'revised', created_at: '2026-01-01T00:00:03Z', sha256: digest, base_sha256: baseDigest, added: id === 'receipt_inline' ? 302 : 1, removed: id === 'receipt_inline' ? 2 : 0, text, truncated: false, origin: null };
  };
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const requestGeneration = generation;
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push({ path: url.pathname, generation: requestGeneration });
    let body, status = 200;
    if (request.method() !== 'GET') { writes.push(url.pathname); status = 400; body = { detail: 'inline fixture is read-only' }; }
    else if (url.pathname === '/health') body = { session: generation };
    else if (url.pathname === '/api/conversation') {
      if (failConversation) { status = 401; body = { detail: 'Synthetic inline generation expired' }; }
      else body = { messages, next_before: null, has_more: false };
    } else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (['/api/quests', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: generation, workspace_root: `D:/Synthetic/${generation}`, context_limit: 8192, restore_fenced: false, environment_overrides: [] };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/budget') body = { token_limit: 0, concurrency_limit: 1 };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else if (url.pathname === '/api/artifacts/receipts/receipt_inline/diff') body = record('receipt_inline', changes);
    else if (url.pathname === '/api/artifacts/receipts/receipt_pending/diff') {
      if (requestGeneration === 'inline-generation-one') {
        body = record('receipt_pending', '--- a/src/pending.ts\n+++ b/src/pending.ts\n@@ -0,0 +1 @@\n+OLD STALE INLINE CONTENT');
        pendingArrived(); await new Promise((resolve) => pendingReleases.push(resolve));
      } else body = record('receipt_pending', '--- a/src/pending.ts\n+++ b/src/pending.ts\n@@ -0,0 +1 @@\n+NEW INLINE GENERATION CONTENT');
    } else { missing.push(url.pathname); status = 404; body = { detail: 'inline fixture endpoint missing' }; }
    try { await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) }); }
    catch (error) { if (requestGeneration === generation) throw error; }
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('aa'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const line = (id) => page.locator(`[data-scroll-key="event:tool_receipt:${id}"]`);
  await line('receipt_inline').waitFor({ state: 'attached' });
  const readCount = (id) => requests.filter((request) => request.path === `/api/artifacts/receipts/${id}/diff`).length;
  check(readCount('receipt_inline') === 0 && readCount('receipt_pending') === 0, 'folded activity fetches no receipt diff');
  const process = page.locator('.process-transcript');
  await process.locator(':scope > summary').click();
  check(readCount('receipt_inline') === 0 && readCount('receipt_pending') === 0, 'opening the ordered process still avoids receipt reads');
  const group = page.locator('.process-content > .process-tool-summary');
  await group.locator(':scope > summary').click();
  check(readCount('receipt_inline') === 0 && readCount('receipt_pending') === 0, 'opening only the activity group still avoids file reads');
  await line('receipt_inline').locator(':scope > summary').click();
  const inline = line('receipt_inline').locator('.inline-artifact-diff'), source = inline.locator('.source-lines');
  await source.waitFor();
  check(readCount('receipt_inline') === 1 && readCount('receipt_pending') === 0, 'expanding a recorded file reads only its exact immutable receipt');
  check((await inline.locator('.inline-diff-heading').innerText()).includes('+302') && (await inline.locator('.inline-diff-heading').innerText()).includes('−2') && (await inline.locator('.inline-diff-heading').innerText()).includes('模拟'), 'inline header uses supplied addition/removal counts and explicit mock provenance');
  check(await inline.getByRole('button', { name: '查看文件历史 src/example.ts', exact: true }).isVisible(), 'the original file-history action remains available beside the inline diff');
  check(await inline.locator('.source-controls').count() === 0, 'inline code leaves the full file toolbar for the separate preview');
  const context = source.locator('.diff-context').first(), removed = source.locator('.diff-removed').first(), added = source.locator('.diff-added').first();
  check(await context.getAttribute('data-old-line') === '10' && await context.getAttribute('data-new-line') === '20' && await context.locator('.source-line-number').innerText() === '20', 'context rows retain both hunk numbers while displaying the current line in one gutter');
  check(await removed.getAttribute('data-old-line') === '11' && !(await removed.getAttribute('data-new-line')) && await removed.locator('.source-line-number').innerText() === '11', 'removed rows display their original line number in the single gutter');
  check(!(await added.getAttribute('data-old-line')) && await added.getAttribute('data-new-line') === '21' && await added.locator('.source-line-number').innerText() === '21', 'added rows display their new line number in the single gutter');
  const resetContext = source.locator('.diff-context').filter({ hasText: 'second hunk context' });
  check(await resetContext.getAttribute('data-old-line') === '100' && await resetContext.getAttribute('data-new-line') === '200' && await resetContext.locator('.source-line-number').innerText() === '200', 'a second unified-diff hunk resets both original and current line numbering');
  check(await added.locator('.source-line-number').count() === 1 && await removed.locator('.source-line-number').count() === 1 && await inline.locator('.diff-line-sign').count() === 0, 'diff rows keep the reference single gutter without extra signs');
  const backgrounds = await source.evaluate((element) => ['.diff-added', '.diff-removed'].map((selector) => {
    const row = element.querySelector(selector), style = getComputedStyle(row);
    return { color: style.backgroundColor, width: row.getBoundingClientRect().width, viewport: element.clientWidth, bar: style.borderInlineStartWidth, barColor: style.borderInlineStartColor, fontSize: getComputedStyle(element).fontSize, lineHeight: getComputedStyle(element).lineHeight };
  }));
  check(backgrounds.every((row) => row.color !== 'rgba(0, 0, 0, 0)' && row.color !== 'transparent' && row.width >= row.viewport - 1) && backgrounds[0].color !== backgrounds[1].color, 'added and removed backgrounds color the whole row using distinct supplied colors');
  check(backgrounds.every((row) => row.bar === '4px' && row.fontSize === '12px' && row.lineHeight === '21.6px') && backgrounds[0].barColor !== backgrounds[1].barColor, 'diff rows use the reference 4px status bars and 12px text with 21.6px line height');
  check(await source.evaluate((element) => element.clientHeight > 100 && element.clientHeight <= 360 && element.scrollHeight > element.clientHeight), 'a long inline diff has its own bounded scroll viewport');
  const timeline = page.getByLabel('对话历史');
  await line('receipt_inline').evaluate((element) => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top - 60; });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.mouse.move(0, 0); await page.screenshot({ path: `${outputDir}/simple-inline-diff-colors.png`, fullPage: true });
  const transcriptTop = await timeline.evaluate((element) => element.scrollTop);
  await source.evaluate((element) => { element.scrollTop = 2200; element.scrollLeft = 200; });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const afterDiffScroll = { transcriptBefore: transcriptTop, transcriptAfter: await timeline.evaluate((element) => element.scrollTop), diff: await source.evaluate((element) => ({ top: element.scrollTop, height: element.clientHeight, scrollHeight: element.scrollHeight, childStyle: element.firstElementChild.getAttribute('style'), firstRow: element.querySelector('[data-line]')?.getAttribute('data-line') })) };
  check(Math.abs(afterDiffScroll.transcriptAfter - afterDiffScroll.transcriptBefore) <= 1 && afterDiffScroll.diff.top >= 2000, `scrolling the diff does not scroll the conversation: ${JSON.stringify(afterDiffScroll)}`);
  const sourceHandle = await source.elementHandle(), sourceTop = await source.evaluate((element) => element.scrollTop);
  await page.waitForResponse((response) => response.url().includes('/api/timeline?') && response.status() === 200, { timeout: 10000 });
  check(await sourceHandle.evaluate((element) => element.isConnected && Math.abs(element.scrollTop - 2200) <= 1) && readCount('receipt_inline') === 1, 'polling retains the inline source DOM and reading position without rereading the immutable receipt');
  await line('receipt_inline').locator(':scope > summary').click();
  await line('receipt_inline').locator(':scope > summary').click();
  check(await sourceHandle.evaluate((element) => element.isConnected && Math.abs(element.scrollTop - 2200) <= 1) && readCount('receipt_inline') === 1 && sourceTop >= 2000, 'collapsing and reopening the file retains its cached diff and scroll position');
  await line('receipt_inline').evaluate((element) => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top + 20; });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const readingOffset = () => line('receipt_inline').evaluate((element) => element.getBoundingClientRect().top - document.querySelector('.timeline').getBoundingClientRect().top);
  const beforeSplitOffset = await readingOffset(), horizontalBefore = await source.evaluate((element) => element.scrollLeft);
  const beforeSplitAnchor = await timeline.evaluate((element) => { const top = element.getBoundingClientRect().top; return [...element.querySelectorAll('[data-scroll-key]')].map((node) => ({ key: node.dataset.scrollKey, offset: node.getBoundingClientRect().top - top })).filter((node) => node.offset <= 60).slice(-5); });
  check(horizontalBefore >= 190 && await source.locator('.source-line').count() <= 160, 'the inline fixture uses real horizontal scroll and bounded virtual rows');
  messages = [...messages, { id: 'inline-delayed-message', role: 'assistant', content: 'Synthetic delayed reply splits the activity group.', created_at: '2026-01-01T00:00:00.750Z', metadata: { execution_mode: 'mock' } }];
  await page.waitForResponse((response) => response.url().includes('/api/conversation?') && response.status() === 200, { timeout: 10000 });
  await page.waitForFunction(() => document.querySelectorAll('.event-group').length === 2);
  const regroupedSource = line('receipt_inline').locator('.source-lines');
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  check(Math.abs(await regroupedSource.evaluate((element) => element.scrollTop) - 2200) <= 1 && Math.abs(await regroupedSource.evaluate((element) => element.scrollLeft) - horizontalBefore) <= 1 && readCount('receipt_inline') === 1, 'splitting an activity group restores both diff scroll axes without rereading its immutable receipt');
  const afterSplitOffset = await readingOffset();
  check(Math.abs(afterSplitOffset - beforeSplitOffset) <= 1, `splitting the activity group preserves the visible file reading anchor within one pixel: ${JSON.stringify({ before: beforeSplitOffset, after: afterSplitOffset, beforeSplitAnchor })}`);
  await page.mouse.move(0, 0); await page.screenshot({ path: `${outputDir}/simple-inline-diff-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(220);
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'long inline code keeps horizontal scrolling inside its own viewport on narrow windows');
  await page.mouse.move(0, 0); await page.screenshot({ path: `${outputDir}/simple-inline-diff-small.png`, fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await line('receipt_pending').locator(':scope > summary').click();
  await pendingStarted;
  check((await line('receipt_pending').locator('.inline-artifact-diff').innerText()).includes('正在读取更改'), 'an inline receipt displays genuine loading while its response is pending');
  failConversation = true;
  await page.waitForResponse((response) => response.url().includes('/api/conversation?') && response.status() === 401, { timeout: 10000 });
  await page.getByLabel('Core 启动密钥').waitFor();
  check(await page.locator('.inline-artifact-diff .source-lines:visible').count() === 0, 'disconnect hides prior connection inline receipt content');
  failConversation = false; generation = 'inline-generation-two';
  await page.getByLabel('Core 启动密钥').fill('bb'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await line('receipt_pending').waitFor({ state: 'attached' });
  const nextProcess = line('receipt_pending').locator('xpath=ancestor::details[contains(@class,"process-transcript")]');
  if (!(await nextProcess.evaluate((element) => element.open))) await nextProcess.locator(':scope > summary').click();
  const nextGroup = line('receipt_pending').locator('xpath=ancestor::details[contains(@class,"event-group")]');
  if (!(await nextGroup.evaluate((element) => element.open))) await nextGroup.locator(':scope > summary').click();
  if (!(await line('receipt_pending').evaluate((element) => element.open))) await line('receipt_pending').locator(':scope > summary').click();
  await line('receipt_pending').locator('.source-lines').filter({ hasText: 'NEW INLINE GENERATION CONTENT' }).waitFor();
  pendingReleases.forEach((release) => release()); await page.waitForTimeout(100);
  check((await line('receipt_pending').innerText()).includes('NEW INLINE GENERATION CONTENT') && !(await page.locator('body').innerText()).includes('OLD STALE INLINE CONTENT'), 'a late old-connection inline result cannot overwrite the new generation');
  check(writes.length === 0 && missing.length === 0, 'inline receipt rendering uses existing read-only endpoints only');
  check(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0), 'inline receipt caches do not use browser storage');
  return { status: 'passed', fixture: 'synthetic HTTP only', checks: checks.length, writes: writes.length, receiptReads: requests.filter((request) => request.path.includes('/api/artifacts/receipts/')).length };
}
