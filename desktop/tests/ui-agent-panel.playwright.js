// Stateful synthetic HTTP: inspect the Agent companion without running models or tools.
async (page, { outputDir }) => {
  const checks = [], unexpected = [], reads = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const quest = { id: 'agent-panel-quest', title: '整理工作空间', goal: '检查界面与资料', version: 3, status: 'running' };
  const messages = [
    { id: 'agent-panel-user', role: 'user', content: '检查项目，整理发现。', created_at: '2026-11-16T08:00:00Z' },
    { id: 'agent-panel-answer', role: 'assistant', content: '我已安排资料、界面和验证工作，进展会汇总在这里。', created_at: '2026-11-16T08:00:06Z' },
  ];
  let runs = [
    { run_id: 'panel-run-a', worker_id: 'panel-worker-a', quest_id: quest.id, role: '资料整理', model_id: 'fixture-research', provider_id: 'fixture', state: 'succeeded', steps: 3, tool_spent: 2, created_at: '2026-11-16T08:00:01Z' },
    { run_id: 'panel-run-b', worker_id: 'panel-worker-b', quest_id: quest.id, role: '界面检查', model_id: 'fixture-review', provider_id: 'fixture', state: 'succeeded', steps: 4, tool_spent: 3, created_at: '2026-11-16T08:00:02Z' },
    { run_id: 'panel-run-c', worker_id: 'panel-worker-c', quest_id: quest.id, role: '运行验证', model_id: 'fixture-test', provider_id: 'fixture', state: 'running', steps: 2, tool_spent: 1, created_at: '2026-11-16T08:00:03Z' },
  ];
  let activityFailure = false;
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    reads.push(`${method} ${url.pathname}`);
    let status = 200, body;
    if (method !== 'GET') { unexpected.push(`${method} ${url.pathname}`); status = 400; body = {}; }
    else if (url.pathname === '/health') body = { session: 'agent-panel-fixture' };
    else if (url.pathname === '/api/conversation') body = { messages, router: { router_session_id: 'agent-panel-router' }, has_more: false };
    else if (url.pathname === '/api/quests') body = { items: [quest], has_more: false };
    else if (url.pathname === '/api/activity') { if (activityFailure) { status = 503; body = { detail: 'synthetic activity unavailable' }; } else body = { items: runs, has_more: false }; }
    else if (url.pathname === '/api/timeline') body = { items: runs.map((run, index) => ({ kind: 'tool_receipt', event_id: `panel-tool-${index}`, seq: index + 1, quest_id: quest.id, run_id: run.run_id, worker_id: run.worker_id, created_at: `2026-11-16T08:00:0${index + 2}Z`, title: '工具调用', status: 'succeeded', execution_mode: 'live', detail: { tool: 'file_read', path: ['README.md', 'src/App.tsx', 'tests/ui.test.ts'][index] } })), has_more: false };
    else if (url.pathname === '/api/settings') body = { revision: 0, config: null, workspace_id: 'agent-panel-workspace', workspace_root: 'D:/Fixture/AgentPanel', router_model_id: 'fixture-router', provider_id: 'fixture', provider_configured: true, restore_fenced: false };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/approvals/pending') body = { items: [], has_more: false };
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/projects/discovered' || url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/budget') body = { token_limit: 10000, concurrency_limit: 2 };
    else { unexpected.push(`${method} ${url.pathname}`); status = 404; body = {}; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  const connect = async () => {
    await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
    await page.getByRole('button', { name: '连接', exact: true }).click();
    await page.locator('.agent-activity').first().waitFor({ state: 'attached' });
    await page.waitForFunction(() => document.querySelectorAll('.agent-activity').length === 3);
  };
  const hitComposer = () => page.locator('.send-button').evaluate(element => {
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
    return hit === element || element.contains(hit);
  });
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('http://127.0.0.1:5173/');
  await connect();
  const panel = page.locator('.agent-panel'), summary = panel.locator('.agent-panel-summary');
  await panel.waitFor();
  const geometry = await panel.evaluate(element => {
    const rect = element.getBoundingClientRect(), chat = document.querySelector('.conversation-shell').getBoundingClientRect();
    return { width: rect.width, left: rect.left, chatRight: chat.right };
  });
  check(Math.abs(geometry.width - 300) < 1 && geometry.left >= geometry.chatRight, 'wide Agent card is 300px and occupies the right side without covering chat');
  check((await summary.innerText()).includes('2 完成') && (await summary.innerText()).includes('1 运行中'), 'the right summary reports current run states');
  check(await summary.locator('.agent-avatar').count() === 3, 'the summary shows three colored flower identities');
  check(await page.locator('.agent-activity').evaluateAll(elements => elements.every(element => !element.open)), 'agent current-state details start compact inside the chronological process');
  check(await page.locator('.agent-activity > summary').evaluateAll(elements => elements.every(element => element.textContent.includes('当前状态'))), 'activity snapshots identify the current state without fabricating lifecycle events');
  const summaryMarks = await summary.locator('.agent-avatar').evaluateAll(elements => elements.map(element => ({ shape: element.getAttribute('data-agent-shape'), source: element.querySelector('img').currentSrc })));
  check(new Set(summaryMarks.map(mark => mark.source)).size > 1, 'different agent identities retain distinct native flower assets');
  for (let index = 0; index < runs.length; index++) {
    const mark = await page.locator(`.subagent-entry[data-run-id="${runs[index].run_id}"] .agent-avatar`).first().evaluate(element => ({ shape: element.getAttribute('data-agent-shape'), source: element.querySelector('img').currentSrc }));
    check(JSON.stringify(mark) === JSON.stringify(summaryMarks[index]), `right summary and inline identity match for ${runs[index].role}`);
  }
  const more = panel.getByRole('button', { name: 'Agent 面板菜单', exact: true });
  await more.click();
  await panel.getByRole('button', { name: '查看运行活动', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  check(await panel.isVisible() && !(await panel.locator('.agent-panel-menu').isVisible()), 'Escape closes the open Agent menu while preserving its card');
  await more.click();
  await panel.getByRole('button', { name: '查看运行活动', exact: true }).waitFor();
  await panel.locator('.agent-panel-header > span').click();
  check(await panel.isVisible() && !(await panel.locator('.agent-panel-menu').isVisible()), 'clicking outside the Agent menu dismisses it while preserving its card');
  runs = runs.map(run => run.run_id === 'panel-run-c' ? { ...run, state: 'fixture-unknown' } : run);
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('1 其他状态'), null, { timeout: 10000 });
  check((await summary.innerText()).includes('2 完成') && (await summary.innerText()).includes('1 其他状态'), 'unknown run states remain visible alongside successful runs');
  runs = runs.map(run => run.run_id === 'panel-run-c' ? { ...run, state: 'running' } : run);
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('1 运行中'), null, { timeout: 10000 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.agent-panel-summary .agent-avatar img')).every(image => image.complete && image.naturalWidth > 0 && image.currentSrc.endsWith('-dark.svg')));
  await page.screenshot({ path: `${outputDir}/forge-agent-panel-default-dark.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.agent-panel-summary .agent-avatar img')).every(image => image.currentSrc.endsWith('-light.svg')));
  await summary.click();
  check(await panel.locator('.agent-panel-run-button').count() === 3, 'the Agent disclosure exposes all three recorded runs');
  const selected = page.locator('.agent-activity[data-run-id="panel-run-c"]');
  await panel.locator('.agent-panel-run[data-run-id="panel-run-c"] .agent-panel-run-button').click();
  await page.waitForFunction(() => document.querySelector('.agent-activity[data-run-id="panel-run-c"]')?.open === true);
  check(await selected.locator('.agent-activity-record[data-run-id="panel-run-c"] .worker-run-details-body').isVisible() && (await selected.innerText()).includes('fixture-test') && !(await selected.innerText()).includes('fixture-research'), 'selecting a run opens only its exact retained run metadata');
  check(await selected.evaluate(element => element.closest('.process-transcript').open), 'selecting a run opens its containing execution process');
  check(await selected.locator('.tool-item').count() === 0, 'agent details do not duplicate tools from the chronological process');
  check(await page.locator('.tool-item').count() === 3 && await page.locator('[data-scroll-key="event:tool_receipt:panel-tool-2"]').count() === 1, 'each tool receipt stays at its original process position exactly once');
  runs = runs.map(run => ({ ...run, state: 'succeeded' }));
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('3 完成'), null, { timeout: 10000 });
  check(await selected.evaluate(element => element.open && element.closest('.process-transcript').open), 'manually opened run and process details stay open after completion');
  const afterMarks = await summary.locator('.agent-avatar').evaluateAll(elements => elements.map(element => ({ shape: element.getAttribute('data-agent-shape'), source: element.querySelector('img').currentSrc })));
  check(JSON.stringify(afterMarks) === JSON.stringify(summaryMarks), 'polling and completion preserve each flower identity');
  await page.screenshot({ path: `${outputDir}/forge-agent-panel-light.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForFunction(() => Array.from(document.querySelectorAll('.agent-panel-summary .agent-avatar img')).every(image => image.complete && image.naturalWidth > 0 && image.currentSrc.endsWith('-dark.svg')));
  check(await summary.locator('.agent-avatar img').evaluateAll(images => images.every(image => image.currentSrc.endsWith('-dark.svg'))), 'system dark mode selects the native dark flower assets');
  await summary.click();
  const cardBackground = await panel.evaluate(element => getComputedStyle(element).backgroundColor);
  check(cardBackground !== 'rgb(255, 255, 255)', 'the Agent card follows the dark system palette');
  await page.screenshot({ path: `${outputDir}/forge-agent-panel-dark.png`, fullPage: true });
  const process = page.locator('.process-transcript');
  if (await process.evaluate(element => element.open)) await process.locator(':scope > summary').click();
  const riskToggle = page.getByRole('button', { name: '切换 Agent 面板', exact: true });
  await riskToggle.click();
  runs = runs.map(run => run.run_id === 'panel-run-c' ? { ...run, state: 'failed' } : run);
  await page.waitForFunction(() => document.querySelector('.process-transcript > summary')?.textContent.includes('当前运行失败'), null, { timeout: 10000 });
  check(!(await panel.isVisible()) && !(await process.evaluate(element => element.open)) && await process.locator(':scope > summary .event-failed').isVisible(), 'a failed current run stays visible on the collapsed process header even when only successful receipts are loaded and the Agent card is hidden');
  runs = runs.map(run => run.run_id === 'panel-run-c' ? { ...run, state: 'unknown' } : run);
  await page.waitForFunction(() => document.querySelector('.process-transcript > summary')?.textContent.includes('当前状态未知'), null, { timeout: 10000 });
  check(!(await panel.isVisible()) && !(await process.evaluate(element => element.open)) && await process.locator(':scope > summary .event-unknown').isVisible() && await process.locator(':scope > summary .event-failed').count() === 0, 'an unknown current state replaces the failed state visibly without rewriting successful historical receipts');
  runs = runs.map(run => ({ ...run, state: 'succeeded' }));
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('3 完成') && !document.querySelector('.process-transcript > summary .event-unknown'), null, { timeout: 10000 });
  check(await process.locator(':scope > summary .event-failed, :scope > summary .event-unknown').count() === 0, 'a recovered run clears current risk labels while the recorded receipts remain unchanged');
  await riskToggle.click();
  await panel.getByRole('button', { name: 'README.md', exact: true }).click();
  await page.getByLabel('工作空间文件预览', { exact: true }).waitFor();
  check(!(await panel.isVisible()), 'opening a source uses the file workspace and hides the Agent companion');
  await page.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await panel.waitFor();
  await panel.locator('.agent-panel-changes').click();
  await page.getByLabel('工作空间文件预览', { exact: true }).waitFor();
  check(!(await panel.isVisible()), 'opening changes uses the existing file workspace without overlapping the Agent companion');
  await page.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await panel.waitFor();
  const draft = page.getByLabel('消息内容');
  await draft.fill('切换面板后保留这段草稿');
  const toggle = page.getByRole('button', { name: '切换 Agent 面板', exact: true });
  await toggle.click();
  check(!(await panel.isVisible()) && await draft.inputValue() === '切换面板后保留这段草稿', 'hiding the right card preserves the composer draft');
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.setViewportSize({ width: 1440, height: 1000 });
  check(!(await panel.isVisible()), 'a manually hidden card stays hidden after window resizing');
  await toggle.click();
  await panel.waitFor();
  activityFailure = true;
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('暂不可用'), null, { timeout: 10000 });
  check(await summary.locator('.agent-avatar').count() === 0 && await panel.locator('.agent-panel-run-button').count() === 0, 'an activity read failure clears stale current run states and avatars');
  activityFailure = false;
  await page.waitForFunction(() => document.querySelector('.agent-panel-summary')?.textContent.includes('3 完成'), null, { timeout: 10000 });
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.reload();
  await connect();
  await page.waitForFunction(() => document.querySelectorAll('.tool-item').length === 3);
  check(await page.locator('.process-transcript').evaluateAll(elements => elements.every(element => !element.open)) && await page.locator('[data-scroll-key="message:agent-panel-answer"]').isVisible(), 'a completed process starts collapsed after reload while its trailing reply stays visible');
  check(!(await panel.isVisible()), '1024px starts with the companion hidden');
  await draft.fill('窄屏仍可输入');
  await toggle.click();
  await panel.waitFor();
  check(await hitComposer(), 'the 1024px companion leaves the composer send button uncovered');
  await page.screenshot({ path: `${outputDir}/forge-agent-panel-1024-dark.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  const sidebarClose = page.getByRole('button', { name: '收起工作空间侧栏', exact: true });
  if (await sidebarClose.count()) await sidebarClose.click();
  await panel.waitFor();
  check(await hitComposer() && await draft.inputValue() === '窄屏仍可输入', 'the 390px companion preserves the usable composer and draft');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'the narrow companion has no horizontal page overflow');
  await page.screenshot({ path: `${outputDir}/forge-agent-panel-390-dark.png`, fullPage: true });
  await toggle.click();
  check(await draft.isVisible() && !(await panel.isVisible()), 'the narrow Agent toggle closes the overlay');
  check(unexpected.length === 0, `all requests use existing read-only endpoints: ${unexpected.join(', ')}`);
  return { status: 'passed', checks, geometry, identities: summaryMarks, reads: [...new Set(reads)], screenshots: ['forge-agent-panel-default-dark.png', 'forge-agent-panel-light.png', 'forge-agent-panel-dark.png', 'forge-agent-panel-1024-dark.png', 'forge-agent-panel-390-dark.png'] };
}
