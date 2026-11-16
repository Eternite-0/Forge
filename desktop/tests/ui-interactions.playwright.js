// Interaction acceptance uses synthetic HTTP only; it never calls a live Core.
async (page, { outputDir }) => {
  const checks = [], requests = [], mutations = [], screenshots = [], missing = [];
  const check = (condition, message) => { if (!condition) throw new Error(message); checks.push(message); };
  const screenshot = async (name) => {
    name = `rebuild-${name}`;
    await page.mouse.move(0, 0);
    await page.waitForTimeout(220);
    await page.screenshot({ path: `${outputDir}/${name}.png`, fullPage: true });
    screenshots.push(name);
  };
  const questRecords = [
    ['alpha', 'Alpha 调研交付', 'awaiting_approval', 'goal-alpha-proof'],
    ['beta', 'Beta 数据核对', 'running', 'goal-beta-proof'],
    ['gamma', 'Gamma 权限请求', 'blocked', 'goal-gamma-proof'],
    ['delta', 'Delta 暂停记录', 'paused', 'goal-delta-proof'],
    ['epsilon', 'Epsilon 补充目标', 'needs_input', 'goal-epsilon-proof'],
    ['finished', 'Finished 已完成', 'completed', 'goal-finished-proof'],
    ['failed', 'Failed 失败记录', 'failed', 'goal-failed-proof'],
    ['cancelled', 'Cancelled 取消记录', 'cancelled', 'goal-cancelled-proof'],
    ['rejected', 'Rejected 拒绝记录', 'rejected', 'goal-rejected-proof'],
    ...Array.from({ length: 18 }, (_, index) => [`history-${index}`, `History ${index} 历史记录`, 'completed', `goal-history-${index}`]),
  ];
  const quests = questRecords.map(([key, title, status, goal]) => ({ id: `quest-${key}`, title, status, goal, version: 2, plan_revision: 3, metadata: {} }));
  const closedStates = new Set(['completed', 'failed', 'cancelled', 'rejected']);
  const attentionStates = new Set(['awaiting_approval', 'blocked', 'needs_input']);
  const plan = (id) => ({ id: `plan-${id}`, plan_revision: 3, digest: 'd'.repeat(64), status: 'approved', max_total_tokens: 30000, max_cost_microunits: null,
    nodes: Array.from({ length: 24 }, (_, index) => ({ key: `node-${index}`, objective: `${id} 记录 ${index + 1}`, status: 'running', budget_tokens: 1000, allowed_tools: ['file_read'], success_criteria: ['保留实际来源'] })) });
  const messages = Array.from({ length: 12 }, (_, index) => ({ id: `interaction-message-${index}`, role: 'assistant', content: `合成交互记录 ${index}。`, created_at: `2026-01-01T00:00:${String(index).padStart(2, '0')}Z`, metadata: { execution_mode: 'mock' } }));
  const events = [{ kind: 'tool_receipt', event_id: 'interaction-read', seq: null, created_at: '2026-01-02T00:00:00Z', quest_id: null, title: 'Tool execution recorded', status: 'succeeded', execution_mode: 'mock', detail: { tool: 'file_read', path: 'synthetic.txt', result_sha256: 'e'.repeat(64) } }];
  const approval = { id: 'interaction-approval', quest_id: 'quest-alpha', action_type: 'browser_get', risk: 'R2', resource_scope: 'https://fixture.invalid/interaction-approval', plan_revision: 3, parameter_digest: 'f'.repeat(64), status: 'PENDING', reason: '合成审批详情', expires_at: '2026-12-01T00:00:00Z' };
  const settings = { revision: 2, workspace_root: 'D:/Synthetic/FORGE-interactions', workspace_id: 'workspace-interactions', provider_configured: true, provider_id: 'fixture-provider', router_model_id: 'fixture-model', context_limit: 8192, restore_fenced: false, environment_overrides: [], config: {
    provider: { id: 'fixture-provider', kind: 'openai_compatible', base_url: 'https://fixture.invalid/v1', credential_ref: 'FORGE_PROVIDER_KEY_FIXTURE', supports_tools: true, supports_json_schema: false, allow_insecure_loopback: false },
    router_model_id: 'fixture-model', router_context_limit: 8192, model_token_limit: 10000, model_concurrency_limit: 2, network_hosts: [],
  } };
  let delayPlan = false, failPlan = false, releasePlan = null, planArrived = null;
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push({ method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams) });
    let body, status = 200;
    if (request.method() !== 'GET') { mutations.push({ path: url.pathname, body: request.postData() }); status = 404; body = { detail: 'unexpected interaction mutation' }; }
    else if (url.pathname === '/health') body = { session: 'interaction-session' };
    else if (url.pathname === '/api/conversation') body = { messages, has_more: false, next_before: null };
    else if (url.pathname === '/api/timeline') body = { items: events, has_more: false, next_before: null };
    else if (url.pathname === '/api/quests') body = { items: quests, has_more: false, next_before: null };
    else if (/^\/api\/quests\/[^/]+\/plan$/.test(url.pathname)) {
      if (delayPlan) { delayPlan = false; await new Promise((resolve) => { releasePlan = resolve; planArrived?.(); }); }
      if (failPlan) { status = 503; body = { detail: 'synthetic plan refresh unavailable' }; failPlan = false; }
      else body = plan(url.pathname.split('/')[3]);
    } else if (/^\/api\/quests\/[^/]+\/grants$/.test(url.pathname)) body = [];
    else if (/^\/api\/quests\/[^/]+\/teams$/.test(url.pathname)) body = [];
    else if (url.pathname === '/api/approvals/pending' || url.pathname === '/api/approvals') body = { items: [approval], has_more: false, next_before: null };
    else if (url.pathname === '/api/activity') body = { items: [], has_more: false, next_before: null };
    else if (url.pathname === '/api/settings') body = settings;
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/budget') body = { token_limit: 10000, known_tokens: 0, reserved_tokens: 0, opening_tokens: 0, charged_tokens: 0, active_reservations: 0, concurrency_limit: 2 };
    else if (url.pathname === '/api/models') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/projects/discovered' || url.pathname === '/api/events' || url.pathname === '/api/triggers') body = [];
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { missing.push(url.pathname); status = 404; body = { detail: 'interaction fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.locator('.message.from-agent').first().waitFor();
  const draft = page.getByLabel('消息内容');
  const taskTrigger = page.getByRole('button', { name: '查看任务', exact: true });
  const closeTasks = () => page.getByRole('button', { name: '关闭任务详情', exact: true }).click();
  const timeline = page.getByLabel('对话历史');
  const originalTimeline = await timeline.elementHandle(), originalDraft = await draft.elementHandle();
  const baseHeight = await draft.evaluate((element) => element.clientHeight);
  await draft.fill(Array.from({ length: 25 }, (_, index) => `草稿第 ${index + 1} 行`).join('\n'));
  await page.waitForFunction((height) => document.querySelector('textarea[aria-label="消息内容"]').clientHeight > height, baseHeight);
  const tallHeight = await draft.evaluate((element) => element.clientHeight);
  check(tallHeight > baseHeight && tallHeight <= 180, 'composer grows for multiple lines and respects its 180px limit');
  check(await draft.evaluate((element) => element.scrollHeight > element.clientHeight), 'long drafts scroll inside the bounded editor');
  await draft.fill('');
  await page.waitForFunction((height) => document.querySelector('textarea[aria-label="消息内容"]').clientHeight === height, baseHeight);
  check(await draft.evaluate((element) => element.clientHeight) === baseHeight, 'clearing the draft restores the compact editor');
  await draft.fill('交互验收保留草稿');
  await draft.press('Shift+Enter');
  check((await draft.inputValue()).includes('\n') && mutations.length === 0, 'Shift+Enter adds a line without submitting');
  const preservedDraft = await draft.inputValue();
  await draft.evaluate((element) => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
  check(await draft.inputValue() === preservedDraft && mutations.length === 0, 'composer composition remains non-submitting');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'http://127.0.0.1:5173' });
  const copyReply = page.getByRole('button', { name: '复制回复', exact: true }).first();
  await copyReply.focus();
  await copyReply.click();
  check(await page.evaluate(() => navigator.clipboard.readText()) === messages[0].content, 'copy reply copies the actual complete message content');
  check(await page.getByRole('status').filter({ hasText: '已复制回复' }).isVisible(), 'copy reply gives visible completion feedback');
  const approvalCard = page.locator('.approval-card').filter({ hasText: approval.resource_scope });
  check((await approvalCard.locator('.approval-scope').innerText()).includes(approval.resource_scope) && (await approvalCard.locator('.approval-scope').innerText()).includes('计划版本 3'), 'collapsed approval keeps its actual scope and plan revision visible');
  const approvalDetails = approvalCard.locator('.approval-details'), approvalSummary = approvalDetails.locator(':scope > summary');
  const originalApprovalPanel = await approvalDetails.locator(':scope > .detail-collapsible').elementHandle();
  check(await approvalSummary.getAttribute('aria-expanded') === 'false', 'approval technical details start collapsed');
  await approvalSummary.click();
  await page.waitForFunction(() => document.querySelector('.approval-details > .detail-collapsible')?.style.height === 'auto');
  check((await approvalDetails.innerText()).includes(approval.parameter_digest) && (await approvalDetails.innerText()).includes(approval.reason), 'expanded approval exposes the original digest and reason');
  await approvalSummary.click();
  await page.waitForFunction(() => !document.querySelector('.approval-details')?.open);
  check(await originalApprovalPanel.evaluate((element) => element.isConnected) && await approvalSummary.getAttribute('aria-expanded') === 'false', 'collapsing approval details preserves the mounted detail DOM');
  const more = page.getByRole('button', { name: '更多对话操作', exact: true });
  await more.click();
  const moreDialog = page.getByRole('dialog', { name: '对话操作', exact: true });
  await moreDialog.waitFor();
  await page.keyboard.press('Escape');
  check(!(await moreDialog.isVisible()) && await draft.inputValue() === preservedDraft, 'closing the overflow preserves the conversation draft');
  check(await more.evaluate(element => document.activeElement === element), 'the overflow restores focus');
  await taskTrigger.click();
  const list = page.locator('.quest-list'), filters = list.locator('.quest-filters'), search = page.getByLabel('搜索任务');
  const row = (id) => list.locator(`button.list-row[data-task-id="${id}"]`);
  const heading = page.locator('.quest-detail-heading');
  const back = () => page.getByRole('button', { name: '← 返回任务列表', exact: true }).click();
  await row('quest-alpha').waitFor();
  check(await page.locator('.quest-detail').count() === 0, 'global task manager starts with the list, not an automatically selected detail');
  check(!(await row('quest-alpha').getAttribute('aria-label')).includes('quest-alpha'), 'task accessible names contain human titles rather than internal IDs');
  check(await page.locator('.rail').count() === 0, 'task drawer does not introduce a permanent sidebar');
  await row('quest-alpha').click();
  await page.locator('.quest-plan-nodes .plan-node').first().waitFor();
  check(await heading.innerText() === 'Alpha 调研交付' && !(await list.isVisible()), 'title click opens the correct detail and hides the list');
  const details = page.locator('.quest-plan > .quest-technical');
  check(await details.getAttribute('open') === null, 'technical details are collapsed on entry');
  await details.locator(':scope > summary').click();
  check((await details.innerText()).includes('quest-alpha'), 'internal task ID remains available only in disclosed technical detail');
  await back();
  check(await row('quest-alpha').evaluate((element) => document.activeElement === element), 'returning to the list restores focus to the opened task');
  const project = list.locator('.quest-project-group');
  check(await project.count() === 1 && await project.locator(':scope > .quest-project-toggle').count() === 0, 'a single workspace shows tasks without a redundant project heading');
  check(await project.locator('.quest-closed-toggle').getAttribute('aria-expanded') === 'false', 'finished tasks start in a folded history group');
  check(!(await search.isVisible()) && !(await filters.isVisible()), 'search and filters do not crowd the default task list');
  await list.locator('.quest-find > summary').click();
  check(await search.isVisible() && await filters.isVisible(), 'larger task lists retain discoverable search and status filters');
  for (const [label, expected] of [
    ['未结束', quests.filter((quest) => !closedStates.has(quest.status)).length],
    ['需处理', quests.filter((quest) => attentionStates.has(quest.status)).length],
    ['已结束', quests.filter((quest) => closedStates.has(quest.status)).length],
    ['全部', quests.length],
  ]) {
    await filters.getByRole('button', { name: label, exact: true }).click();
    check(await list.locator('.list-row').count() === expected, `${label} filter uses only its exact loaded status set`);
    check(await filters.getByRole('button', { name: label, exact: true }).getAttribute('aria-pressed') === 'true', `${label} filter exposes selection`);
    check(await page.locator('.quest-detail').count() === 0, `${label} filter does not automatically open a task`);
  }
  for (const [query, expectedId] of [['调研', 'quest-alpha'], ['goal-beta-proof', 'quest-beta'], ['权限请求', 'quest-gamma']]) {
    await search.fill(query);
    check(await list.locator('.list-row').count() === 1 && await row(expectedId).count() === 1, `task search matches ${query} in human title or goal`);
  }
  await search.fill('quest-gamma');
  check(await list.locator('.list-row').count() === 0, 'internal task IDs are not searchable UI labels');
  await search.press('Escape');
  check(await search.inputValue() === '' && await list.locator('.list-row').count() === quests.length, 'Escape clears task search and restores loaded matches');
  await search.fill('Beta');
  await page.getByRole('button', { name: '清除任务搜索', exact: true }).click();
  check(await search.inputValue() === '' && await search.evaluate((element) => document.activeElement === element), 'task search clear restores focus to the search field');
  await filters.getByRole('button', { name: '已结束', exact: true }).click();
  await search.fill('Alpha');
  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  check(await search.inputValue() === '' && await list.locator('.list-row').count() === quests.length && await filters.getByRole('button', { name: '全部', exact: true }).getAttribute('aria-pressed') === 'true', 'empty filter state offers a complete reset');
  check((await list.locator('.quest-filter-count').innerText()).includes(`${quests.length} 项任务`), 'filter count reports the actual loaded task set');
  // Search expands history; fold it explicitly before checking visible keyboard order.
  if (await project.locator('.quest-closed-toggle').getAttribute('aria-expanded') === 'true') await project.locator('.quest-closed-toggle').click();
  await row('quest-alpha').focus();
  for (const id of ['quest-gamma', 'quest-epsilon', 'quest-beta']) {
    await page.keyboard.press('ArrowDown');
    check(await row(id).evaluate((element) => document.activeElement === element), `ArrowDown focuses ${id} in attention-first order`);
  }
  check(await page.locator('.quest-detail').count() === 0, 'arrow navigation does not open details or change selection');
  await page.keyboard.press('Home');
  check(await row('quest-alpha').evaluate((element) => document.activeElement === element), 'Home focuses the first visible task');
  await page.keyboard.press('End');
  check(await row('quest-delta').evaluate((element) => document.activeElement === element), 'End skips folded historical tasks');
  await project.locator('.quest-closed-toggle').click();
  await row('quest-alpha').focus();
  await page.keyboard.press('End');
  check(await row('quest-history-17').evaluate((element) => document.activeElement === element), 'End includes expanded history');
  await page.keyboard.press('ArrowUp');
  await row('quest-history-16').evaluate((element) => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', isComposing: true, bubbles: true })));
  check(await row('quest-history-16').evaluate((element) => document.activeElement === element), 'IME composition does not move task focus');
  await search.focus();
  await search.press('ArrowDown');
  check(await search.evaluate((element) => document.activeElement === element), 'list navigation leaves search field arrow keys alone');
  await row('quest-beta').focus();
  await page.keyboard.press('Enter');
  await heading.getByText('Beta 数据核对', { exact: true }).waitFor();
  check(!(await list.isVisible()), 'Enter opens the focused task');
  await back();
  const detail = page.locator('.task-inspector .quest-body');
  check(await list.locator(':scope > .list').evaluate((element) => getComputedStyle(element).maxHeight === 'none'), 'task list is not capped inside a tiny nested viewport');
  await detail.evaluate((element) => { element.scrollTop = 240; });
  check(await detail.evaluate((element) => element.scrollTop) > 0, 'long task list uses the drawer body scroll');
  await row('quest-alpha').click();
  await page.locator('.quest-plan-nodes .plan-node').first().waitFor();
  await detail.evaluate((element) => { element.scrollTop = 240; });
  const refreshPosition = await detail.evaluate((element) => element.scrollTop);
  const refresh = page.getByRole('button', { name: '刷新当前视图', exact: true });
  await refresh.waitFor();
  const blockedPlan = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('refresh did not request the existing plan endpoint')), 15000);
    planArrived = () => { clearTimeout(timeout); resolve(); };
  });
  delayPlan = true;
  await refresh.click();
  await blockedPlan;
  check(await refresh.isDisabled() && await refresh.getAttribute('aria-busy') === 'true', 'refresh disables repeat requests and exposes busy state while plan data is pending');
  releasePlan();
  await page.waitForFunction(() => !document.querySelector('.refresh-button')?.disabled);
  check(await detail.evaluate((element) => element.scrollTop) === refreshPosition, 'refreshing the same task preserves its detail scroll position');
  failPlan = true;
  await refresh.click();
  await page.getByRole('alert').filter({ hasText: 'synthetic plan refresh unavailable' }).waitFor();
  check(await page.getByRole('alert').filter({ hasText: 'synthetic plan refresh unavailable' }).isVisible(), 'refresh errors remain visible instead of appearing successful');
  await refresh.click();
  await page.locator('.quest-plan-nodes .plan-node').first().waitFor();
  check(await page.getByRole('alert').filter({ hasText: 'synthetic plan refresh unavailable' }).count() === 0, 'a successful retry recovers the plan view');
  await screenshot('forge-interactions-task-filters');
  await closeTasks();
  check(await originalTimeline.evaluate((element) => element.isConnected) && await originalDraft.evaluate((element) => element.isConnected) && await draft.inputValue() === preservedDraft, 'task interactions retain one mounted conversation and its draft');
  await page.keyboard.press('Control+,');
  const settingsNav = page.locator('.settings-nav'), settingsSearch = page.getByLabel('搜索设置');
  await settingsSearch.fill('Registry');
  check(await settingsNav.locator('.settings-nav-item').count() === 1, 'settings search filters the implemented navigation');
  await settingsSearch.evaluate((element) => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true })));
  check(await page.locator('.settings-title').innerText() === '常规', 'IME Enter does not activate a settings search result');
  await settingsSearch.press('Enter');
  check(await page.locator('.settings-title').innerText() === '模型库' && await settingsNav.getByRole('button', { name: '模型库', exact: true }).evaluate((element) => document.activeElement === element), 'Enter activates and focuses the first matching setting');
  await page.getByRole('button', { name: '清除设置搜索', exact: true }).click();
  check(await settingsNav.locator('.settings-nav-item').count() === 9 && await settingsSearch.evaluate((element) => document.activeElement === element), 'settings clear restores all entries and search focus');
  await settingsSearch.fill('no-such-fixture-setting');
  await settingsNav.getByRole('button', { name: '显示全部设置', exact: true }).click();
  check(await settingsSearch.inputValue() === '' && await settingsNav.locator('.settings-nav-item').count() === 9, 'settings empty results have a working reset');
  await settingsSearch.fill('Registry');
  await settingsSearch.press('Escape');
  check(await settingsSearch.inputValue() === '' && await settingsNav.locator('.settings-nav-item').count() === 9, 'Escape clears settings search');
  await settingsNav.getByRole('button', { name: '模型库', exact: true }).focus();
  await page.keyboard.press('Home');
  check(await page.locator('.settings-title').innerText() === '常规', 'settings Home activates the first visible category');
  await page.keyboard.press('End');
  check(await page.locator('.settings-title').innerText() === '备份与恢复', 'settings End activates the last visible category');
  await page.keyboard.press('ArrowUp');
  check(await page.locator('.settings-title').innerText() === '供应商凭据', 'settings arrows activate the adjacent category');
  await settingsNav.getByRole('button', { name: '连接', exact: true }).evaluate((element) => element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', isComposing: true, bubbles: true })));
  check(await page.locator('.settings-title').innerText() === '供应商凭据', 'IME navigation leaves settings selection unchanged');
  await settingsNav.getByRole('button', { name: '环境', exact: true }).click();
  check(await page.locator('.operator-connection-options').getAttribute('open') === null && await page.locator('.operator-advanced').getAttribute('open') === null, 'environment settings start with connection and Router decisions while optional detail stays folded');
  check(await page.getByLabel('服务地址', { exact: true }).isVisible() && await page.getByLabel('Router 模型 ID', { exact: true }).isVisible() && !(await page.getByLabel('模型 Token 上限', { exact: true }).isVisible()), 'connection and Router fields remain directly usable without budget configuration');
  await page.locator('.operator-connection-options > summary').click();
  await page.locator('.operator-settings').getByLabel('凭据引用', { exact: true }).waitFor();
  await page.locator('.operator-settings').getByLabel('凭据引用', { exact: true }).fill('invalid-credential-reference');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '有效的供应商 ID 和凭据引用' }).waitFor();
  check(mutations.length === 0, 'invalid operator configuration gives local feedback before any mutation');
  check(await page.locator('.operator-connection-options').getAttribute('open') !== null && await page.locator('.operator-advanced').getAttribute('open') !== null, 'configuration validation reveals all fields that may need correction');
  await screenshot('forge-interactions-settings-validation');
  const newProjectQuests = [
    { id: 'quest-project-a-running', title: '项目 A 执行记录', status: 'running', version: 2, plan_revision: 3, goal: 'project-a-running-proof', metadata: { project: { id: 'fixture-project-a', name: '同名项目', root: 'D:/Synthetic/Project-A' } } },
    { id: 'quest-project-a-input', title: '项目 A 补充目标', status: 'needs_input', version: 2, plan_revision: 3, goal: 'project-a-input-proof', metadata: { project_id: 'fixture-project-a', project_name: '同名项目', project_root: 'D:/Synthetic/Project-A' } },
    { id: 'quest-project-b-running', title: '项目 B 执行记录', status: 'running', version: 2, plan_revision: 3, goal: 'project-b-running-proof', project_id: 'fixture-project-b', project_name: '同名项目', project_root: 'D:/Synthetic/Project-B', metadata: {} },
  ];
  quests.push(...newProjectQuests);
  await settingsNav.getByRole('button', { name: '返回对话', exact: true }).click();
  await taskTrigger.click();
  await refresh.click();
  await page.waitForFunction(() => document.querySelectorAll('.quest-project-group').length === 3);
  const projectA = list.locator('[data-project-key="project:fixture-project-a"]'), projectB = list.locator('[data-project-key="project:fixture-project-b"]');
  check(await list.locator('.quest-project-toggle').filter({ hasText: '同名项目' }).count() === 2, 'distinct project IDs remain separate even when their display names are identical');
  check(await projectA.locator('.list-row').count() === 2 && await projectB.locator('.list-row').count() === 1, 'top-level and nested metadata group only the records belonging to the same project');
  check(await projectA.locator('.quest-project-toggle').getAttribute('title') === 'D:/Synthetic/Project-A' && await projectB.locator('.quest-project-toggle').getAttribute('title') === 'D:/Synthetic/Project-B', 'project groups disclose their separate actual paths');
  await row('quest-project-a-input').click();
  await heading.getByText('项目 A 补充目标', { exact: true }).waitFor();
  await back();
  const projectBToggle = projectB.locator('.quest-project-toggle');
  await projectBToggle.click();
  check(await projectBToggle.getAttribute('aria-expanded') === 'false' && await projectB.locator(':scope > .quest-group-content').getAttribute('inert') !== null, 'collapsing a project removes its rows from keyboard navigation');
  await projectBToggle.press('ArrowRight');
  check(await projectBToggle.getAttribute('aria-expanded') === 'true', 'ArrowRight expands a real multi-project group');
  await projectBToggle.press('ArrowLeft');
  const projectBNode = await projectB.elementHandle();
  await list.locator('.quest-project-group').filter({ has: page.locator('.quest-project-toggle').filter({ hasText: 'FORGE-interactions' }) }).locator('.quest-closed-toggle').click();
  await page.waitForFunction(() => [...document.querySelectorAll('.quest-project-group')].some((group) => group.querySelector('.quest-project-toggle')?.textContent.includes('FORGE-interactions') && group.querySelector('.quest-closed-group > .quest-group-content')?.style.height === 'auto') && document.querySelector('[data-project-key="project:fixture-project-b"] > .quest-group-content')?.style.height === '0px');
  const projectIndex = page.locator('.task-inspector .quest-body');
  await projectIndex.evaluate((element) => { element.scrollTop = 240; });
  const projectScroll = await projectIndex.evaluate((element) => element.scrollTop);
  await refresh.click();
  await page.waitForFunction(() => !document.querySelector('.refresh-button')?.disabled);
  check(await projectBNode.evaluate((element) => element.isConnected) && await projectBToggle.getAttribute('aria-expanded') === 'false', 'refreshing retains the mounted project group and its collapsed state');
  check(await projectIndex.evaluate((element) => element.scrollTop) === projectScroll && projectScroll > 0, 'refreshing project groups preserves the task index scroll position');
  check(await row('quest-project-a-input').getAttribute('aria-current') === 'page', 'refreshing grouped tasks retains the actual selected project record without opening its detail');
  await list.locator('.quest-find > summary').click();
  await search.fill('项目 B 执行记录');
  check(await list.locator('.quest-project-group').count() === 1 && await row('quest-project-b-running').count() === 1 && await projectBToggle.getAttribute('aria-expanded') === 'true', 'searching a human task title finds and expands only its matching group');
  await screenshot('forge-interactions-project-groups');
  await search.press('Escape');
  const smallList = [quests[0], quests[1], quests.find((quest) => quest.id === 'quest-finished')];
  quests.splice(0, quests.length, ...smallList);
  await refresh.click();
  await page.waitForFunction(() => document.querySelectorAll('.quest-list .list-row').length === 3);
  check(await list.locator('.quest-find').count() === 0 && await list.locator('.quest-project-toggle').count() === 0, 'a short list needs neither search controls nor a redundant project group');
  check(await row('quest-finished').isVisible() && await list.locator('.quest-closed-toggle').count() === 0, 'short lists show finished tasks directly rather than hiding a small amount of history');
  check(mutations.length === 0 && missing.length === 0, 'read-only interactions introduce no mutations or additional API endpoints');
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  check(storage.local === 0 && storage.session === 0, 'interaction state stays out of browser storage');
  return { status: 'passed', fixture: 'synthetic HTTP only', checks: checks.length, screenshots, mutations: mutations.length, requests: requests.length, storage };
}
