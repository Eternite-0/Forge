// Synthetic read-only HTTP verifies the ordered process transcript, without running tools.
async (page, { outputDir }) => {
  const checks = [], requests = [], unexpected = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const digest = 'a'.repeat(64), baseDigest = 'b'.repeat(64);
  const quest = { id: 'process-quest', title: '核对前端记录设计', goal: '保留过程与最终结果', version: 2, status: 'running' };
  const at = second => `2026-11-16T08:00:${String(second).padStart(2, '0')}Z`;
  const progress = (id, second, content) => ({ id, role: 'assistant', content, created_at: at(second), metadata: { source: 'router_notice', execution_mode: 'live' } });
  const user = { id: 'process-user', role: 'user', content: '参照设计整理对话过程，保留可以查看的执行记录。', created_at: at(0) };
  let messages = [user, progress('process-intro', 1, '我会先检查已有实现，再核对过程记录与界面。'), progress('process-update', 5, '资料检查已经完成，接下来运行验证并整理改动。')];
  let runs = [
    { run_id: 'process-run-a', worker_id: 'process-worker-a', quest_id: quest.id, role: '资料检查', model_id: 'fixture-review', provider_id: 'fixture', state: 'succeeded', steps: 3, tool_spent: 2, created_at: at(4) },
    { run_id: 'process-run-b', worker_id: 'process-worker-b', quest_id: quest.id, role: '运行验证', model_id: 'fixture-test', provider_id: 'fixture', state: 'running', steps: 2, tool_spent: 1, created_at: at(4) },
  ];
  const lifecycle = (id, second, index, status) => ({ kind: 'event', event_id: id, seq: second, created_at: at(second), quest_id: quest.id, run_id: runs[index].run_id, worker_id: runs[index].worker_id, title: '执行成员状态更新', status, execution_mode: 'live', detail: {} });
  const receipt = (id, second, detail, extra = {}) => ({ kind: 'tool_receipt', event_id: id, seq: second, created_at: at(second), quest_id: quest.id, title: '工具回执', status: 'succeeded', execution_mode: 'live', detail, ...extra });
  let events = [
    receipt('process-read-a', 2, { tool: 'file_read', path: 'README.md', result_sha256: digest }),
    receipt('process-read-b', 3, { tool: 'file_read', path: 'src/App.tsx', result_sha256: digest }),
    lifecycle('process-start-a', 4, 0, 'worker.started'), { ...lifecycle('process-start-b', 4, 1, 'worker.started'), created_at: '2026-11-16T08:00:04.100Z' },
    receipt('process-command', 6, { tool: 'shell_inspect', command: 'npm run build', stdout: Array.from({ length: 70 }, (_, index) => `Synthetic build record ${index + 1}`).join('\n'), exit_code: 0 }, { run_id: runs[1].run_id, worker_id: runs[1].worker_id }),
    lifecycle('process-done-a', 7, 0, 'worker.result'),
    receipt('process-edit', 8, { tool: 'file_revise', path: 'src/App.tsx', result_sha256: digest }),
  ];
  let complete = false, historyLoaded = false;
  const oldMessages = [
    { id: 'process-older-user', role: 'user', content: '较早的独立请求。', created_at: '2026-11-16T07:00:00Z' },
    { id: 'process-older-answer', role: 'assistant', content: '较早的独立回复。', created_at: '2026-11-16T07:00:02Z', metadata: { execution_mode: 'live' } },
  ];
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push(`${method} ${url.pathname}`);
    let status = 200, body;
    if (method !== 'GET') { unexpected.push(`${method} ${url.pathname}`); status = 400; body = {}; }
    else if (url.pathname === '/health') body = { session: 'process-transcript-fixture' };
    else if (url.pathname === '/api/conversation') {
      if (url.searchParams.has('before')) { historyLoaded = true; body = { messages: oldMessages, next_before: null, has_more: false }; }
      else body = { messages, next_before: historyLoaded ? null : 'process-history', has_more: !historyLoaded, router: { router_session_id: 'process-router' } };
    } else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (url.pathname === '/api/conversations/main/subagents') body = { conversation_id: 'main', workspace_id: 'process-workspace', items: runs.map(run => ({ ...run, task_title: quest.title, task_objective: quest.goal, source: { kind: 'message', id: user.id }, execution_mode: 'mock', lease_status: 'active', delegation_state: 'active', outcome: { kind: run.state, label: '' }, milestones: [] })), next_before: null, has_more: false };
    else if (url.pathname === '/api/activity') body = { items: runs, has_more: false };
    else if (url.pathname === '/api/quests') body = { items: [{ ...quest, status: complete ? 'completed' : quest.status }], has_more: false };
    else if (url.pathname === '/api/approvals/pending') body = { items: [], has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: 'process-workspace', workspace_root: 'D:/Fixture/Process', router_model_id: 'fixture-router', provider_id: 'fixture', provider_configured: true, restore_fenced: false };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/projects/discovered' || url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/budget') body = { token_limit: 10000, concurrency_limit: 2 };
    else if (url.pathname === '/api/artifacts/receipts/process-edit/diff') body = { receipt_id: 'process-edit', base_receipt_id: 'process-base', quest_id: quest.id, run_id: 'process-run-b', path: 'src/App.tsx', execution_mode: 'live', change: 'revised', created_at: at(8), sha256: digest, base_sha256: baseDigest, added: 1, removed: 1, text: '--- a/src/App.tsx\n+++ b/src/App.tsx\n@@ -1 +1 @@\n-old rendering\n+ordered process rendering', truncated: false, origin: { original_request_message_id: user.id, goal_update_message_id: null } };
    else { unexpected.push(`${method} ${url.pathname}`); status = 404; body = {}; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.clock.setFixedTime(new Date('2026-11-16T08:01:05Z'));
  await page.goto(globalThis.process.env.FORGE_UI_BASE_URL || 'http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const process = page.locator('.process-transcript').filter({ has: page.locator('[data-scroll-key="message:process-intro"]') });
  const processSummary = process.locator(':scope > summary');
  const line = id => process.locator(`[data-scroll-key="event:tool_receipt:${id}"]`);
  const toolGroup = id => process.locator('.process-tool-summary').filter({ has: page.locator(`[data-scroll-key="event:tool_receipt:${id}"]`) });
  const readCount = () => requests.filter(path => path === 'GET /api/artifacts/receipts/process-edit/diff').length;
  await process.waitFor();
  check(await page.locator('.process-transcript').count() === 1, 'the recorded work has one process disclosure');
  check(await process.evaluate(element => element.open), 'an ongoing recorded process starts expanded');
  await page.waitForFunction(() => document.querySelector('.process-transcript > summary')?.textContent.includes('用时 1 分钟 5 秒'));
  check((await processSummary.innerText()).trim() === '用时 1 分钟 5 秒', 'the active timer counts from the recorded user request in minutes and seconds');
  await page.clock.setFixedTime(new Date('2026-11-16T08:01:07Z'));
  await page.waitForFunction(() => document.querySelector('.process-transcript > summary')?.textContent.includes('用时 1 分钟 7 秒'));
  check((await processSummary.innerText()).trim() === '用时 1 分钟 7 秒', 'the running timer advances without a new timeline response');
  const order = await process.locator('.process-content').evaluate(element => [...element.children].map(child => child.classList.contains('message') ? child.dataset.scrollKey : child.classList.contains('agent-activity') ? `agents:${child.dataset.eventIds}` : `tools:${child.dataset.scrollKey}`));
  check(order.join('|') === 'message:process-intro|tools:group:tool_receipt:process-read-a|agents:process-start-a process-start-b|message:process-update|tools:group:tool_receipt:process-command|agents:process-done-a|tools:group:tool_receipt:process-edit', 'progress, tools and agent lifecycle records preserve their interleaved order');
  const started = process.locator('.agent-activity[data-event-ids="process-start-a process-start-b"]');
  check(await started.locator('.agent-avatar').count() === 2 && (await started.locator(':scope > summary').innerText()).includes('资料检查和运行验证开始工作'), 'adjacent starts share their colorful identities and a plain sentence');
  check((await process.locator('.agent-activity[data-event-ids="process-done-a"] > summary').innerText()).includes('资料检查已返回结果'), 'a recorded Worker result remains distinct from successful completion');
  check(await line('process-command').count() === 1, 'a Worker command remains in the timeline exactly once');
  check(!(await toolGroup('process-read-a').evaluate(element => element.open)) && !(await toolGroup('process-command').evaluate(element => element.open)), 'tool batches start as compact disclosure rows');
  check(readCount() === 0, 'a folded edit performs no immutable receipt read');
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-active-dark.png`, fullPage: true });
  await toolGroup('process-command').locator(':scope > summary').click();
  await line('process-command').locator(':scope > summary').click();
  const output = line('process-command').locator('.command-card .out');
  await output.waitFor();
  check((await line('process-command').locator('.command-card .cmd').innerText()) === 'npm run build' && (await output.innerText()).includes('Synthetic build record 70'), 'expanding the command exposes its exact recorded command and output');
  await output.evaluate(element => { element.scrollTop = 200; });
  const outputHandle = await output.elementHandle(), savedOutput = await output.evaluate(element => element.scrollTop);
  await toolGroup('process-edit').locator(':scope > summary').click();
  await line('process-edit').locator(':scope > summary').click();
  const diff = line('process-edit').locator('.inline-artifact-diff');
  await diff.locator('.source-lines').waitFor();
  check(readCount() === 1 && (await diff.innerText()).includes('ordered process rendering'), 'expanding the edit reads only its exact immutable receipt');
  check(await page.locator('.conversation-list > .turn-diff').count() === 1 && await process.locator('.turn-diff').count() === 0, 'one file-change summary sits outside the process disclosure');
  const draft = page.getByLabel('消息内容');
  await draft.fill('过程展开与刷新后保留的草稿');
  await page.waitForResponse(response => response.url().includes('/api/timeline?') && response.status() === 200, { timeout: 10000 });
  check(await outputHandle.evaluate((element, top) => element.isConnected && Math.abs(element.scrollTop - top) <= 1, savedOutput) && readCount() === 1, 'polling preserves the open command viewport and cached immutable diff');
  await started.locator(':scope > summary').click();
  const secondRunDetails = started.locator('.agent-activity-record[data-run-id="process-run-b"] .worker-run-details');
  await secondRunDetails.locator(':scope > summary').click();
  check(await started.evaluate(element => element.open) && !(await secondRunDetails.evaluate(element => element.open)), 'the grouped agent row keeps its outer process open while a single run detail is closed');
  await line('process-command').evaluate(element => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top - 20; });
  const readingOffset = () => line('process-command').evaluate(element => element.getBoundingClientRect().top - document.querySelector('.timeline').getBoundingClientRect().top);
  const beforeLateRecord = await readingOffset();
  messages = [...messages,
    { ...progress('process-between-starts', 4, '已开始检查资料，同时准备运行验证。'), created_at: '2026-11-16T08:00:04.050Z' },
    { ...progress('process-late-update', 5, '检查结果已核对，现在继续验证。'), created_at: '2026-11-16T08:00:05.500Z' },
  ];
  await process.locator('[data-scroll-key="message:process-late-update"]').waitFor({ timeout: 10000 });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const afterLateRecord = await readingOffset();
  check(Math.abs(afterLateRecord - beforeLateRecord) <= 1 && readCount() === 1, `a late progress message preserves the visible command reading anchor and cached diff: ${JSON.stringify({ beforeLateRecord, afterLateRecord, readCount: readCount() })}`);
  const splitSecond = process.locator('.agent-activity[data-event-ids="process-start-b"]');
  check(await splitSecond.evaluate(element => element.open) && !(await splitSecond.locator('.worker-run-details').evaluate(element => element.open)), 'splitting adjacent agent starts preserves the remounted second run detail closure and manually expanded outer row');
  check(await line('process-command').count() === 1 && readCount() === 1, 'splitting agent records neither duplicates tools nor reloads cached receipt content');
  await processSummary.click();
  await processSummary.click();
  complete = true;
  runs = runs.map(run => ({ ...run, state: 'succeeded' }));
  events = [...events, lifecycle('process-done-b', 9, 1, 'worker.result')];
  messages = [...messages, { id: 'process-final', role: 'assistant', content: '记录展示已整理完成。\n\n工具摘要与子智能体动态保持原顺序，详情可以按需展开。', created_at: at(10), metadata: { execution_mode: 'live', model_id: 'fixture-router' } }];
  const final = page.locator('.conversation-list > [data-scroll-key="message:process-final"]');
  await final.waitFor({ timeout: 12000 });
  check(await process.evaluate(element => element.open), 'manual process expansion survives the final reply and completed Worker snapshot');
  check(await process.locator('[data-scroll-key="message:process-final"]').count() === 0, 'the trailing final reply is outside the execution process');
  check(await page.locator('.conversation-list > .turn-diff').count() === 1 && await draft.inputValue() === '过程展开与刷新后保留的草稿', 'final delivery preserves the single change summary and composer draft');
  check(readCount() === 1 && await outputHandle.evaluate((element, top) => element.isConnected && Math.abs(element.scrollTop - top) <= 1, savedOutput), 'completion preserves the exact command DOM and cached diff');
  await page.locator('.timeline').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-expanded-dark.png`, fullPage: true });
  await processSummary.click();
  check(!(await process.locator('.process-content').isVisible()) && await final.isVisible(), 'folding the process leaves the final reply readable');
  await processSummary.click();
  await page.getByRole('button', { name: '加载更早的消息与记录', exact: true }).click();
  await page.locator('[data-scroll-key="message:process-older-user"]').waitFor();
  check(await process.evaluate(element => element.open) && readCount() === 1 && await outputHandle.evaluate(element => element.isConnected), 'prepending older history preserves this process expansion and retained tool DOM');
  await page.reload();
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await final.waitFor();
  check(!(await process.evaluate(element => element.open)) && await final.isVisible(), 'a completed process starts compact on a fresh view while its final reply remains visible');
  check((await processSummary.innerText()).trim() === '用时 0 分钟 10 秒', 'the completed timer freezes at the recorded reply time');
  await page.locator('.timeline').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-collapsed-dark.png`, fullPage: true });
  await processSummary.click();
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-default-dark.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-light.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const sidebarClose = page.getByRole('button', { name: '收起工作空间侧栏', exact: true });
  if (await sidebarClose.count()) await sidebarClose.click();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'the narrow process has no horizontal page overflow');
  await draft.fill('窄屏输入仍然可用');
  const composerHit = await page.locator('.send-button').evaluate(element => { const rect = element.getBoundingClientRect(); const target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2); return target === element || element.contains(target); });
  check(composerHit && await draft.inputValue() === '窄屏输入仍然可用', 'the 390px process leaves the composer usable');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: `${outputDir}/forge-process-transcript-390-dark.png`, fullPage: true });
  check(unexpected.length === 0, `all process inspection uses existing read-only routes: ${unexpected.join(', ')}`);
  return { status: 'passed', checks, order, requests: [...new Set(requests)], screenshots: ['forge-process-transcript-active-dark.png', 'forge-process-transcript-default-dark.png', 'forge-process-transcript-expanded-dark.png', 'forge-process-transcript-collapsed-dark.png', 'forge-process-transcript-light.png', 'forge-process-transcript-390-dark.png'] };
}
