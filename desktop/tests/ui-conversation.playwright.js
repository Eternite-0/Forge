// Normal conversation acceptance with synthetic HTTP records only.
async (page, { outputDir }) => {
  const checks = [], screenshots = [], requests = [], mutations = [], missing = [];
  const check = (condition, message) => { if (!condition) throw new Error(message); checks.push(message); };
  const near = (value, expected, message) => check(Math.abs(value - expected) <= 1, `${message}: ${value} (expected ${expected})`);
  const screenshot = async (name) => {
    name = `rebuild-${name}`;
    await page.mouse.move(0, 0);
    await page.waitForTimeout(220);
    await page.screenshot({ path: `${outputDir}/${name}.png`, fullPage: true });
    screenshots.push(name);
  };
  const quest = { id: 'quest-research', title: '整理项目调研与实施方案', goal: '阅读项目资料，整理可以核对的实施步骤。', status: 'running', version: 4 };
  const digest = 'a'.repeat(64);
  const historical = { id: 'router-historical', role: 'assistant', content: '历史回复：保留旧模型来源。', created_at: '2026-01-01T00:00:00Z', metadata: { model_id: 'previous-router-model', provider_id: 'previous-provider', execution_mode: 'mock' } };
  const notice = { id: 'router-notice', role: 'assistant', content: '状态通知：任务已进入验证阶段。', created_at: '2026-01-01T00:00:01Z', metadata: { source: 'router_notice', event_id: 'notice-fixture', execution_mode: 'mock' } };
  let messages = [
    historical, notice,
    { id: 'user-goal', role: 'user', content: '帮我阅读项目资料，整理一份可以执行的改造方案。', created_at: '2026-01-01T00:00:02Z', metadata: {} },
    { id: 'router-current', role: 'assistant', content: '我会先核对现有结构，再整理实施顺序。\n\n资料研究和方案核对会在后台继续执行，进展会回到这条对话。', created_at: '2026-01-01T00:00:04Z', metadata: { model_id: 'current-router-model', provider_id: 'fixture-provider', execution_mode: 'mock' } },
    ...Array.from({ length: 10 }, (_, index) => ({ id: `initial-tail-${index}`, role: 'assistant', content: `Inline activity viewport tail ${index + 1}.`, created_at: `2026-01-01T00:01:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } })),
  ];
  let events = [
    { kind: 'tool_receipt', event_id: 'worker-read', seq: null, created_at: '2026-01-01T00:00:03Z', quest_id: quest.id, worker_id: 'worker-research', run_id: 'run-research-current', title: 'Tool execution recorded', status: 'succeeded', detail: { tool: 'file_read', path: 'README.md', result_sha256: digest }, execution_mode: 'mock' },
    { kind: 'tool_receipt', event_id: 'worker-command', seq: null, created_at: '2026-01-01T00:00:03.750Z', quest_id: quest.id, worker_id: 'worker-research', run_id: 'run-research-current', title: 'Tool execution recorded', status: 'succeeded', detail: { tool: 'shell_inspect', command: 'git status --short', stdout: Array.from({ length: 50 }, (_, index) => ` M synthetic-command-file-${index}.txt`).join('\n'), exit_code: 0, result_sha256: digest }, execution_mode: 'mock' },
  ];
  let settings = { revision: 0, config: null, workspace_id: 'conversation-fixture', workspace_root: 'D:/Synthetic/FORGE-conversation', context_limit: 16384, provider_configured: true, provider_id: 'fixture-provider', router_model_id: 'current-router-model', restore_fenced: false, environment_overrides: [] };
  let activityItems = [
    { run_id: 'run-research-current', worker_id: 'worker-research', quest_id: quest.id, role: '资料研究', state: 'running', provider_id: 'fixture-provider', model_id: 'research-model', steps: 4, tool_spent: 2 },
    { run_id: 'run-research-blocked', worker_id: 'worker-research', quest_id: quest.id, role: '研究复核', state: 'blocked', provider_id: 'fixture-provider', model_id: 'review-model', steps: 2 },
    { run_id: 'run-plan-review', worker_id: 'worker-planning', quest_id: quest.id, role: '方案核对', state: 'review_pending', provider_id: 'fixture-provider', model_id: 'planning-model', steps: 3 },
    { run_id: 'run-old-success', worker_id: 'worker-archive', quest_id: quest.id, role: '旧资料检查', state: 'succeeded', provider_id: 'fixture-provider', model_id: 'archive-model', steps: 1 },
  ];
  let activityMode = 'hold', activityHasMore = true, failConversation = false, generation = 'conversation-generation-1';
  let releaseActivity, activityArrived;
  const firstActivity = new Promise((resolve) => { activityArrived = resolve; });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push({ method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams) });
    let body, status = 200;
    if (request.method() !== 'GET') { mutations.push(url.pathname); status = 404; body = { detail: 'unexpected mutation' }; }
    else if (url.pathname === '/health') body = { session: generation };
    else if (url.pathname === '/api/conversation') {
      if (failConversation) { status = 401; body = { detail: 'synthetic conversation generation expired' }; }
      else body = { router: { conversation_id: 'main', router_session_id: `router-session-${generation}`, revision: 4, capsule: { private: 'never-render-capsule-fixture' } }, messages, next_before: null, has_more: false };
    } else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (url.pathname === '/api/quests') body = { items: [quest], next_before: null, has_more: false };
    else if (url.pathname.endsWith('/plan')) body = { id: 'plan-research', status: 'approved', plan_revision: 2, digest, max_total_tokens: 24000, nodes: [] };
    else if (url.pathname.endsWith('/grants')) body = [];
    else if (url.pathname === '/api/approvals/pending' || url.pathname === '/api/approvals') body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/activity') {
      if (activityMode === 'hold') { activityArrived(); await new Promise((resolve) => { releaseActivity = resolve; }); }
      if (activityMode === 'error') { status = 503; body = { detail: 'synthetic worker activity unavailable' }; }
      else body = { items: activityMode === 'empty' ? [] : activityItems, next_before: activityHasMore ? 'older-run-fixture' : null, has_more: activityHasMore };
    } else if (url.pathname === '/api/settings') body = settings;
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname.endsWith('/plan/history') || url.pathname.endsWith('/teams')) body = [];
    else if (url.pathname === `/api/quests/${quest.id}`) body = { quest, workers: [] };
    else if (url.pathname === '/api/budget') body = { token_limit: 24000, concurrency_limit: 2 };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { missing.push(url.pathname); status = 404; body = { detail: 'conversation fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  const rail = page.getByLabel('主导航'), header = page.locator('.conversation-shell .topbar');
  const workers = page.getByLabel('Agent 面板', { exact: true });
  const workerDrawer = page.locator('.agent-panel-slot');
  const workerToggle = header.locator('.agent-panel-toggle');
  const modelTrigger = page.getByRole('button', { name: '查看 Router 配置', exact: true });
  const timeline = page.getByLabel('对话历史');
  const openWorkers = async () => { if (await workerToggle.getAttribute('aria-expanded') !== 'true') await workerToggle.click(); await workers.waitFor(); if (await workers.locator('.agent-panel-summary').getAttribute('aria-expanded') !== 'true') await workers.locator('.agent-panel-summary').click(); };
  const closeWorkers = async () => { if (await workerToggle.getAttribute('aria-expanded') === 'true') await workerToggle.click(); await workerDrawer.waitFor({ state: 'hidden' }); };
  const revealProcessFor = async (record) => {
    await record.waitFor({ state: 'attached' });
    const process = record.locator('xpath=ancestor::details[contains(@class,"process-transcript")]');
    if (await process.count() && !(await process.evaluate(element => element.open))) await process.locator(':scope > summary').click();
    await record.waitFor();
  };
  check(!(await header.isVisible()) && await header.evaluate((element) => element.getBoundingClientRect().height === 0), 'empty conversation has no toolbar or toolbar placeholder');
  check(await page.locator('.composer-context').count() === 1, 'normal composer retains its accessible workspace context');
  check(await page.locator('.composer-context').evaluate((element) => { const style = getComputedStyle(element), rect = element.getBoundingClientRect(); return style.position === 'absolute' && rect.width <= 1 && rect.height <= 1; }), 'workspace context occupies no visible composer row');
  check(await workerToggle.getAttribute('aria-expanded') === 'false' && await workerDrawer.getAttribute('hidden') !== null, 'the Agent companion stays hidden on an empty conversation');
  near(await workerDrawer.evaluate((element) => element.clientHeight), 0, 'collapsed Worker details occupy no conversation row');
  check(await workerDrawer.evaluate((element) => getComputedStyle(element).display === 'none'), 'hidden Agent controls are excluded from keyboard interaction');
  await screenshot('forge-conversation-collapsed-home');
  check((await workers.textContent()).includes('未连接'), 'Worker source distinguishes disconnected from an empty execution history');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await firstActivity;
  await revealProcessFor(page.locator('.message.from-agent').filter({ hasText: '我会先核对现有结构' }));
  check(await header.isVisible() && (await header.innerText()).includes('长期对话'), 'a populated conversation has its compact single-line title');
  const timelineHeight = await timeline.evaluate((element) => element.clientHeight);
  await openWorkers();
  check(await workerToggle.getAttribute('aria-expanded') === 'true' && await workers.isVisible(), 'opening the Agent companion exposes its controls');
  near(await timeline.evaluate((element) => element.clientHeight), timelineHeight, 'the Agent companion leaves the conversation viewport height unchanged');
  check(await workerDrawer.evaluate((element) => getComputedStyle(element).position) === 'absolute', 'Worker details float above the conversation');
  check((await workers.innerText()).includes('正在读取'), 'Worker loading is visible while its independent request is pending');
  check((await modelTrigger.innerText()).includes('current-router-model'), 'Router composer model comes from the existing settings response');
  check(await header.locator('[title*="router-session-conversation-generation-1"]').count() > 0, 'Router exposes its actual nonsecret session identity');
  const oldReply = page.locator('.message.from-agent').filter({ hasText: historical.content });
  check((await oldReply.innerText()).includes('Router') && (await oldReply.innerText()).includes('previous-router-model'), 'a historical Router reply retains its own recorded model');
  check(!(await oldReply.innerText()).includes('current-router-model'), 'current Router configuration never replaces a historical reply model');
  check(await oldReply.locator('.message-meta').evaluate((element) => getComputedStyle(element).opacity) === '0', 'historical reply metadata starts visually quiet');
  await oldReply.hover();
  await page.waitForTimeout(180);
  check(await oldReply.locator('.message-meta').evaluate((element) => getComputedStyle(element).opacity) === '1', 'hover reveals the recorded reply source');
  await page.mouse.move(0, 0);
  const noticeReply = page.locator('.message.from-agent').filter({ hasText: notice.content });
  check(!(await noticeReply.innerText()).includes('current-router-model') && !(await noticeReply.innerText()).includes('previous-router-model'), 'a Router notice without model metadata does not invent a model');
  check(!(await page.locator('body').innerText()).includes('never-render-capsule-fixture'), 'private Router capsule data stays out of the rendered conversation');
  const firstResponse = page.waitForResponse((response) => response.url().includes('/api/activity?') && response.status() === 200);
  activityMode = 'populated'; releaseActivity(); await firstResponse;
  await openWorkers();
  await workers.locator('.agent-panel-run-button').first().waitFor();
  check(await workers.locator('.agent-panel-run-button').count() === 4, 'the expanded Agent companion exposes every loaded execution record');
  check((await workers.innerText()).includes('查看更早的运行'), 'execution history discloses server pagination');
  check(await workers.locator('.agent-panel-run[data-run-id="run-research-current"]').count() === 1 && await workers.locator('.agent-panel-run[data-run-id="run-research-blocked"]').count() === 1, 'two runs from one Worker remain separate records');
  const currentRun = workers.locator('.agent-panel-run[data-run-id="run-research-current"] .agent-panel-run-button');
  check((await currentRun.innerText()).includes('资料研究') && (await currentRun.getAttribute('title')).includes('research-model') && (await currentRun.innerText()).includes('运行中'), 'expanded Worker details expose actual role, model and server state');
  check((await workers.locator('.agent-panel-run[data-run-id="run-plan-review"]').innerText()).includes('待验证'), 'review pending remains distinct from final task completion');
  check(!(await workers.innerText()).includes('真实执行'), 'activity records without execution mode never claim live execution');
  const panelRunDetails = currentRun.locator('..').locator('.worker-run-details');
  await panelRunDetails.locator(':scope > summary').click();
  await page.waitForFunction(() => document.querySelector('.agent-panel .worker-run-details > .detail-collapsible')?.style.height === 'auto');
  check((await panelRunDetails.innerText()).includes('fixture-provider') && (await panelRunDetails.innerText()).includes('run-research-current'), 'independent Worker details expose the exact provider and run identity');
  check((await panelRunDetails.locator('.worker-current-facts dd').allTextContents()).join('|') === '资料研究|fixture-provider|research-model|运行中|4|2', 'Worker details show recorded state, steps and tool count without inference');
  await panelRunDetails.locator(':scope > summary').focus();
  await page.keyboard.press('Tab');
  check(await workers.locator('.agent-panel-run[data-run-id="run-research-blocked"] .agent-panel-run-button').evaluate((element) => document.activeElement === element), 'normal keyboard navigation reaches the next Agent run control');
  await page.setViewportSize({ width: 1440, height: 600 });
  check(await workerDrawer.evaluate((element) => element.getBoundingClientRect().bottom <= innerHeight - 8), 'expanded Worker details stay inside a short window');
  check(await workers.evaluate((element) => element.scrollHeight > element.clientHeight), 'long Worker details scroll within the right card');
  await page.setViewportSize({ width: 1440, height: 1000 });
  const originalWorkerRun = await currentRun.elementHandle();
  await closeWorkers();
  near(await workerDrawer.evaluate((element) => element.clientHeight), 0, 'closing Worker details restores the full conversation viewport');
  check(await originalWorkerRun.evaluate((element) => element.isConnected) && await workerDrawer.evaluate((element) => getComputedStyle(element).display === 'none'), 'closing Worker details preserves the read-only snapshot and disables hidden controls');
  await screenshot('forge-conversation-collapsed-chat');
  await openWorkers();
  check(await originalWorkerRun.evaluate((element) => element.isConnected), 'reopening Worker details keeps existing run DOM mounted');
  await currentRun.focus();
  await page.keyboard.press('Escape');
  await closeWorkers();
  check(await workerToggle.evaluate((element) => document.activeElement === element), 'Escape closes Worker details and restores trigger focus');
  await openWorkers();
  await header.locator('h1').click();
  check(await workerToggle.getAttribute('aria-expanded') === 'true', 'the persistent Agent companion stays available after clicking the conversation');
  await closeWorkers();

  await page.getByLabel('消息内容').fill('模型配置导航仍保留这一条草稿');
  const modelMenu = page.getByRole('dialog', { name: 'Router 模型配置', exact: true });
  await modelTrigger.click();
  await modelMenu.waitFor();
  check((await modelMenu.locator('.router-model').innerText()).includes('current-router-model') && (await modelMenu.locator('.router-provider').innerText()).includes('fixture-provider'), 'model popover exposes the actual configured provider and model');
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('.composer-model-menu')?.getAttribute('aria-hidden') === 'true');
  check(await modelTrigger.evaluate((element) => document.activeElement === element), 'Escape closes Router configuration and restores trigger focus');
  await modelTrigger.click();
  await header.locator('h1').click();
  await page.waitForFunction(() => document.querySelector('.composer-model-menu')?.getAttribute('aria-hidden') === 'true');
  check(await modelTrigger.getAttribute('aria-expanded') === 'false', 'outside click dismisses Router configuration');
  for (const [button, expectedTitle] of [['环境', '运行配置'], ['模型库', '模型库']]) {
    await modelTrigger.click();
    await modelMenu.getByRole('button', { name: button, exact: true }).click();
    await page.locator('.settings-title').filter({ hasText: expectedTitle }).waitFor();
    check(await page.locator('.settings-title').innerText() === expectedTitle, `Router configuration opens the real ${expectedTitle} settings page`);
    await rail.getByRole('button', { name: '对话', exact: true }).click();
    check(await page.getByLabel('消息内容').inputValue() === '模型配置导航仍保留这一条草稿' && await modelTrigger.getAttribute('aria-expanded') === 'false', `${expectedTitle} navigation returns to the same draft with its popover closed`);
  }
  await page.getByLabel('消息内容').fill('');
  await openWorkers();
  await screenshot('forge-conversation-router-worker-initial');
  const group = page.locator('.event-group').filter({ hasText: 'README.md' }).first();
  check((await group.locator(':scope > summary').getAttribute('title')).includes('Worker') && (await group.locator(':scope > summary').getAttribute('title')).includes('资料研究'), 'activity summary retains real Worker identity in its source tooltip');
  check(!(await group.locator(':scope > summary').innerText()).includes('Worker'), 'activity summary stays a compact action line');
  await currentRun.click();
  const selectedEntry = page.locator('.agent-activity[data-run-id="run-research-current"]');
  check(await selectedEntry.evaluate(element => element.open && element.closest('.process-transcript').open) && (await selectedEntry.innerText()).includes('run-research-current') && (await selectedEntry.innerText()).includes('research-model'), 'the Agent row locates and expands the exact run metadata in its visible process');
  check(await selectedEntry.locator('.tool-item').count() === 0 && await page.locator('[data-scroll-key="event:tool_receipt:worker-read"]').count() === 1 && await page.locator('[data-scroll-key="event:tool_receipt:worker-command"]').count() === 1, 'run metadata does not adopt or duplicate the independently ordered tool records');
  await selectedEntry.getByRole('button', { name: '任务详情', exact: true }).click();
  await page.locator('.quest-overview').getByRole('heading', { name: quest.title, exact: true }).waitFor();
  check((await page.locator('.quest-overview').innerText()).includes(quest.goal), 'Worker row opens the actual associated task');
  await rail.getByRole('button', { name: '对话', exact: true }).click();
  await openWorkers();
  await workers.getByRole('button', { name: 'Agent 面板菜单', exact: true }).click();
  await workers.getByRole('button', { name: '查看运行活动', exact: true }).click();
  await page.locator('.workspace-tabs').getByRole('button', { name: '运行活动', exact: true }).waitFor();
  check(await page.locator('.workspace-tabs').getByRole('button', { name: '运行活动', exact: true }).getAttribute('aria-current') === 'page' || (await page.locator('.workspace-tabs').getByRole('button', { name: '运行活动', exact: true }).getAttribute('class')).includes('active'), 'all execution records use the existing activity destination');
  await rail.getByRole('button', { name: '对话', exact: true }).click();
  await group.locator(':scope > summary').click();
  const readLine = group.locator('.event-line').filter({ hasText: 'README.md' });
  await readLine.locator(':scope > summary').click();
  await page.waitForFunction(() => document.querySelector('.event-line > .detail-collapsible')?.style.height === 'auto');
  const readRecord = readLine.locator('.event-record');
  check(await readRecord.getAttribute('open') === null && !(await readLine.locator('.event-origin').isVisible()), 'file activity keeps technical origin behind record details');
  await readRecord.locator(':scope > summary').click();
  check((await readLine.locator('.event-origin').innerText()).includes('资料研究'), 'expanded activity exposes its actual Worker origin');
  const inlineRunDetails = readLine.locator('.worker-run-details');
  await inlineRunDetails.locator(':scope > summary').click();
  await page.waitForFunction(() => document.querySelector('.event-line .worker-run-details > .detail-collapsible')?.style.height === 'auto');
  check((await inlineRunDetails.locator('.worker-history-facts').innerText()).includes('已读取文件') && (await inlineRunDetails.locator('.worker-current-facts').innerText()).includes('运行中'), 'inline Worker details separate the historical event from current execution state');
  const originalInlineDetails = await inlineRunDetails.elementHandle();
  const originalGroup = await group.elementHandle(), originalLine = await readLine.elementHandle();
  await readLine.evaluate((element) => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top; });
  const scrollPosition = await timeline.evaluate((element) => element.scrollTop);
  activityItems = [{ ...activityItems[0], state: 'succeeded' }, { ...activityItems[1], state: 'unknown', model_id: null, role: null }];
  activityHasMore = false;
  await page.waitForResponse((response) => response.url().includes('/api/activity?') && response.status() === 200, { timeout: 10000 });
  await page.waitForFunction(() => document.querySelector('.agent-panel')?.textContent.includes('执行成功'));
  check((await workers.innerText()).includes('执行成功') && !(await workers.innerText()).includes('已完成'), 'Worker success does not imply Quest completion');
  await workers.locator('.agent-panel-run[data-run-id="run-research-blocked"] .worker-run-details > summary').click();
  check((await workers.innerText()).includes('模型未记录') && (await workers.innerText()).includes('未知'), 'missing Worker model and unknown state remain explicit');
  near(await timeline.evaluate((element) => element.scrollTop), scrollPosition, 'Worker polling keeps the expanded conversation viewport');
  check(await originalGroup.evaluate((element) => element.isConnected && element.open) && await originalLine.evaluate((element) => element.isConnected && element.open), 'Worker polling keeps expanded activity DOM mounted');
  check(await originalInlineDetails.evaluate((element) => element.isConnected && element.open) && (await inlineRunDetails.locator('.worker-current-facts').innerText()).includes('执行成功'), 'polling retains the independent Worker disclosure while updating its current state');
  check((await inlineRunDetails.locator('.worker-history-facts').innerText()).includes('已读取文件'), 'current run success does not overwrite the historical event description');
  activityMode = 'error';
  messages = [...messages, { id: 'after-worker-failure', role: 'assistant', content: 'Worker 状态暂不可用时，对话仍继续同步。', created_at: '2026-01-01T00:00:05Z', metadata: { model_id: 'current-router-model', execution_mode: 'mock' } }];
  await page.waitForResponse((response) => response.url().includes('/api/activity?') && response.status() === 503, { timeout: 10000 });
  await workers.getByText('暂不可用', { exact: true }).waitFor({ state: 'attached' });
  check(await workers.locator('.agent-panel-run-button').count() === 0, 'a failed Worker read removes stale running states');
  check((await inlineRunDetails.innerText()).includes('执行详情暂未加载') && await inlineRunDetails.locator('.worker-current-facts').count() === 0, 'a failed activity read removes stale current details while retaining historical identity');
  await page.locator('.message.from-agent').filter({ hasText: '对话仍继续同步' }).waitFor();
  check((await modelTrigger.innerText()).includes('current-router-model'), 'Worker failure leaves Router configuration and conversation updates available');
  activityMode = 'empty';
  await page.waitForResponse((response) => response.url().includes('/api/activity?') && response.status() === 200, { timeout: 10000 });
  await workers.getByText('暂无运行', { exact: true }).waitFor({ state: 'attached' });
  check(await workers.locator('.agent-panel-run-button').count() === 0, 'a successful empty Worker response is distinct from unavailable status');
  activityMode = 'populated';
  activityItems = [
    { run_id: 'run-recovered', worker_id: 'worker-recovered', quest_id: quest.id, role: '实施方案', state: 'running', provider_id: 'fixture-provider', model_id: 'implementation-model', steps: 5 },
    { run_id: 'run-recovered-review', worker_id: 'worker-review', quest_id: quest.id, role: '资料复核', state: 'review_pending', provider_id: 'fixture-provider', model_id: 'review-model', steps: 3 },
    { run_id: 'run-recovered-check', worker_id: 'worker-check', quest_id: quest.id, role: '代码核对', state: 'queued', provider_id: 'fixture-provider', model_id: 'code-model', steps: 0 },
  ];
  await page.waitForResponse((response) => response.url().includes('/api/activity?') && response.status() === 200, { timeout: 10000 });
  await workers.locator('.agent-panel-run[data-run-id="run-recovered"]').waitFor({ state: 'attached' });
  check((await workers.locator('.agent-panel-run[data-run-id="run-recovered"] .agent-panel-run-button').getAttribute('title')).includes('implementation-model') && !(await workers.innerText()).includes('暂不可用'), 'a later successful Worker poll recovers its visible state');
  check((await inlineRunDetails.innerText()).includes('执行详情暂未加载') && !(await inlineRunDetails.innerText()).includes('implementation-model'), 'a newer Worker run never substitutes for the historical run in inline details');

  messages = [...messages, ...Array.from({ length: 18 }, (_, index) => ({ id: `regroup-tail-${index}`, role: 'assistant', content: `用于验证阅读锚点的后续消息 ${index + 1}。`, created_at: `2026-01-01T00:00:${String(index + 6).padStart(2, '0')}Z`, metadata: { model_id: 'current-router-model', provider_id: 'fixture-provider', execution_mode: 'mock' } }))];
  await page.locator('.message.from-agent').filter({ hasText: '用于验证阅读锚点的后续消息 18。' }).waitFor();
  await page.locator('.new-messages').click();
  const bottomDistance = () => timeline.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop);
  await page.waitForFunction(() => { const element = document.querySelector('.timeline'); return Math.abs(element.scrollHeight - element.clientHeight - element.scrollTop) <= 1; });
  await closeWorkers();
  near(await bottomDistance(), 0, 'collapsing Worker details keeps a following conversation at its newest message');
  await openWorkers();
  near(await bottomDistance(), 0, 'opening Worker details keeps a following conversation at its newest message');

  const commandLine = page.locator('.event-line').filter({ hasText: 'git status --short' });
  await commandLine.locator(':scope > summary').click();
  const commandRecord = commandLine.locator('.event-record');
  await commandRecord.locator(':scope > summary').click();
  await page.waitForFunction((element) => element.style.height === 'auto', await commandRecord.locator(':scope > .detail-collapsible').elementHandle());
  await commandLine.evaluate((element) => { const timeline = document.querySelector('.timeline'); timeline.scrollTop += element.getBoundingClientRect().top - timeline.getBoundingClientRect().top; });
  const commandOutput = commandLine.locator('.command-card .out');
  await commandOutput.evaluate((element) => { element.scrollTop = -45; });
  const commandOutputPosition = await commandOutput.evaluate((element) => element.scrollTop);
  check(Math.abs(commandOutputPosition) >= 40, 'regroup fixture scrolls inside a real overflowing command output');
  const commandAnchorOffset = () => commandLine.locator(':scope > summary').evaluate((element) => element.getBoundingClientRect().top - document.querySelector('.timeline').getBoundingClientRect().top);
  const readingOffset = await commandAnchorOffset(), readingPosition = await timeline.evaluate((element) => element.scrollTop);
  near(readingOffset, 0, 'the command activity is the first visible reading anchor before Worker toggles');
  await closeWorkers();
  near(await timeline.evaluate((element) => element.scrollTop), readingPosition, 'collapsing Worker details keeps manual reading scrollTop');
  near(await commandAnchorOffset(), readingOffset, 'collapsing Worker details keeps the relative reading anchor');
  await openWorkers();
  near(await timeline.evaluate((element) => element.scrollTop), readingPosition, 'opening Worker details keeps manual reading scrollTop');
  near(await commandAnchorOffset(), readingOffset, 'opening Worker details keeps the relative reading anchor');

  const previewAnchorMotion = await page.evaluate(async () => {
    const timeline = document.querySelector('.timeline');
    const anchor = document.querySelector('[data-scroll-key="event:tool_receipt:worker-command"] > summary');
    const offset = () => anchor.getBoundingClientRect().top - timeline.getBoundingClientRect().top;
    const before = offset(), samples = [];
    document.querySelector('[aria-label="查看工作空间文件"]').click();
    const started = performance.now();
    do { await new Promise(requestAnimationFrame); samples.push(offset()); }
    while (document.querySelector('.chat-workspace').style.getPropertyValue('--preview-progress') !== '1' && performance.now() - started < 1000);
    return { before, samples };
  });
  check(previewAnchorMotion.samples.every((offset) => Math.abs(offset - previewAnchorMotion.before) <= 1), 'every preview opening frame preserves the manually selected transcript anchor');
  await page.getByRole('button', { name: '关闭文件预览', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.file-preview-workspace').hidden);
  near(await commandAnchorOffset(), readingOffset, 'animated preview closing restores the same transcript anchor');
  near(await commandOutput.evaluate((element) => element.scrollTop), commandOutputPosition, 'preview layout animation retains nested command output scroll');

  const beforeRegroupOffset = await commandAnchorOffset();
  messages = [...messages, { id: 'late-between-worker-events', role: 'assistant', content: '延迟到达的 Router 消息：这条消息位于两个活动之间。', created_at: '2026-01-01T00:00:03.500Z', metadata: { model_id: 'current-router-model', provider_id: 'fixture-provider', execution_mode: 'mock' } }];
  await page.locator('.message.from-agent').filter({ hasText: '延迟到达的 Router 消息' }).waitFor();
  await page.waitForTimeout(220);
  const regroupedCommand = page.locator('[data-scroll-key="event:tool_receipt:worker-command"]');
  const regroupedGroup = regroupedCommand.locator('xpath=ancestor::details[contains(@class,"event-group")]');
  check(await regroupedGroup.count() === 1 && await page.locator('.event-group').count() === 2, 'a delayed Router message splits the two adjacent activities into distinct groups');
  check(await regroupedGroup.evaluate((element) => element.open) && await regroupedCommand.evaluate((element) => element.open) && await regroupedCommand.locator('.event-record').evaluate((element) => element.open), 'regrouping keeps the command group, event line and record details expanded');
  near(await regroupedCommand.locator('.command-card .out').evaluate((element) => element.scrollTop), commandOutputPosition, 'regrouping preserves the command output scroll position');
  near(await commandAnchorOffset(), beforeRegroupOffset, 'regrouping preserves the visible command reading anchor within one pixel');
  check(await regroupedCommand.locator(':scope > .detail-collapsible').evaluate((element) => element.style.height === 'auto' && getComputedStyle(element).opacity === '1'), 'restored event details use their final visible height without replaying expansion');
  await screenshot('forge-conversation-regrouped-activity');
  await regroupedGroup.locator(':scope > summary').click();
  await page.waitForFunction(() => !document.querySelector('[data-scroll-key="group:tool_receipt:worker-command"]')?.open);
  await group.locator(':scope > summary').click();
  await page.getByLabel('消息内容').fill('继续核对方案中的实现步骤。');
  await page.locator('.message.from-agent').filter({ hasText: '我会先核对现有结构' }).scrollIntoViewIfNeeded();
  await openWorkers();
  await screenshot('forge-conversation-router-worker');
  await page.emulateMedia({ colorScheme: 'dark' });
  await screenshot('forge-conversation-router-worker-dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(220);
  check(await header.isVisible() && await workerToggle.isVisible() && await modelTrigger.isVisible() && await page.getByLabel('消息内容').isVisible(), 'conversation title, Worker trigger and Router composer remain available in a narrow window');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && document.documentElement.scrollHeight <= innerHeight + 1), 'normal conversation has no document overflow at 390px');
  await screenshot('forge-conversation-router-worker-small');
  await page.setViewportSize({ width: 1440, height: 1000 });
  settings = { ...settings, provider_configured: false, router_model_id: null, provider_id: null };
  await page.waitForResponse((response) => response.url().includes('/api/settings') && response.status() === 200, { timeout: 10000 });
  await page.waitForTimeout(50);
  check(!(await modelTrigger.innerText()).includes('current-router-model'), 'an unconfigured Router no longer displays its prior configured model');
  check((await historicalReplyText()).includes('previous-router-model'), 'changing Router configuration preserves historical reply provenance');
  failConversation = true;
  await page.waitForResponse((response) => response.url().includes('/api/conversation?') && response.status() === 401, { timeout: 10000 });
  await page.getByLabel('Core 启动密钥').waitFor();
  check(await workers.locator('.agent-panel-run-button').count() === 0 && !(await modelTrigger.innerText()).includes('current-router-model'), 'disconnect immediately clears current Router and Worker presentation');
  failConversation = false; generation = 'conversation-generation-2';
  settings = { ...settings, provider_configured: true, provider_id: 'next-provider', router_model_id: 'next-router-model' };
  activityItems = [{ run_id: 'run-next-generation', worker_id: 'worker-next', quest_id: quest.id, role: '新代次执行', state: 'queued', model_id: 'next-worker-model', provider_id: 'next-provider' }];
  await page.getByLabel('Core 启动密钥').fill('cd'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await openWorkers();
  await workers.locator('.agent-panel-run-button').filter({ hasText: '新代次执行' }).waitFor({ state: 'attached' });
  check((await modelTrigger.innerText()).includes('next-router-model') && !(await workers.innerText()).includes('implementation-model'), 'a new Core generation shows only its own Router configuration and Worker snapshot');
  check(mutations.length === 0 && missing.length === 0, 'conversation visibility uses existing read-only endpoints without mutations');
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  check(storage.local === 0 && storage.session === 0, 'normal conversation interaction state stays out of browser storage');
  return { status: 'passed', fixture: 'synthetic HTTP only', checks: checks.length, screenshots, mutations: mutations.length, requests: requests.length, storage };

  async function historicalReplyText() { return page.locator('.message.from-agent').filter({ hasText: historical.content }).innerText(); }
}
