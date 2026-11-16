// Synthetic HTTP acceptance. Provider discovery and probes are fulfilled locally.
async (page, { outputDir }) => {
  const checks = [], mutations = [], missing = [], calls = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const models = [
    { id: 'profile-enabled', display_name: 'Enabled fixture', provider: 'fixture', model_id: 'model-enabled', enabled: true },
    { id: 'profile-disabled', display_name: 'Disabled fixture', provider: 'fixture', model_id: 'model-disabled', enabled: false },
  ];
  let failEvent = true, failSnapshot = false;
  let received = null;
  const rate = (value, samples) => ({ value, n_samples: samples, confidence: { method: 'wilson_95', lower: Math.max(0, value - 0.2), upper: Math.min(1, value + 0.2) } });
  const snapshot = {
    health: { recommendation: 'available', unknown_attempts: 0, in_flight_calls: 1, consecutive_failures: 0, observed_at: '2026-11-16T08:00:00Z', source_refs: [] },
    performance: { measurements: { transport_success_rate: rate(0.75, 4), task_success_rate: rate(0.25, 4) }, unknown_usage_attempts: 0, source_refs: ['model_observation:fixture'] },
    capabilities: { suite_version: 'fixture-micro-v1', measurements: { mathematics: rate(1, 1), vision: null }, measured_at: '2026-11-16T08:00:00Z', source_refs: ['benchmark:fixture'] },
  };
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS' };
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers });
    calls.push(`${method} ${url.pathname}`);
    const payload = request.postData() ? JSON.parse(request.postData()) : null;
    if (method !== 'GET') mutations.push({ path: url.pathname, payload });
    let body, status = 200;
    if (method === 'POST' && url.pathname === '/api/triggers/sources') body = { id: payload.source_id, ...payload };
    else if (method === 'POST' && url.pathname === '/api/triggers') {
      if (failEvent) { failEvent = false; status = 503; body = { detail: 'synthetic ingress failure' }; }
      else { const duplicate = received !== null; received = payload; body = { id: 'trigger-created', ...payload, status: 'pending', reason: 'candidate', preview: JSON.stringify(payload.payload), duplicate }; }
    } else if (method === 'POST' && url.pathname === '/api/triggers/schedules') body = { id: 'timer-created', ...payload, next_at: payload.due_at, enabled: 1 };
    else if (method === 'POST' && url.pathname === '/api/triggers/file-watches') body = { id: 'watch-created', source_id: payload.source_id, relative_path: payload.path, thread_key: payload.thread_key || payload.path };
    else if (method === 'POST' && url.pathname === '/api/model-intelligence/discover') {
      const added = { id: 'profile-discovered', display_name: 'Discovered fixture', model_id: 'model-new', provider: 'fixture', enabled: true };
      models.push(added); body = [added];
    } else if (method === 'POST' && url.pathname === '/api/model-intelligence/benchmark') body = { id: 'benchmark-created', mode: 'live', suite_version: 'fixture-micro-v1', created_at: '2026-11-16T08:00:00Z', results: payload.dimensions.map((dimension) => ({ dimension, status: 'judged', passed: true, elapsed_seconds: 0.25 })) };
    else if (url.pathname.startsWith('/api/model-intelligence/')) { if (failSnapshot) { status = 503; body = { detail: 'synthetic snapshot failure' }; } else body = snapshot; }
    else if (url.pathname === '/health') body = { session: 'forge-settings' };
    else if (url.pathname === '/api/settings') body = { revision: 1, workspace_id: 'settings-workspace', workspace_root: 'D:/Fixture/Settings', provider_configured: true, provider_id: 'fixture', router_model_id: 'fixture-router', restore_fenced: false, config: null };
    else if (url.pathname === '/api/conversation') body = { messages: [], has_more: false };
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/triggers') body = [{ id: 'trigger-pending', source_id: 'external-fixture', external_id: 'pending-id', thread_key: '待处理构建', status: 'pending', reason: 'candidate', preview: '{"content":"构建完成，请核对报告"}', duplicate: false }];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === '/api/models') body = models;
    else if (url.pathname === '/api/projects/discovered' || url.pathname === '/api/events') body = [];
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null };
    else if (url.pathname === '/api/budget') body = { token_limit: 10000, concurrency_limit: 2 };
    else if (['/api/quests', '/api/approvals/pending', '/api/approvals', '/api/activity', '/api/timeline'].includes(url.pathname)) body = { items: [], has_more: false };
    else { missing.push(`${method} ${url.pathname}`); status = 404; body = { detail: 'No synthetic settings fixture' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto(process.env.FORGE_TEST_URL || 'http://127.0.0.1:5173/');
  await page.getByLabel('Core 启动密钥').fill('ab'.repeat(32));
  await page.getByRole('button', { name: '连接', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.current-project')?.textContent.includes('Settings'));
  await page.keyboard.press('Control+,');
  const nav = page.locator('.settings-nav');
  await nav.getByRole('button', { name: '触发器', exact: true }).click();
  await page.getByText('构建完成，请核对报告', { exact: true }).waitFor();
  check(mutations.length === 0, 'opening trigger settings only reads pending events');
  const triggers = page.locator('.trigger-settings');
  const section = (title) => triggers.locator('.settings-block').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
  const sources = section('事件源');
  await sources.getByLabel('事件源标识', { exact: true }).fill('timer-fixture');
  await sources.getByLabel(/^事件源类型/).selectOption('timer');
  await sources.getByLabel(/^接收规则/).selectOption('explicit');
  await sources.getByLabel('允许的来源用户', { exact: true }).fill('alice\nbob');
  await sources.getByRole('button', { name: '注册事件源', exact: true }).click();
  await sources.getByRole('status').waitFor();
  check(JSON.stringify(mutations[0].payload) === JSON.stringify({ source_id: 'timer-fixture', kind: 'timer', mode: 'explicit', allowed_actors: ['alice', 'bob'] }), 'source registration retains its type and explicit admission actors');
  const timer = section('定时任务');
  check(await timer.getByLabel('定时事件源', { exact: true }).inputValue() === 'timer-fixture', 'a timer source populates the matching semantic form');
  await timer.getByLabel('定时主题标识', { exact: true }).fill('daily-report');
  await timer.getByLabel('定时任务内容', { exact: true }).fill('核对日报');
  await timer.getByLabel('执行时间（本地）', { exact: true }).fill('2026-11-20T09:30');
  await timer.getByLabel('重复间隔（秒）', { exact: true }).fill('60');
  const expectedSeconds = await page.evaluate(() => new Date('2026-11-20T09:30').getTime() / 1000);
  await timer.getByRole('button', { name: '创建定时任务', exact: true }).click();
  await timer.getByText('定时任务已创建', { exact: true }).waitFor();
  const scheduled = mutations.find((entry) => entry.path === '/api/triggers/schedules').payload;
  check(scheduled.due_at === expectedSeconds && scheduled.due_at < 1e11 && scheduled.interval_seconds === 60 && scheduled.payload.content === '核对日报', 'timer input sends epoch seconds and a numeric repeat interval');
  await sources.getByLabel('事件源标识', { exact: true }).fill('file-fixture');
  await sources.getByLabel(/^事件源类型/).selectOption('file');
  await sources.getByLabel(/^接收规则/).selectOption('candidate');
  await sources.getByLabel('允许的来源用户', { exact: true }).fill('');
  await sources.getByRole('button', { name: '注册事件源', exact: true }).click();
  await sources.getByText('已注册事件源 file-fixture', { exact: true }).waitFor();
  const watch = section('文件监听');
  await watch.getByLabel('相对文件路径', { exact: true }).fill('reports/latest.txt');
  await watch.getByRole('button', { name: '创建文件监听', exact: true }).click();
  await watch.getByText('文件监听已创建', { exact: true }).waitFor();
  const watched = mutations.find((entry) => entry.path === '/api/triggers/file-watches').payload;
  check(watched.source_id === 'file-fixture' && watched.path === 'reports/latest.txt' && watched.thread_key === null, 'file watch uses the registered file source and an explicit relative path');
  const ingress = section('接收事件');
  await ingress.getByLabel('事件源标识', { exact: true }).fill('external-fixture');
  await ingress.getByLabel('主题标识', { exact: true }).fill('build-thread');
  await ingress.getByLabel(/^内容格式/).selectOption('json');
  await ingress.getByLabel('事件内容', { exact: true }).fill('{"message":"build complete","build":7}');
  await ingress.locator('summary').filter({ hasText: '事件来源与去重' }).click();
  await ingress.getByLabel('来源用户', { exact: true }).fill('alice');
  await ingress.getByLabel('此事件明确发给助手', { exact: true }).check();
  const originalId = await ingress.getByLabel('事件标识', { exact: true }).inputValue();
  await ingress.getByRole('button', { name: '接收此事件', exact: true }).click();
  await ingress.getByRole('alert').filter({ hasText: 'synthetic ingress failure' }).waitFor();
  check(await ingress.getByLabel('事件标识', { exact: true }).inputValue() === originalId && (await ingress.getByLabel('事件内容', { exact: true }).inputValue()).includes('build complete'), 'failed ingress preserves its original identity and complete draft');
  await ingress.getByRole('button', { name: '接收此事件', exact: true }).click();
  await ingress.getByRole('status').filter({ hasText: '事件已接收' }).waitFor();
  await ingress.getByRole('button', { name: '接收此事件', exact: true }).click();
  await ingress.getByRole('status').filter({ hasText: '事件已接收' }).waitFor();
  await ingress.locator('.settings-result-record').filter({ hasText: 'build-thread' }).locator('dd').filter({ hasText: /^是$/ }).waitFor({ state: 'attached' });
  const ingressWrites = mutations.filter((entry) => entry.path === '/api/triggers');
  check(ingressWrites.length === 3 && ingressWrites.every((entry) => entry.payload.external_id === originalId), 'retry and repeat keep one durable event identity');
  check(ingressWrites[1].payload.payload.build === 7 && ingressWrites[1].payload.actor === 'alice' && ingressWrites[1].payload.addressed === true, 'structured ingress retains actor, addressing and JSON object fields');
  const light = await page.locator('.settings-content').evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.locator('.settings-content').evaluate((element) => element.scrollTop = 0);
  await page.screenshot({ path: `${outputDir}/forge-triggers-light.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'dark' });
  const dark = await page.locator('.settings-content').evaluate((element) => getComputedStyle(element).backgroundColor);
  check(light !== dark && dark === 'rgb(17, 17, 17)', 'trigger settings follow the system theme without a JS theme toggle');
  const settleFocus = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const focusedItemVisible = (label) => nav.getByRole('button', { name: label, exact: true }).evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const strip = element.closest('.settings-nav-sections').getBoundingClientRect();
    return document.activeElement === element && bounds.left >= Math.max(0, strip.left) - 1 &&
      bounds.right <= Math.min(innerWidth, strip.right) + 1 && bounds.top >= strip.top - 1 && bounds.bottom <= strip.bottom + 1;
  });
  const writesBeforeFocus = mutations.length;
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.waitForFunction(() => document.querySelector('.settings-content').clientWidth >= innerWidth - 50);
    check(await page.locator('.settings-content').evaluate(element => element.clientWidth >= innerWidth - 50), `${width}px settings retain usable content width`);
    await nav.getByRole('searchbox', { name: '搜索设置', exact: true }).focus();
    for (let index = 0; index < 7; index += 1) await page.keyboard.press('Tab');
    await settleFocus();
    check(await focusedItemVisible('触发器'), `${width}px Tab reveals the entire focused settings entry within its scroll strip`);
    await page.keyboard.press('Shift+Tab');
    await settleFocus();
    check(await focusedItemVisible('记忆'), `${width}px reverse Tab preserves order and reveals the previous entry`);
    await page.keyboard.press('Tab');
    await settleFocus();
    check(await focusedItemVisible('触发器'), `${width}px forward Tab returns to the same genuine settings entry`);
    check(await page.locator('.settings-content').evaluate(element => element.scrollWidth <= element.clientWidth) &&
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}px trigger content and document do not overflow horizontally`);
    await page.screenshot({ path: `${outputDir}/forge-triggers-${width}-dark.png`, fullPage: true });
  }
  check(mutations.length === writesBeforeFocus, 'keyboard focus navigation does not change settings or submit a request');
  await nav.getByRole('button', { name: '触发器', exact: true }).focus();
  await nav.getByRole('searchbox', { name: '搜索设置', exact: true }).focus();
  await settleFocus();
  check(await nav.getByRole('searchbox', { name: '搜索设置', exact: true }).evaluate(element => document.activeElement === element), 'deferred reveal never steals a later deliberate focus');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: 'light' });
  await nav.getByRole('button', { name: '模型库', exact: true }).click();
  const intelligence = page.locator('.model-intelligence');
  await intelligence.locator('.model-observations').waitFor();
  check(!mutations.some((entry) => entry.path === '/api/model-intelligence/benchmark'), 'opening model settings never starts a paid benchmark');
  const measurements = intelligence.locator('.model-measurement');
  check((await measurements.filter({ hasText: '请求传输成功率' }).innerText()).includes('75%') && (await measurements.filter({ hasText: '任务成功率' }).innerText()).includes('25%'), 'transport and task success rates remain separate with recorded sample counts');
  check((await measurements.filter({ hasText: '视觉' }).innerText()).includes('未知'), 'unsupported visual capability is never filled with a fake zero');
  await intelligence.getByRole('button', { name: '从供应商发现模型', exact: true }).click();
  await intelligence.getByText('已发现 1 个新模型，模型库已刷新。', { exact: true }).waitFor();
  await intelligence.getByLabel(/^观测模型/).locator('option[value="profile-discovered"]').waitFor({ state: 'attached' });
  check(mutations.find((entry) => entry.path === '/api/model-intelligence/discover').payload === null, 'model discovery sends the original body-free POST and refreshes the model list');
  await intelligence.getByLabel(/^观测模型/).selectOption('profile-disabled');
  await intelligence.locator('.model-observations').waitFor();
  check(await intelligence.getByRole('button', { name: '开始固定样例测量', exact: true }).isDisabled(), 'a disabled model cannot start a live probe');
  await intelligence.getByLabel(/^观测模型/).selectOption('profile-enabled');
  await intelligence.locator('.model-observations').waitFor();
  await intelligence.getByRole('button', { name: '开始固定样例测量', exact: true }).click();
  await intelligence.getByText('固定样例测量已记录。请查看各维度结果。', { exact: true }).waitFor();
  const probes = mutations.filter((entry) => entry.path === '/api/model-intelligence/benchmark');
  check(probes.length === 1 && probes[0].payload.profile_id === 'profile-enabled' && JSON.stringify(probes[0].payload.dimensions) === '["mathematics"]', 'only the explicit click starts one probe with the selected profile and exact dimensions');
  failSnapshot = true;
  await intelligence.getByRole('button', { name: '刷新模型观测', exact: true }).click();
  await intelligence.getByRole('alert').filter({ hasText: 'synthetic snapshot failure' }).waitFor();
  check(await intelligence.locator('.model-observations').count() === 0, 'a failed observation refresh clears the previous snapshot');
  failSnapshot = false;
  await intelligence.getByRole('button', { name: '刷新模型观测', exact: true }).click();
  await intelligence.locator('.model-observations').waitFor();
  await page.locator('.settings-content').evaluate((element) => element.scrollTop = 0);
  await page.screenshot({ path: `${outputDir}/forge-models-light.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('.settings-content').clientWidth >= innerWidth - 50);
  check(await page.locator('.settings-content').evaluate((element) => element.clientWidth >= 340), '390px model settings retain at least 340px of usable content width');
  await page.screenshot({ path: `${outputDir}/forge-models-narrow-dark.png`, fullPage: true });
  check(await page.locator('.settings-content').evaluate((element) => element.scrollWidth <= element.clientWidth), '390px model measurements and controls have no horizontal content overflow');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px settings preserve the overall shell bounds');
  check(missing.length === 0, `all requested endpoints have synthetic fixtures: ${missing.join(', ')}`);
  return { status: 'passed', fixture: 'synthetic HTTP only, no Provider', checks, calls: [...new Set(calls)], mutations: mutations.map((entry) => entry.path), screenshots: ['forge-triggers-light.png', 'forge-triggers-390-dark.png', 'forge-triggers-320-dark.png', 'forge-models-light.png', 'forge-models-narrow-dark.png'] };
}
