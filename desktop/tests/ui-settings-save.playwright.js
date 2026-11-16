// Synthetic HTTP + synthetic Tauri bridge only; never touches a credential vault or real provider.
// Control variants: settings-source.txt — zLIa compact input and hwkA md button, chosen for FORGE forms.
async (page, { outputDir }) => {
  const checks = [], mutations = [], confirms = [], missing = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const profile = {
    id: 'profile-save-fixture', display_name: 'Recorded model', provider: 'fixture-provider', model_id: 'fixture-model', enabled: true,
    context_window: 65536, max_output_tokens: 8192,
    tool_calling_support: { supported: true, source: 'provider_metadata' },
    structured_output_support: { supported: false, source: 'user_override' },
    capabilities: { research: 73 }, provenance: { source: 'provider_metadata' },
    capability_measurements: { research: { value: 73, source: 'forge_benchmark', n_samples: 7, observed_at: '2026-01-01T00:00:00Z' } },
    input_cost_per_million: 120000, output_cost_per_million: 350000, currency: 'USD',
  };
  let modelVersion = '2026-01-01T00:00:00Z';
  const config = {
    provider: { id: 'fixture-provider', kind: 'openai_compatible', base_url: 'https://provider.invalid/v1', credential_ref: 'FORGE_PROVIDER_KEY_SETTINGS_FIXTURE', supports_tools: true, supports_json_schema: false, allow_insecure_loopback: false },
    router_model_id: 'fixture-model', router_context_limit: 32768, model_token_limit: 34000, model_concurrency_limit: 3,
    network_hosts: ['source.invalid', 'reference.invalid'],
  };
  const settings = { revision: 3, config, context_limit: 32768, provider_configured: true, provider_id: 'fixture-provider', router_model_id: 'fixture-model',
    workspace_id: 'settings-save-fixture', workspace_root: 'D:/Synthetic/FORGE-settings-save', restore_fenced: false, environment_overrides: [], restart_required: false };
  const originalProfile = structuredClone(profile), originalConfig = structuredClone(config);
  const secret = 'synthetic-credential-settings-save-not-a-real-secret';
  await page.addInitScript(() => {
    window.isTauri = true;
    window.__fixtureNativeCalls = [];
    window.__TAURI_INTERNALS__ = { invoke: async (command, args = {}) => {
      window.__fixtureNativeCalls.push({ command, args });
      if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-native-bearer', workspaceId: 'settings-save-fixture', generation: 'settings-save-generation' };
      if (command === 'runtime_status') return { state: 'ready', generation: 'settings-save-generation', restartCount: 0, lastErrorCode: null };
      if (command === 'workspace_operation_status') throw new Error('OPERATION_NOT_FOUND');
      if (command === 'workspace_changes') return { files: [], repository: false };
      if (command === 'workspace_files_list') return [];
      if (command === 'credential_set') return { saved: true };
      if (command === 'credential_exists') return { exists: true };
      throw new Error(`unexpected fixture native command: ${command}`);
    } };
  });
  page.on('dialog', async (dialog) => { confirms.push(dialog.message()); await dialog.accept(); });
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    let body, status = 200;
    if (request.method() !== 'GET') {
      const payload = JSON.parse(request.postData() || '{}');
      mutations.push({ method: request.method(), path: url.pathname, payload });
      if (url.pathname === `/api/models/${profile.id}` && request.method() === 'PATCH') {
        if (payload.expected_updated_at !== modelVersion) { status = 409; body = { detail: 'fixture model version conflict' }; }
        else { const { expected_updated_at, ...changes } = payload; Object.assign(profile, changes); modelVersion = '2026-01-01T00:00:01Z'; body = { profile, updated_at: modelVersion }; }
      } else if (url.pathname === '/api/settings' && request.method() === 'POST') {
        if (payload.expected_revision !== settings.revision) { status = 409; body = { detail: 'fixture config revision conflict' }; }
        else { settings.config = payload.config; settings.revision += 1; settings.restart_required = true; body = settings; }
      } else { missing.push(`${request.method()} ${url.pathname}`); status = 404; body = { detail: 'unexpected fixture mutation' }; }
    } else if (url.pathname === '/api/conversation') body = { messages: [], next_before: null, has_more: false };
    else if (['/api/timeline', '/api/quests', '/api/approvals/pending', '/api/activity'].includes(url.pathname)) body = { items: [], next_before: null, has_more: false };
    else if (url.pathname === '/api/settings') body = settings;
    else if (url.pathname === '/api/restore-status') body = { fenced: false };
    else if (url.pathname === '/api/models') body = [profile];
    else if (url.pathname === '/api/projects/discovered') body = [];
    else if (url.pathname === '/api/runtime/health') body = { components: [] };
    else if (url.pathname === `/api/models/${profile.id}`) body = { profile, updated_at: modelVersion };
    else if (url.pathname === `/api/model-intelligence/${profile.id}`) body = { performance: { measurements: {}, source_refs: [] }, health: { recommendation: 'unknown' }, capabilities: { measurements: {}, source_refs: [] } };
    else if (url.pathname === '/api/budget') body = { token_limit: 34000, concurrency_limit: 3 };
    else if (url.pathname === '/api/memory') body = { items: [], cursor: null, omitted: 0, bytes: 0 };
    else { missing.push(url.pathname); status = 404; body = { detail: 'settings fixture endpoint missing' }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('http://127.0.0.1:5173/');
  await page.waitForFunction(() => document.querySelector('.current-project')?.textContent.includes('FORGE-settings-save'));
  await page.keyboard.press('Control+,');
  const nav = page.locator('.settings-nav');
  await nav.getByRole('button', { name: '模型库', exact: true }).click();
  await page.getByRole('button', { name: '编辑 / 启停', exact: true }).click();
  let editor = page.locator('.model-form');
  await editor.getByLabel('显示名称', { exact: true }).waitFor();
  const editorControlSize = await editor.getByLabel('显示名称', { exact: true }).evaluate((element) => {
    const style = getComputedStyle(element);
    return { font: parseFloat(style.fontSize), line: parseFloat(style.lineHeight), height: element.getBoundingClientRect().height };
  });
  check(editorControlSize.font === 13 && editorControlSize.line === 18 && Math.abs(editorControlSize.height - 28) <= 1, 'FORGE model editor selects native compact input sizing: 13px/18px and 28px high');
  const saveButtonSize = await editor.getByRole('button', { name: '保存', exact: true }).evaluate((element) => {
    const style = getComputedStyle(element);
    return { font: parseFloat(style.fontSize), line: parseFloat(style.lineHeight), height: element.getBoundingClientRect().height };
  });
  check(saveButtonSize.font === 13 && saveButtonSize.line === 18 && Math.abs(saveButtonSize.height - 28) <= 1, 'FORGE model save action selects native md control sizing: 13px and 28px high');
  check(await editor.locator('.model-options').getAttribute('open') === null, 'editing an existing model starts with protocol and capacity options folded');
  await editor.getByLabel('显示名称', { exact: true }).fill('Renamed recorded model');
  await editor.getByRole('button', { name: '保存', exact: true }).click();
  await editor.waitFor({ state: 'detached' });
  const patch = mutations.find((item) => item.path === `/api/models/${profile.id}`);
  check(patch?.method === 'PATCH' && patch.payload.expected_updated_at === '2026-01-01T00:00:00Z', 'editing the model retains the original PATCH version binding');
  check(Object.keys(patch.payload).sort().join('|') === 'display_name|expected_updated_at' && patch.payload.display_name === 'Renamed recorded model', 'renaming a model sends only the explicit change and its version');
  for (const key of ['context_window', 'max_output_tokens', 'tool_calling_support', 'structured_output_support', 'capabilities', 'capability_measurements', 'provenance', 'input_cost_per_million', 'output_cost_per_million']) {
    check(JSON.stringify(profile[key]) === JSON.stringify(originalProfile[key]), `saving a compact model form preserves recorded ${key}`);
  }
  await page.getByRole('button', { name: '编辑 / 启停', exact: true }).click();
  editor = page.locator('.model-form');
  await editor.locator('.model-options > summary').click();
  check(await editor.getByLabel('上下文窗口（未知可留空）', { exact: true }).inputValue() === '65536' && await editor.getByLabel('最大输出 Token（未知可留空）', { exact: true }).inputValue() === '8192', 'reopening the compact editor restores its original hidden capacity values');
  check(await editor.getByRole('combobox', { name: /^工具调用支持/ }).inputValue() === 'yes' && await editor.getByRole('combobox', { name: /^结构化输出支持/ }).inputValue() === 'no', 'reopening preserves both explicit true and explicit false protocol declarations');
  await editor.getByRole('button', { name: '取消', exact: true }).click();
  await nav.getByRole('button', { name: '连接', exact: true }).click();
  const keyInput = page.getByLabel('新凭据', { exact: true });
  await keyInput.waitFor();
  check(await page.locator('.credential-options').getAttribute('open') === null && await keyInput.getAttribute('type') === 'password', 'an existing connection exposes the password input while credential identifiers stay folded');
  check(await page.getByLabel('凭据引用', { exact: true }).inputValue() === originalConfig.provider.credential_ref, 'credential management uses the recorded reference rather than a new inferred identifier');
  await keyInput.fill(secret);
  await page.locator('.credential-form').getByRole('button', { name: '保存', exact: true }).click();
  await page.locator('.credential-form [role="status"]').filter({ hasText: '凭据已保存' }).waitFor();
  const credentialCalls = await page.evaluate(() => window.__fixtureNativeCalls.filter((call) => call.command === 'credential_set'));
  check(credentialCalls.length === 1 && credentialCalls[0].args.secret === secret && credentialCalls[0].args.providerId === originalConfig.provider.id && credentialCalls[0].args.credentialRef === originalConfig.provider.credential_ref, 'saving a synthetic key uses the separate native credential command with the original identity');
  check(await keyInput.inputValue() === '', 'the password field clears after the original credential action');
  check(mutations.length === 1, 'saving a credential never writes a Core configuration or model request');
  await nav.getByRole('button', { name: '环境', exact: true }).click();
  check(await page.locator('.operator-connection-options').getAttribute('open') === null && await page.locator('.operator-advanced').getAttribute('open') === null, 'an existing runtime configuration keeps technical connection and budget fields folded');
  await page.getByLabel('Router 模型 ID', { exact: true }).fill('fixture-router-updated');
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await page.getByText('运行配置已保存，重启 Core 后生效', { exact: true }).waitFor();
  const configWrite = mutations.find((item) => item.path === '/api/settings');
  check(configWrite?.method === 'POST' && configWrite.payload.expected_revision === 3, 'runtime configuration keeps its original expected_revision contract');
  check(confirms.length === 1 && confirms[0].includes('配置版本 3') && confirms[0].includes('重启 Core'), 'saving configuration retains the revision and restart confirmation');
  check(configWrite.payload.config.router_model_id === 'fixture-router-updated', 'the visible Router change reaches the original configuration payload');
  for (const key of ['provider', 'router_context_limit', 'model_token_limit', 'model_concurrency_limit', 'network_hosts']) {
    check(JSON.stringify(configWrite.payload.config[key]) === JSON.stringify(originalConfig[key]), `saving the primary configuration fields preserves hidden ${key}`);
  }
  check(!JSON.stringify(mutations).includes(secret) && Object.keys(configWrite.payload.config).sort().join('|') === Object.keys(originalConfig).sort().join('|'), 'the API key never enters configuration or model JSON');
  // The success notice confirms the POST; the revision arrives in the following panel refresh.
  await page.locator('.operator-revision').filter({ hasText: '配置版本 4' }).waitFor();
  check((await page.locator('.operator-revision').innerText()).includes('配置版本 4') && (await page.locator('.operator-revision').innerText()).includes('待重启'), 'saved configuration shows the new revision and pending restart state');
  check(missing.length === 0 && mutations.length === 2, `settings use only the two intended version-bound HTTP writes; missing=${missing.join(',')}, writes=${mutations.length}`);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `${outputDir}/simple-settings-save.png`, fullPage: true });
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  check(storage.local === 0 && storage.session === 0, 'settings and credential actions leave browser storage empty');
  return { status: 'passed', fixture: 'synthetic HTTP and Tauri bridge only', checks: checks.length, mutations: mutations.map(({ method, path }) => ({ method, path })), credentialCommands: credentialCalls.length, storage };
}
