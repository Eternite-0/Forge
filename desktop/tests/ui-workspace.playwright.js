// Product workflow acceptance against synthetic HTTP records; never uses a live Core.
async (page, { outputDir }) => {
  const checks = [], screenshots = [], requests = [], mutations = [], confirms = [], missing = [];
  const check = (condition, message) => { if (!condition) throw new Error(message); checks.push(message); };
  const screenshot = async (name) => {
    name = `rebuild-${name}`;
    await page.mouse.move(0, 0);
    await page.waitForTimeout(220);
    await page.screenshot({ path: `${outputDir}/${name}.png`, fullPage: true });
    screenshots.push(name);
  };
  const digest = 'b'.repeat(64), teamDigest = 'c'.repeat(64);
  const quests = [
    ['alpha', 'Alpha 调研交付', 'awaiting_approval'],
    ['beta', 'Beta 数据核对', 'running'],
    ['gamma', 'Gamma 等待权限', 'blocked'],
    ['delta', 'Delta 暂停记录', 'paused'],
    ['epsilon', 'Epsilon 补充目标', 'needs_input'],
    ['finished', 'Finished 历史完成', 'completed'],
    ['failed', 'Failed 历史失败', 'failed'],
    ['cancelled', 'Cancelled 历史取消', 'cancelled'],
    ['rejected', 'Rejected 历史拒绝', 'rejected'],
  ].map(([key, title, status]) => ({ id: `quest-${key}`, title, status, version: key === 'alpha' ? 7 : 2, plan_revision: 3, goal: `合成验收目标：${title}`, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', metadata: {} }));
  const approvals = Array.from({ length: 6 }, (_, index) => ({
    id: `approval-workspace-${index}`, quest_id: index < 4 ? 'quest-alpha' : 'quest-beta', action_type: 'browser_get', risk: 'R2',
    resource_scope: `https://fixture.invalid/source-${index}`, plan_revision: 3, parameter_digest: digest,
    status: 'PENDING', reason: `合成外部请求 ${index}`, expires_at: '2026-12-01T00:00:00Z',
  }));
  const models = [
    { id: 'profile-research', display_name: 'Fixture Research Model', provider: 'fixture-provider', model_id: 'fixture-model-research', enabled: true,
      context_window: 32768, max_output_tokens: 4096, tool_calling_support: { supported: true, source: 'provider_metadata' }, structured_output_support: { supported: false, source: 'user_override' },
      capabilities: { research: 72 }, provenance: { source: 'provider_metadata' }, capability_measurements: { research: { value: 72, source: 'forge_benchmark', n_samples: 12, observed_at: '2026-02-03T04:05:06Z', confidence: .83 } } },
    { id: 'profile-unknown', display_name: 'Fixture Unknown Model', provider: 'fixture-provider', model_id: 'fixture-model-unknown', enabled: false,
      context_window: null, max_output_tokens: null, tool_calling_support: null, structured_output_support: null,
      capabilities: {}, provenance: {}, capability_measurements: {} },
  ];
  const settings = { workspace_id: 'workspace-fixture', workspace_root: 'D:/Synthetic/FORGE-workspace', provider_configured: true,
    provider_id: 'fixture-provider', router_model_id: 'fixture-router-model', context_limit: 16384, revision: 0, config: null,
    restore_fenced: false, environment_overrides: [], restart_required: false };
  let budget = { token_limit: 10000, concurrency_limit: 2, charged_tokens: 1900, known_tokens: 1200, reserved_tokens: 450, opening_tokens: 250,
    active_reservations: 1, cost_limit_microunits: null, currency: null, historical_usage: 'unknown', breached: false,
    recent_unsettled: [{ code: 'reserved', purpose: 'fixture-worker-unsettled', recover_after: null, input_reserved: 300, output_reserved: 150 }] };
  let planStatus = 'proposed', teamStatus = 'proposed';
  let alphaObjective = 'quest-alpha 证据调研', betaObjective = 'quest-beta 证据调研', betaNodeStatus = 'running';
  const plan = (id) => ({ id: `plan-${id}`, plan_revision: 3, digest, status: id === 'quest-alpha' ? planStatus : 'approved', max_total_tokens: 24000,
    max_cost_microunits: null, cost_currency: null, nodes: [{ key: 'research', objective: id === 'quest-alpha' ? alphaObjective : id === 'quest-beta' ? betaObjective : `${id} 证据调研`, status: id === 'quest-alpha' ? 'proposed' : betaNodeStatus, budget_tokens: 12000,
      success_criteria: ['交付来源明确的记录'], allowed_tools: ['file_read', 'browser_get'] }], edges: [] });
  const teams = (id) => id === 'quest-alpha' ? [{ id: 'team-alpha', status: teamStatus, plan_revision: 3, proposal_digest: teamDigest,
    body: { limits: { max_total_tokens: 24000, max_cost_microunits: 1500000, currency: 'USD' }, members: [{ role_id: 'fixture_researcher', provider_id: 'fixture-provider', model_id: 'fixture-model-research',
      tool_scope: ['file_read', 'browser_get'], estimated_tokens: 12000 }] } }] : [];
  const activity = (id) => [{ run_id: `run-${id}`, quest_id: id, worker_id: `worker-${id}`, role: `${id} Research Worker`, state: 'review_pending',
    provider_id: 'fixture-provider', model_id: 'fixture-model-research', steps: 3, tool_spent: 2, execution_mode: 'mock', created_at: '2026-01-01T00:00:03Z', output_contract: 'approved' }];
  const events = [
    { kind: 'event', event_id: 'worker-started-fixture', seq: 1, created_at: '2026-01-01T00:00:01Z', quest_id: 'quest-alpha', worker_id: 'worker-alpha', run_id: 'run-alpha',
      title: 'Worker started', status: 'worker.started', detail: null, execution_mode: 'mock' },
    { kind: 'context_archive', event_id: '1', seq: null, created_at: '2026-01-01T00:00:02Z', quest_id: 'quest-alpha', worker_id: 'worker-alpha', run_id: 'run-alpha',
      title: 'Context archived', status: 'archived', detail: { scope: 'worker', source_count: 2 }, execution_mode: 'mock' },
  ];
  const messages = [{ id: 'workspace-message', role: 'assistant', content: '合成工作空间已准备好。任务和执行记录均为测试数据。', created_at: '2026-01-01T00:00:00Z', metadata: { execution_mode: 'mock' } }];
  page.on('dialog', async (dialog) => { confirms.push(dialog.message()); await dialog.accept(); });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    requests.push({ method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams) });
    let body, status = 200;
    if (request.method() !== 'GET') {
      const payload = JSON.parse(request.postData() || '{}');
      mutations.push({ path: url.pathname, payload });
      if (url.pathname === '/api/quests/quest-alpha/plan/approve') {
        planStatus = 'approved'; quests[0].status = 'approved'; quests[0].version += 1; body = plan('quest-alpha');
      } else if (url.pathname === '/api/quests/quest-alpha/teams/approve') { teamStatus = 'approved'; body = { ok: true }; }
      else { status = 404; missing.push(url.pathname); body = { detail: 'unexpected fixture mutation' }; }
    } else if (url.pathname === '/health') body = { session: 'workspace-fixture-session' };
    else if (url.pathname === '/api/conversation') body = { messages, next_before: null, has_more: false };
    else if (url.pathname === '/api/timeline') body = { items: events, next_before: null, has_more: false };
    else if (url.pathname === '/api/quests') body = { items: quests, next_before: null, has_more: false };
    else if (/^\/api\/quests\/[^/]+$/.test(url.pathname)) body = { quest: quests.find((quest) => quest.id === url.pathname.split('/')[3]), workers: [] };
    else if (/^\/api\/quests\/[^/]+\/plan$/.test(url.pathname)) body = plan(url.pathname.split('/')[3]);
    else if (/^\/api\/quests\/[^/]+\/teams$/.test(url.pathname)) body = teams(url.pathname.split('/')[3]);
    else if (/^\/api\/quests\/[^/]+\/grants$/.test(url.pathname)) body = [];
    else if (url.pathname === '/api/approvals/pending') body = { items: approvals, next_before: null, has_more: false };
    else if (url.pathname === '/api/approvals') body = { items: approvals.filter((item) => item.quest_id === url.searchParams.get('quest_id')), next_before: null, has_more: false };
    else if (url.pathname === '/api/activity') body = { items: activity(url.searchParams.get('quest_id') || 'quest-alpha'), next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = settings;
    else if (url.pathname === '/api/budget') body = budget;
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname.endsWith('/plan/history')) body = [];
    else if (url.pathname === '/api/models') body = models;
    else if (url.pathname.startsWith('/api/model-intelligence/')) body = { health: {}, performance: { measurements: {} }, capabilities: { measurements: {} } };
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { status = 404; missing.push(url.pathname); body = { detail: 'fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  const rail = page.getByLabel('主导航');
  const workspaceSelector = rail.getByRole('button', { name: '查看当前工作空间', exact: true });
  await page.waitForFunction(() => document.querySelector('.current-project')?.textContent.includes('FORGE-workspace'));
  check(await workspaceSelector.getAttribute('title') === settings.workspace_root, 'workspace selector uses the actual workspace path');
  check(await rail.getByRole('button', { name: '对话', exact: true }).count() === 1 && await rail.getByRole('button', { name: '长期对话', exact: true }).count() === 0, 'sidebar has one conversation entry');
  check(await rail.locator('.sidebar-quest, .sidebar-attention').count() === 0, 'sidebar stays focused on workspace and primary destinations');
  await workspaceSelector.click();
  const workspaceDialog = page.getByRole('dialog', { name: '当前工作空间', exact: true });
  await workspaceDialog.waitFor();
  const currentWorkspace = workspaceDialog.getByRole('button', { name: '使用当前工作空间', exact: true });
  check(await workspaceDialog.getByRole('button').count() === 1 && (await currentWorkspace.innerText()).includes(settings.workspace_root), 'workspace picker exposes only the actual current workspace');
  check(await currentWorkspace.evaluate((element) => document.activeElement === element), 'workspace picker focuses its current workspace');
  await page.keyboard.press('Escape');
  check(!(await workspaceDialog.isVisible()) && await workspaceSelector.evaluate((element) => document.activeElement === element), 'Escape closes workspace picker and restores trigger focus');
  await workspaceSelector.press('ArrowDown');
  await workspaceDialog.waitFor();
  await currentWorkspace.click();
  check(!(await workspaceDialog.isVisible()) && await workspaceSelector.getAttribute('title') === settings.workspace_root, 'selecting the current workspace closes the picker without changing its directory');
  await workspaceSelector.click();
  await workspaceDialog.waitFor();
  await page.locator('.conversation-shell .topbar').click({ position: { x: 10, y: 10 } });
  check(!(await workspaceDialog.isVisible()) && await workspaceSelector.getAttribute('aria-expanded') === 'false', 'outside click closes the workspace picker');
  await workspaceSelector.click();
  await workspaceDialog.waitFor();
  await page.keyboard.press('Control+,');
  await page.locator('.settings-view').waitFor();
  await page.getByLabel('搜索设置').waitFor();
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '搜索设置');
  check(!(await workspaceDialog.isVisible()) && await page.getByLabel('搜索设置').evaluate((element) => document.activeElement === element), 'opening settings closes the retained workspace picker and moves focus to settings search');
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await workspaceSelector.waitFor();
  check(!(await workspaceDialog.isVisible()) && await workspaceSelector.getAttribute('aria-expanded') === 'false', 'returning from settings leaves the workspace picker closed');
  for (const name of ['新聊天', '插件', 'Git', '导入', '已归档的聊天', '电脑操控']) {
    check(await page.getByRole('button', { name, exact: true }).count() === 0, `no decorative ${name} destination`);
  }
  const timeline = page.getByLabel('对话历史');
  const activityLabels = await timeline.locator('.event-group > summary .event-label').allTextContents();
  check(activityLabels.join('').includes('归档') && !activityLabels.join('').includes('压缩'), 'source-bound context archiving is labelled as archiving');
  check(!activityLabels.join('').includes('创建聊天'), 'private Worker execution is not presented as creating a user chat');
  const updates = timeline.locator('.timeline-updates');
  check((await updates.innerText()).includes('已加载的审批中另有 3 项待处理，审批页按任务筛选') && await updates.getByRole('button', { name: '按任务查看审批', exact: true }).isVisible(), 'additional pending approvals disclose the loaded remainder and Quest scope');
  check(await updates.locator(':scope > button.timeline-update').count() === 3 && (await updates.locator(':scope > button.timeline-update').allTextContents()).join(' ').includes('Epsilon 补充目标'), 'all three loaded tasks needing attention remain visible beside approvals');
  check(!(await updates.innerText()).includes('Beta 数据核对') && !(await updates.innerText()).includes('Delta 暂停记录'), 'ordinary running and paused tasks do not repeat at the end of a chat');
  await page.getByLabel('消息内容').fill('workspace draft survives every task and settings view');
  await timeline.evaluate((element) => { element.scrollTop = 0; });
  await screenshot('forge-workspace-conversation');
  await updates.getByRole('button', { name: '按任务查看审批', exact: true }).click();
  await page.locator('.workspace-panel .data-row').filter({ hasText: 'https://fixture.invalid/source-0' }).waitFor();
  check(await page.getByLabel('任务范围').inputValue() === 'quest-alpha' && await page.locator('.workspace-panel .data-row').count() === 4, 'additional approval destination opens the first actual Quest and its scoped approvals');
  await page.getByLabel('任务范围').selectOption('quest-beta');
  await page.locator('.workspace-panel .data-row').filter({ hasText: 'https://fixture.invalid/source-4' }).waitFor();
  check(await page.locator('.workspace-panel .data-row').count() === 2, 'approval scope can switch to a second actual Quest');
  await page.locator('.workspace-tabs').getByRole('button', { name: '后台任务', exact: true }).click();
  const taskList = page.locator('.quest-list');
  await taskList.getByRole('button', { name: /Beta 数据核对/ }).waitFor();
  check(!(await taskList.innerText()).includes('quest-beta') && !(await taskList.getByRole('button', { name: /Beta 数据核对/ }).getAttribute('aria-label')).includes('quest-beta'), 'visible text and accessible task names do not expose internal identifiers');
  check(await taskList.getByRole('button', { name: /Beta 数据核对/ }).getByText('运行中', { exact: true }).count() === 1 &&
    await taskList.getByRole('button', { name: /Delta 暂停记录/ }).getByText('已暂停', { exact: true }).count() === 1,
    'running and paused tasks remain separately visible with their real state in the task manager');
  await page.locator('.quest-list .list-row').filter({ hasText: quests[0].title }).click();
  await page.locator('.quest-detail').getByRole('heading', { name: quests[0].title, exact: true }).waitFor();
  await page.waitForFunction(() => Math.abs(document.querySelector('.rail')?.getBoundingClientRect().width - 260) <= 1);
  check(await rail.locator('.workspace-selector-wrap').isVisible(), 'task details retain the single workspace sidebar');
  check(!(await page.locator('.quest-list').isVisible()) && await page.locator('.quest-detail').isVisible() && await page.getByLabel('消息内容').isVisible(), 'task drawer shows task detail without repeating the list beside the preserved conversation');
  check((await page.locator('.quest-overview').innerText()).includes(quests[0].goal), 'opening a Quest shows its actual goal');
  const tabs = page.locator('.workspace-tabs');
  check((await tabs.getByRole('button').allTextContents()).join('/') === '后台任务/任务团队/运行活动/审批中心', 'task workspace contains the four product tabs');
  await screenshot('forge-workspace-plan');
  const planDetails = page.locator('.quest-plan > .quest-technical');
  check(!(await page.locator('.quest-detail').innerText()).includes('quest-alpha'), 'task detail keeps internal identity hidden until requested');
  await planDetails.locator(':scope > summary').click();
  check((await planDetails.innerText()).includes('quest-alpha'), 'expanded technical details retain the actual task ID');
  const originalPlanDetails = await planDetails.elementHandle();
  alphaObjective = '显式刷新后的证据调研';
  await page.getByRole('button', { name: '刷新当前视图', exact: true }).click();
  await page.locator('.quest-plan-nodes').getByText(`1. ${alphaObjective}`, { exact: true }).waitFor();
  check(await originalPlanDetails.evaluate((element) => element.isConnected && element.open), 'explicit plan refresh updates source data without replacing expanded technical details');
  await planDetails.locator(':scope > summary').click();
  await page.getByRole('button', { name: '批准此计划', exact: true }).click();
  await page.getByRole('button', { name: '批准此计划', exact: true }).waitFor({ state: 'detached' });
  check(mutations[0]?.path.endsWith('/plan/approve') && mutations[0].payload.expected_version === 7 && mutations[0].payload.plan_revision === 3 && mutations[0].payload.digest === digest, 'plan approval retains its Quest version, revision and digest');
  check(confirms[0]?.includes(digest) && confirms[0]?.includes('计划版本：3'), 'plan approval confirms the exact contract');
  await tabs.getByRole('button', { name: '任务团队', exact: true }).click();
  await page.getByText('fixture_researcher', { exact: true }).waitFor();
  check(await page.getByLabel('任务范围').inputValue() === 'quest-alpha', 'team tab preserves the selected Quest');
  check((await page.locator('.workspace-panel .data-row').first().innerText()).includes('1500000 微单位 · USD'), 'team costs use the proposal limits currency field');
  await page.getByRole('button', { name: '批准此团队', exact: true }).click();
  await page.getByRole('button', { name: '批准此团队', exact: true }).waitFor({ state: 'detached' });
  check(mutations[1]?.path.endsWith('/teams/approve') && mutations[1].payload.expected_version === 8 && mutations[1].payload.plan_revision === 3 && mutations[1].payload.proposal_digest === teamDigest, 'team approval binds the refreshed Quest version and exact proposal');
  check(confirms[1]?.includes(teamDigest) && confirms[1]?.includes('任务版本：8'), 'team approval confirms the refreshed contract');
  await screenshot('forge-workspace-team');
  await tabs.getByRole('button', { name: '运行活动', exact: true }).click();
  await page.locator('.workspace-panel .data-row').getByText(/^Research Worker/).first().waitFor();
  check(await page.getByLabel('任务范围').inputValue() === 'quest-alpha', 'activity tab preserves the selected Quest');
  check((await page.locator('.workspace-panel .data-row').first().innerText()).includes('待验证'), 'a review-pending Worker is not displayed as completed');
  await tabs.getByRole('button', { name: '审批中心', exact: true }).click();
  await page.locator('.workspace-panel .data-row').filter({ hasText: 'https://fixture.invalid/source-0' }).waitFor();
  check(await page.getByLabel('任务范围').inputValue() === 'quest-alpha', 'approval tab preserves the selected Quest');
  check(await page.locator('.workspace-panel .data-row').count() === 4, 'approval records belong to the selected Quest');
  check((await page.locator('.workspace-panel .data-row').first().innerText()).includes('R2'), 'approval risk preserves the real backend risk code');
  await screenshot('forge-workspace-approvals');
  await tabs.getByRole('button', { name: '后台任务', exact: true }).click();
  check(await taskList.isVisible() && !await page.locator('.quest-detail').isVisible(), 'returning to the task tab opens the task list without forcing a detail view');
  await page.locator('.quest-list .list-row').filter({ hasText: quests[1].title }).click();
  await page.locator('.quest-detail').getByRole('heading', { name: quests[1].title, exact: true }).waitFor();
  const betaReads = requests.filter((request) => request.path === '/api/quests/quest-beta/plan').length;
  quests[1].version = 3;
  betaObjective = '轮询版本更新后的证据调研';
  betaNodeStatus = 'succeeded';
  await page.locator('.quest-plan-nodes').getByText(`1. ${betaObjective}`, { exact: true }).waitFor();
  check((await page.locator('.quest-technical').textContent()).includes('任务版本3') && requests.filter((request) => request.path === '/api/quests/quest-beta/plan').length > betaReads, 'a polled Quest version refreshes its plan through the existing endpoint');
  check((await page.locator('.quest-plan-nodes .state-label').innerText()).includes('执行成功'), 'polled plan nodes display the updated execution state');
  await tabs.getByRole('button', { name: '运行活动', exact: true }).click();
  await page.locator('.workspace-panel .data-row').getByText(/^Research Worker/).first().waitFor();
  check(await page.getByLabel('任务范围').inputValue() === 'quest-beta', 'selecting another Quest changes the execution scope');
  check(requests.some((request) => request.path === '/api/quests/quest-alpha/teams') && requests.some((request) => request.path === '/api/activity' && request.query.quest_id === 'quest-alpha') && requests.some((request) => request.path === '/api/activity' && request.query.quest_id === 'quest-beta') && requests.some((request) => request.path === '/api/approvals' && request.query.quest_id === 'quest-alpha'), 'task tabs use the existing Quest-scoped API requests');
  await rail.getByRole('button', { name: '对话', exact: true }).click();
  check(await page.getByLabel('消息内容').inputValue() === 'workspace draft survives every task and settings view', 'one long-lived conversation retains its draft across task views');
  await page.keyboard.press('Control+,');
  await page.locator('.settings-view').waitFor();
  const settingsNav = page.locator('.settings-nav');
  check(await settingsNav.locator('.settings-nav-item').count() === 9 && await settingsNav.getByRole('button', { name: '记忆', exact: true }).count() === 1 && await settingsNav.getByRole('button', { name: '触发器', exact: true }).count() === 1, 'settings includes the nine implemented categories, including memory and triggers');
  await settingsNav.getByRole('button', { name: '模型库', exact: true }).click();
  await page.getByText(models[0].display_name, { exact: true }).waitFor();
  const modelCard = (name) => page.locator('.settings-page .data-row').filter({ has: page.getByText(name, { exact: true }) });
  const research = modelCard(models[0].display_name), unknown = modelCard(models[1].display_name);
  check(await research.count() === 1 && await unknown.count() === 1, 'model cards use the actual Registry display names');
  const definition = async (container, label) => container.locator('dt').filter({ hasText: label }).locator('xpath=following-sibling::dd[1]').innerText();
  check((await research.innerText()).includes(models[0].model_id) && (await research.innerText()).includes(models[0].provider), 'model profile identity remains separate from its display name');
  check(await research.locator('.model-profile-details').getAttribute('open') === null && await unknown.locator('.model-profile-details').getAttribute('open') === null, 'model identity and protocol summary start compact with full measurements folded');
  check(!(await research.locator('.model-profile-capabilities').isVisible()) && !(await research.locator('.measurement-list').first().isVisible()), 'the model list keeps protocol and measurement detail out of the default reading path');
  await screenshot('forge-workspace-models-compact');
  await research.getByText('档案与能力详情', { exact: true }).click();
  await unknown.getByText('档案与能力详情', { exact: true }).click();
  check(/^支持/.test(await definition(research, /^工具调用(?:支持)?$/)), 'Registry tool support is shown as explicitly supported');
  check(/^不支持/.test(await definition(research, /^结构化输出(?:支持)?$/)), 'Registry structured-output support preserves an explicit false');
  check((await definition(unknown, /^工具调用(?:支持)?$/)).includes('未知') && (await definition(unknown, /^结构化输出(?:支持)?$/)).includes('未知'), 'missing protocol declarations remain unknown');
  const measurements = (await research.locator('.measurement-list').allTextContents()).join(' ');
  check(measurements.includes('12') && measurements.includes('2026'), 'capability evidence includes sample count and observation time');
  await screenshot('forge-workspace-models');
  await research.getByText('档案与能力详情', { exact: true }).click();
  await unknown.getByText('档案与能力详情', { exact: true }).click();
  await page.getByRole('button', { name: '添加模型', exact: true }).click();
  const modelForm = page.locator('.model-form');
  check(await modelForm.getByLabel('显示名称', { exact: true }).isVisible() && await modelForm.getByLabel('模型 ID', { exact: true }).isVisible() && await modelForm.getByLabel('供应商 ID', { exact: true }).isVisible(), 'adding a model begins with the name and service identifiers');
  check(await modelForm.locator('.model-options').getAttribute('open') === null && !(await modelForm.getByLabel('档案 ID', { exact: true }).isVisible()), 'model identifiers and protocol options start folded');
  check(/^model_[a-f0-9]{32}$/.test(await modelForm.getByLabel('档案 ID', { exact: true }).inputValue()), 'a new model receives a usable profile identifier without manual entry');
  await screenshot('forge-workspace-model-editor-compact');
  const mutationsBeforeValidation = mutations.length;
  await modelForm.getByRole('button', { name: '保存', exact: true }).click();
  await modelForm.getByRole('alert').filter({ hasText: '请填写显示名称' }).waitFor();
  check(mutations.length === mutationsBeforeValidation && await modelForm.locator('.model-options').getAttribute('open') !== null, 'invalid model input is reported locally and reveals the relevant options');
  await screenshot('forge-workspace-model-editor');
  await modelForm.getByRole('button', { name: '取消', exact: true }).click();
  check(await modelForm.count() === 0 && await research.count() === 1, 'cancelling a model draft returns to the preserved model list');
  await settingsNav.getByRole('button', { name: '存储', exact: true }).click();
  await page.getByRole('heading', { name: 'Token 账本', exact: true }).waitFor();
  const valueFor = (label) => page.locator('.settings-row').filter({ has: page.locator('.settings-row-label').filter({ hasText: new RegExp(`^${label}$`) }) }).locator('.settings-row-value');
  for (const [label, expected] of [['已确认用量', '1,200'], ['未结算预留', '450'], ['迁移期上界', '250'], ['保守计入 Token', '1,900'], ['Token 上限', '10,000'], ['并发占用', '1 / 2'], ['费用上限', '未知']]) {
    check(await valueFor(label).innerText() === expected, `budget renders the supplied ${label} without conflating fields`);
  }
  check((await page.locator('.settings-content').innerText()).includes('不是实际消耗'), 'conservative admission usage is distinguished from actual consumption');
  check((await page.locator('.budget-unsettled-record').innerText()).includes('fixture-worker-unsettled'), 'unsettled reservations retain their actual purpose');
  check((await page.locator('.settings-content').innerText()).includes('请求超时不表示远端已取消'), 'a timeout is not represented as automatic cancellation or refund');
  await screenshot('forge-workspace-budget');
  budget = { token_limit: 0, concurrency_limit: 2, charged_tokens: 0, known_tokens: null, reserved_tokens: 0, active_reservations: 0,
    cost_limit_microunits: 0, currency: 'USD', recent_unsettled: null };
  await page.getByRole('button', { name: '应用菜单', exact: true }).click();
  await page.getByRole('menuitem', { name: '刷新当前视图', exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('.settings-row')].some((row) => row.querySelector('.settings-row-label')?.textContent === '已确认用量' && row.querySelector('.settings-row-value')?.textContent === '未知'));
  check(await valueFor('已确认用量').innerText() === '未知' && await valueFor('迁移期上界').innerText() === '未知', 'missing and null budget measurements stay unknown');
  check(await valueFor('未结算预留').innerText() === '0' && await valueFor('保守计入 Token').innerText() === '0', 'explicit zero budget measurements stay zero');
  check(await valueFor('费用上限').innerText() === '0 USD', 'an explicit zero currency ceiling is distinguished from unknown cost');
  check((await page.locator('.settings-content').innerText()).includes('当前预算未开放'), 'a zero token ceiling explains why model calls are restricted');
  await screenshot('forge-workspace-budget-unknown');
  await rail.getByRole('button', { name: '后台任务', exact: true }).click();
  await taskList.getByRole('button', { name: /Beta 数据核对/ }).click();
  await page.locator('.quest-detail').getByRole('heading', { name: quests[1].title, exact: true }).waitFor();
  check(await page.locator('.quest-detail').getByRole('heading', { name: quests[1].title, exact: true }).isVisible(), 'task detail stays reachable by its title after returning from settings');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(220);
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'mobile task workspace has no horizontal document overflow');
  await page.locator('.quest-overview').scrollIntoViewIfNeeded();
  await screenshot('forge-workspace-plan-small');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(220);
  await rail.getByRole('button', { name: '记忆中心', exact: true }).click();
  await page.getByLabel('搜索记忆').waitFor();
  check(await page.locator('.settings-view').count() === 1 && await page.locator('.settings-title').innerText() === '记忆', 'memory opens its implemented settings category');
  await rail.getByRole('button', { name: '对话', exact: true }).click();
  check(await page.getByLabel('消息内容').count() === 1 && await page.getByLabel('消息内容').inputValue() === 'workspace draft survives every task and settings view', 'all destinations share one preserved conversation and draft');
  check(mutations.length === 2 && missing.length === 0, 'workflow uses only the expected mocked Core endpoints and two explicit approvals');
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  check(storage.local === 0 && storage.session === 0, 'workflow does not persist browser state');
  return { status: 'passed', fixture: 'synthetic HTTP only', checks: checks.length, mutations, screenshots, storage };
}
