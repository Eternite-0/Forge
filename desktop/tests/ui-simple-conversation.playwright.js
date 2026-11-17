// Synthetic read-only Core receipts, rendered through the real frontend.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [], nativeReads = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const scope = { conversation_id: 'main', workspace_id: 'artifact-space', project_id: 'artifact-project' };
  const stamp = new Date(Date.now() - 60000).toISOString(), sha = 'a'.repeat(64);
  const run = id => ({ run_id: id, worker_id: `worker-${id}`, quest_id: 'artifact-quest', role: "报告整理（演示）", task_title: "整理演示周报", task_objective: "核对演示周报的内容与格式。", state: 'succeeded', execution_mode: 'mock', lease_status: 'none', delegation_state: 'authorized', parent_message_id: 'artifact-user', source: { kind: 'message', id: 'artifact-user' }, outcome: { kind: 'verified', label: '' }, milestones: [], model_id: '模拟模型', created_at: stamp });
  const item = id => ({ receipt_id: `receipt-${id}`, path: `docs/${id}.md`, sha256: sha, bytes: 40, created_at: stamp, execution_mode: 'mock', change: 'revised' });
  let execution = true, needsApproval = false;
  let listMode = 'valid', mismatch = '', delayList = false, delayDiff = false;
  const releases = [];
  const listCount = () => reads.filter(path => path.endsWith('/artifacts')).length;
  const diffCount = () => reads.filter(path => path.endsWith('/diff')).length;
  await page.addInitScript(() => { window.isTauri = true; window.__artifactNativeCalls = []; window.__TAURI_INTERNALS__ = { invoke: async command => {
    window.__artifactNativeCalls.push(command);
    if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-artifacts', workspaceId: 'artifact-space', generation: 'artifact-gen' };
    if (command === 'runtime_status') return { state: 'ready', generation: 'artifact-gen' };
    throw Error('OPERATION_NOT_FOUND');
  } }; });
    await page.route('http://127.0.0.1:8765/**', async route => {
    const r = route.request(), u = new URL(r.url()), headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (r.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    if (r.method() !== 'GET') { writes.push(u.pathname); return route.fulfill({ status: 405, headers, json: {} }); }
    reads.push(u.pathname); let json, status = 200, delayed = false;
    if (u.pathname === '/api/conversations/main/subagents') json = { ...scope, phase: u.searchParams.get('phase'), items: !execution || u.searchParams.get('phase') === 'active' ? [] : [run('A')], has_more: false, next_before: null };
    else if (/\/subagents\/[AB]\/artifacts$/.test(u.pathname)) {
      const id = u.pathname.split('/').at(-2);
      json = { ...scope, run_id: id, execution_mode: 'mock', provenance: 'historical_receipts', current_files_verified: false, items: listMode === 'empty' || listMode === 'unavailable' ? [] : [item(id)], has_more: false, next_before: null, unavailable: listMode === 'unavailable' };
      if (listMode === 'wrongscope') json.workspace_id = 'other-workspace';
      if (listMode === 'malformed') json.items[0].bytes = '40';
      if (id === 'A' && delayList) { delayed = true; await new Promise(resolve => releases.push(resolve)); }
    } else if (/\/subagents\/[AB]$/.test(u.pathname)) json = { ...scope, item: run(u.pathname.split('/').pop()) };
    else if (/\/api\/artifacts\/receipts\/receipt-[AB]\/diff$/.test(u.pathname)) {
      const id = u.pathname.split('/').at(-2).slice(-1);
      json = { receipt_id: `receipt-${id}`, base_receipt_id: `base-${id}`, quest_id: 'artifact-quest', run_id: id, path: `docs/${id}.md`, execution_mode: 'mock', change: 'revised', created_at: stamp, sha256: sha, base_sha256: 'b'.repeat(64), added: 1, removed: 1, text: `--- a/docs/${id}.md\n+++ b/docs/${id}.md\n@@ -1 +1 @@\n-OLD ${id}\n+HISTORICAL ${id} ONLY\n`, truncated: false, origin: null };
      if (mismatch) json[mismatch] = { receipt_id: 'wrong-receipt', run_id: 'other-run', path: 'wrong/path.md', sha256: 'c'.repeat(64), execution_mode: 'live' }[mismatch];
      if (id === 'A' && delayDiff) { delayed = true; await new Promise(resolve => releases.push(resolve)); }
    } else if (u.pathname === '/health') json = { session: 'artifact-gen' };
    else if (u.pathname === '/api/conversation') json = { messages: [{ id: 'artifact-user', role: 'user', content: '帮我整理一下周报，给我一份简洁总结。（演示）', created_at: stamp }, { id: 'artifact-notice', role: 'assistant', content: '内部验证与执行日志演示', created_at: new Date(Date.parse(stamp) + 5000).toISOString(), metadata: { source: 'router_notice' } }, { id: 'artifact-answer', role: 'assistant', content: '周报已整理好。重点是本周完成事项、尚待解决的问题和下周计划。\n\n你希望我把哪一部分展开？（演示内容）', created_at: new Date(Date.parse(stamp) + 10000).toISOString(), metadata: { execution_mode: 'mock' } }], router: {}, has_more: false };
    else if (u.pathname === '/api/settings') json = { revision: 1, config: {}, workspace_id: scope.workspace_id, workspace_root: 'C:/演示工作空间', provider_configured: true, router_model_id: '模拟展示' };
    else if (u.pathname === '/api/approvals/pending' && needsApproval) json = { items: [{ id: 'demo-approval', quest_id: 'artifact-quest', action_type: 'publish', risk: 'R2', resource_scope: '演示：发送周报前需要确认', plan_revision: 1, parameter_digest: 'a'.repeat(64), status: 'PENDING', reason: '演示审批，不执行发送', expires_at: '2099-12-01T00:00:00Z' }], has_more: false };
    else if (['/api/quests', '/api/timeline', '/api/approvals/pending', '/api/activity'].includes(u.pathname)) json = { items: [], has_more: false };
    else if (u.pathname === '/api/restore-status') json = { fenced: false };
    else if (u.pathname === '/api/runtime/health') json = { components: [] };
    else if (['/api/projects/discovered', '/api/models'].includes(u.pathname)) json = [];
    else if (u.pathname === '/api/budget') json = { token_limit: 10000, concurrency_limit: 2 };
    else { unknown.push(u.pathname); status = 404; json = {}; }
    if (u.pathname === '/api/conversation') {
      json.messages.find(message => message.id === 'artifact-notice').metadata.notice_phase = 'review_pending';
      json.messages.find(message => message.id === 'artifact-answer').metadata.source = 'router_notice';
    }
    try { await route.fulfill({ status, headers, json }); } catch (error) { if (!delayed) throw error; }
  });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const base = process.env.FORGE_TEST_URL || 'http://127.0.0.1:5193/';
  await page.goto(base);
  const door = page.getByRole('button',{name:'执行详情',exact:true});
  const draft = page.getByLabel('消息内容', { exact: true });
  await door.waitFor();
  await page.getByText('你希望我把哪一部分展开？（演示内容）', { exact: false }).waitFor();
  check(await door.count() === 1 && await page.locator('.execution-screen').count() === 0 && await page.locator('.conversation-execution-details').count() === 0, 'one execution navigation entry, no inline execution accordion');
  check(!await page.locator('.conversation-agents').isVisible() && !await page.locator('.task-progress-list').isVisible(), 'agent management and background tasks are hidden from default chat');
  check(!await page.getByText('内部验证与执行日志演示', {exact:true}).isVisible(), 'marked technical notices do not clutter main messages');
  check(await page.locator('.conversation-context').count() === 0 && await page.getByRole('button', {name:'查看任务', exact:true}).count() === 0, 'default chat has no context console or task management button');
  check(await page.locator('.composer-approvals').count() === 0, 'no empty approval control competes with ordinary composition');
  await page.screenshot({ path: `${outputDir}/execution-main-chat-demo.png` });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.screenshot({ path: `${outputDir}/simple-chat-light-demo.png` });
  await page.emulateMedia({ colorScheme: 'dark' });
  await draft.fill('保留这个演示草稿');
  const chatScroll = await page.locator('.timeline').evaluate(e=>e.scrollTop);
  await door.click();
  await page.locator('.execution-screen').waitFor();
  check(!await draft.isVisible() && !await page.locator('.conversation-shell').isVisible(), 'execution replaces the chat content area rather than opening inline');
  check(await page.getByRole('dialog').count() === 0 && await page.locator('.execution-screen .conversation-agent-row').count() === 1, 'dedicated execution page shows this conversation scoped run list without modal');
  await page.screenshot({path:`${outputDir}/execution-list-demo.png`});
  await page.locator('.conversation-agent-row').click();
  const panel = page.locator('.conversation-agent-detail');
  await panel.getByText('核对演示周报的内容与格式。', {exact:true}).waitFor();
  check(!await panel.getByText('里程碑', {exact:true}).isVisible() && !await panel.getByText('模型', {exact:true}).isVisible() && !await panel.getByText('结果记录', {exact:true}).isVisible(), 'child detail initially shows responsibility/status, not logs/model/verification');
  check(await panel.getByRole('button', {name:/查看返回文件/}).isVisible(), 'real output navigation remains discoverable');
  check(await panel.evaluate(e=>getComputedStyle(e).position==='static') && !await page.locator('.execution-screen-content').isVisible(), 'selected run fills same execution screen without nested drawer');
  await page.screenshot({ path: `${outputDir}/execution-child-detail-demo.png` });
  await panel.locator('.conversation-agent-technical > summary').click();
  check(await panel.getByText('模型', {exact:true}).isVisible() && await panel.getByText('里程碑', {exact:true}).isVisible(), 'technical records remain explicitly inspectable');
  await panel.locator('.conversation-agent-technical > summary').click();
  await panel.getByRole('button', {name:/查看返回文件/}).click();
  await panel.getByRole('button', {name:/docs\/A.md/}).click();
  await panel.getByText('HISTORICAL A ONLY', {exact:false}).waitFor();
  check(reads.includes('/api/artifacts/receipts/receipt-A/diff'), 'output still opens exact historical receipt');
  await page.keyboard.press('Escape');
  await panel.locator('.artifact-history-preview').waitFor({state:'hidden'});
  await page.waitForFunction(() => document.activeElement?.textContent.includes('docs/A.md'));
  await page.keyboard.press('Escape');
  await panel.waitFor({state:'hidden'});
  await page.waitForFunction(() => document.activeElement?.matches('.conversation-agent-row'));
  check(await page.locator('.conversation-agent-row').evaluate(e=>e===document.activeElement), 'detail back restores selected run focus in execution list');
  await page.getByRole('button',{name:'返回对话',exact:false}).click();
  check(await draft.inputValue() === '保留这个演示草稿' && await door.evaluate(e=>e===document.activeElement), 'main back preserves draft and restores entry focus');
  check(Math.abs(await page.locator('.timeline').evaluate(e=>e.scrollTop)-chatScroll)<2,'return restores chat reading position');
  await door.click();
  await page.locator('.conversation-entry').click();
  await draft.waitFor();
  check(await page.locator('.execution-screen').count() === 0 && await draft.inputValue() === '保留这个演示草稿','existing sidebar conversation navigation also exits execution without losing draft');
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:`${outputDir}/execution-chat-390-demo.png`});
  await door.click();
  await page.locator('.conversation-agent-row').click();
  await panel.getByText('核对演示周报的内容与格式。', {exact:true}).waitFor();
  check(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth), 'compact child detail fits390px');
  check(await page.getByRole('dialog').count() === 0 && await page.locator('.rail').evaluate(e=>!e.inert), 'narrow full-page detail is normal navigation, not modal or inert sidebar');
  await page.screenshot({path:`${outputDir}/execution-child-390-demo.png`});
  await page.keyboard.press('Escape');
  execution = false; needsApproval = true;
  await page.reload();
  await page.getByRole('button', {name:'批准',exact:true}).waitFor();
  check(await page.getByRole('button', {name:'批准',exact:true}).isEnabled(), 'genuinely pending approval remains visible and actionable');
  check(await door.count() === 0, 'ordinary conversation does not invent execution UI');
  check(writes.length === 0 && unknown.length === 0, 'all checks use existing read-only routes; no approval submitted');
  return { checks, reads };
}
