// Synthetic Core notices only. No live completion, model calls or mutations.
async (page, { outputDir }) => {
  const checks = [], reads = [], writes = [], unknown = [];
  const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
  const quest = { id: 'private-task-id', title: '演示：检查报告', goal: '演示：查看已返回的报告', status: 'completed', version: 1 };
  const metadata = { source: 'router_notice', execution_mode: 'programmatic', notice_phase: 'completion_summary_unavailable', completion_execution_mode: 'live', quest_id: quest.id };
  const messages = [
    { id: 'user', role: 'user', content: '演示：整理这份报告。' },
    { id: 'fallback', role: 'assistant', content: '演示：任务已完成。结果较长，这里只通知完成，不概括内容。', metadata },
    { id: 'normal', role: 'assistant', content: '演示：正常交付正文。', metadata: { source: 'router_notice', quest_id: quest.id } },
    { id: 'unknown', role: 'assistant', content: '演示：其他工作空间的任务。', metadata: { ...metadata, quest_id: 'other-workspace-task' } },
    { id: 'mock', role: 'assistant', content: '演示：模拟完成消息。', metadata: { ...metadata, completion_execution_mode: 'mock' } },
    { id: 'missing', role: 'assistant', content: '演示：来源未确认。', metadata: { ...metadata, completion_execution_mode: undefined } },
  ].map((message, index) => ({ ...message, created_at: new Date(Date.now() - 60000 + index * 1000).toISOString() }));
  let known = true;
  await page.addInitScript(() => { window.isTauri = true; window.__TAURI_INTERNALS__ = { invoke: async command => {
    if (command === 'connection_get') return { state: 'ready', baseUrl: 'http://127.0.0.1:8765', bearer: 'synthetic-only', workspaceId: 'notice-demo', generation: 'notice-generation' };
    if (command === 'runtime_status') return { state: 'ready', generation: 'notice-generation' };
    throw Error('OPERATION_NOT_FOUND');
  } }; });
  await page.route('http://127.0.0.1:8765/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({status:204,headers});
    if (request.method() !== 'GET') { writes.push(path); return route.fulfill({status:405,headers,json:{}}); }
    reads.push(path); let json;
    if (path === '/health') json = {session:'notice-generation'};
    else if (path === '/api/conversation') json = {messages,router:{},has_more:false};
    else if (path === '/api/quests') json = {items:known ? [quest] : [],has_more:false};
    else if (path === `/api/quests/${quest.id}`) json = {quest,workers:[]};
    else if (path.endsWith('/plan')) json = {plan_revision:0,status:'draft',nodes:[]};
    else if (['/plan/history','/grants','/teams'].some(suffix=>path.endsWith(suffix))) json = [];
    else if (path === '/api/conversations/main/subagents') json = {conversation_id:'main',workspace_id:'notice-demo',items:[],has_more:false,next_before:null};
    else if (['/api/timeline','/api/activity','/api/approvals','/api/approvals/pending'].includes(path)) json = {items:[],has_more:false};
    else if (path === '/api/settings') json = {revision:1,config:{},workspace_id:'notice-demo',workspace_root:'C:/演示',provider_configured:true};
    else if (path === '/api/restore-status') json = {fenced:false};
    else if (path === '/api/runtime/health') json = {components:[]};
    else if (['/api/projects/discovered','/api/models'].includes(path)) json = [];
    else if (path === '/api/budget') json = {token_limit:10000,concurrency_limit:2};
    else { unknown.push(path); return route.fulfill({status:404,headers,json:{}}); }
    return route.fulfill({status:200,headers,json});
  });
  await page.setViewportSize({width:1000,height:900});
  await page.goto(process.env.FORGE_TEST_URL || 'http://127.0.0.1:5196/');
  const message = id => page.locator(`[data-scroll-key="message:${id}"]`);
  const action = message('fallback').getByRole('button',{name:'查看任务',exact:true});
  await action.waitFor();
  check(await page.locator('.conversation-list').getByRole('button',{name:'查看任务',exact:true}).count() === 1, 'only exact trusted fallback receives one contextual task action');
  for (const id of ['normal','unknown','mock','missing']) check(await message(id).getByRole('button',{name:'查看任务',exact:true}).count() === 0, `${id} notice stays plain`);
  check(!(await message('fallback').innerText()).includes(quest.id), 'notice and action display no task IDs');
  check(await action.evaluate(button => button.getBoundingClientRect().top >= button.previousElementSibling.getBoundingClientRect().bottom), 'context action follows its reply rather than preceding it');
  await page.screenshot({path:`${outputDir}/completion-task-link-demo.png`});
  await page.setViewportSize({width:390,height:844});
  check(await action.isVisible() && await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px action remains visible without horizontal overflow');
  await page.screenshot({path:`${outputDir}/completion-task-link-390-demo.png`});
  await page.getByLabel('消息内容',{exact:true}).fill('演示草稿');
  const planRead = page.waitForResponse(response => new URL(response.url()).pathname === `/api/quests/${quest.id}/plan`);
  await action.click();
  await planRead;
  await page.locator('.workspace-panel').waitFor();
  await page.waitForFunction(() => document.querySelector('.workspace-panel')?.textContent.includes('演示：检查报告'));
  check(reads.includes(`/api/quests/${quest.id}/plan`), 'context action opens exact existing task view');
  await page.getByRole('button',{name:'关闭任务详情',exact:true}).click();
  check(await page.getByLabel('消息内容',{exact:true}).inputValue() === '演示草稿', 'task navigation preserves draft');
  known = false;
  await page.reload();
  await message('fallback').waitFor();
  check(await message('fallback').getByRole('button',{name:'查看任务',exact:true}).count() === 0, 'same notice loses action when task is not known in current workspace');
  check(writes.length === 0 && unknown.length === 0, 'navigation uses existing read-only API with no extra routes or mutations');
  return {checks,reads};
}
